"""API-level tests for the DP-04-05 adjudication router.

End-to-end over FastAPI TestClient: proves the admin endpoints expose the
prioritised queue and evidence view, that decisions write through the
shared contract, that double review is enforced for high-impact merges
over HTTP, and that reversals requeue the case.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from irc_data.matching import adjudication as adj


@pytest.fixture()
def client(monkeypatch):
    from fastapi.testclient import TestClient

    from irc_data.api import app as app_module
    from irc_data.api.routers import adjudication as adj_router
    from irc_data.api.routers import admin as admin_module

    monkeypatch.setattr(admin_module, "ADMIN_PASSWORD", "test-secret")
    adj_router.set_queue(
        adj.AdjudicationQueue(
            clock=lambda: datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)
        )
    )
    try:
        yield TestClient(app_module.app)
    finally:
        adj_router.set_queue(None)


def _auth(as_user: str | None = None) -> dict[str, str]:
    return {"Authorization": "Bearer test-secret"}


def _candidate_payload(
    left: str,
    right: str,
    *,
    score: float,
    impact_flags: list[str] | None = None,
) -> dict:
    return {
        "left_id": left,
        "right_id": right,
        "rules_fired": ["R01", "R05"],
        "matching_keys": ["R01:AUS4343", "R05:WILD OATS XI"],
        "ruleset_id": "blocking-rules-v1",
        "score": score,
        "score_explanation": ["sail token +0.40", "name +0.22"],
        "impact_flags": impact_flags or [],
        "left_evidence": {"sail_number": "AUS4343", "name": "Wild Oats XI", "source": "irc"},
        "right_evidence": {"sail_number": "4343", "name": "WILD OATS XI", "source": "orc"},
    }


def test_requires_admin_auth(client):
    assert client.get("/v1/admin/adjudication/queue").status_code == 401
    assert client.get("/v1/admin/adjudication/cases").status_code == 401
    assert client.get("/v1/admin/adjudication/resolutions").status_code == 401
    assert (
        client.post(
            "/v1/admin/adjudication/decide",
            json={"case_id": "adj-x", "decision": "merge", "decided_by": "human:x"},
        ).status_code
        == 401
    )


def test_enqueue_uncertain_candidate_and_read_queue(client):
    resp = client.post(
        "/v1/admin/adjudication/enqueue",
        json=_candidate_payload("obs-a", "obs-b", score=0.62),
        headers=_auth(),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["queued"] is True
    case_id = body["item"]["case_id"]

    queue = client.get("/v1/admin/adjudication/queue", headers=_auth()).json()
    assert [i["case_id"] for i in queue] == [case_id]
    item = queue[0]
    # the evidence view: side-by-side evidence, explanation, impact, actions
    assert item["left_evidence"]["sail_number"] == "AUS4343"
    assert item["right_evidence"]["sail_number"] == "4343"
    assert item["score_explanation"] == ["sail token +0.40", "name +0.22"]
    assert item["actions"] == ["merge", "separate", "escalate", "defer"]


def test_enqueue_confident_candidate_stays_with_auto_resolver(client):
    resp = client.post(
        "/v1/admin/adjudication/enqueue",
        json=_candidate_payload("obs-a", "obs-b", score=0.99),
        headers=_auth(),
    )
    assert resp.json() == {"queued": False, "routing": "auto_merge"}
    assert client.get("/v1/admin/adjudication/queue", headers=_auth()).json() == []


def test_queue_is_prioritised_high_impact_first(client):
    client.post(
        "/v1/admin/adjudication/enqueue",
        json=_candidate_payload("l1", "l2", score=0.50),
        headers=_auth(),
    )
    hi = client.post(
        "/v1/admin/adjudication/enqueue",
        json=_candidate_payload("h1", "h2", score=0.95, impact_flags=["rated"]),
        headers=_auth(),
    ).json()["item"]
    queue = client.get("/v1/admin/adjudication/queue", headers=_auth()).json()
    assert queue[0]["case_id"] == hi["case_id"]
    assert queue[0]["requires_second_review"] is True


def test_decide_applies_through_shared_contract(client):
    case_id = client.post(
        "/v1/admin/adjudication/enqueue",
        json=_candidate_payload("a", "b", score=0.62),
        headers=_auth(),
    ).json()["item"]["case_id"]
    resp = client.post(
        "/v1/admin/adjudication/decide",
        json={"case_id": case_id, "decision": "separate", "decided_by": "human:stu"},
        headers=_auth(),
    )
    assert resp.status_code == 200
    record = resp.json()
    assert record["status"] == "applied"
    assert record["decision"] == "separate"
    assert record["decided_by"] == "human:stu"
    assert record["reversible"] is True
    # case closed
    assert client.get("/v1/admin/adjudication/queue", headers=_auth()).json() == []


def test_double_review_enforced_over_http(client):
    case_id = client.post(
        "/v1/admin/adjudication/enqueue",
        json=_candidate_payload("a", "b", score=0.97, impact_flags=["rated"]),
        headers=_auth(),
    ).json()["item"]["case_id"]

    first = client.post(
        "/v1/admin/adjudication/decide",
        json={"case_id": case_id, "decision": "merge", "decided_by": "human:alice"},
        headers=_auth(),
    )
    assert first.json()["status"] == "pending_second_review"

    same = client.post(
        "/v1/admin/adjudication/decide",
        json={"case_id": case_id, "decision": "merge", "decided_by": "human:alice"},
        headers=_auth(),
    )
    assert same.status_code == 409

    second = client.post(
        "/v1/admin/adjudication/decide",
        json={"case_id": case_id, "decision": "merge", "decided_by": "human:bob"},
        headers=_auth(),
    )
    assert second.status_code == 200
    assert second.json()["status"] == "applied"
    assert second.json()["decided_by_chain"] == ["human:alice", "human:bob"]


def test_reverse_requeues_case_over_http(client):
    case_id = client.post(
        "/v1/admin/adjudication/enqueue",
        json=_candidate_payload("a", "b", score=0.62),
        headers=_auth(),
    ).json()["item"]["case_id"]
    rec = client.post(
        "/v1/admin/adjudication/decide",
        json={"case_id": case_id, "decision": "merge", "decided_by": "human:alice"},
        headers=_auth(),
    ).json()
    undo = client.post(
        "/v1/admin/adjudication/reverse",
        json={
            "resolution_id": rec["resolution_id"],
            "decided_by": "human:bob",
            "rationale": "wrong hull",
        },
        headers=_auth(),
    )
    assert undo.status_code == 200
    assert undo.json()["undo_of"] == rec["resolution_id"]
    queue = client.get("/v1/admin/adjudication/queue", headers=_auth()).json()
    assert [i["case_id"] for i in queue] == [case_id]


def test_case_detail_includes_resolution_trail(client):
    case_id = client.post(
        "/v1/admin/adjudication/enqueue",
        json=_candidate_payload("a", "b", score=0.62),
        headers=_auth(),
    ).json()["item"]["case_id"]
    client.post(
        "/v1/admin/adjudication/decide",
        json={"case_id": case_id, "decision": "separate", "decided_by": "human:stu"},
        headers=_auth(),
    )
    detail = client.get(
        f"/v1/admin/adjudication/cases/{case_id}", headers=_auth()
    ).json()
    assert detail["status"] == "applied"
    assert len(detail["resolutions"]) == 1
    assert detail["resolutions"][0]["decision"] == "separate"


def test_unknown_case_404(client):
    resp = client.post(
        "/v1/admin/adjudication/decide",
        json={"case_id": "adj-nope", "decision": "merge", "decided_by": "human:x"},
        headers=_auth(),
    )
    assert resp.status_code == 404
    assert client.get("/v1/admin/adjudication/cases/adj-nope", headers=_auth()).status_code == 404


def test_invalid_candidate_422(client):
    payload = _candidate_payload("a", "b", score=0.5)
    payload["rules_fired"] = []
    resp = client.post(
        "/v1/admin/adjudication/enqueue", json=payload, headers=_auth()
    )
    assert resp.status_code == 422
