"""Strict grounding validator: adversarial fixtures + strict-mode behaviour (AI-01-03)."""
import json
import logging
from pathlib import Path

import pytest

from irc_data.api.schemas.analyst import AnalystAnswerV1, Claim
from irc_data.api.services.report import gemini_client
from irc_data.api.services.report.grounding import (
    GroundingError,
    build_allowlist,
    validate_grounded,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "grounding"
FIXTURES = sorted(FIXTURE_DIR.glob("*.json"))
FACTS = {"tcc": 1.025, "lwl_m": 9.6, "displacement_kg": 3250}


# Collection-time guard on the corpus: 20 fixtures, 15 rejected, 5 accepted.
_EXPECTS = [json.loads(_p.read_text())["expect"] for _p in FIXTURES]
assert len(_EXPECTS) == 20, len(_EXPECTS)
assert _EXPECTS.count("reject") == 15 and _EXPECTS.count("accept") == 5


@pytest.mark.parametrize("path", FIXTURES, ids=[p.stem for p in FIXTURES])
def test_fixture(path):
    data = json.loads(path.read_text())
    answer = AnalystAnswerV1.model_validate(data["answer"])
    allow = build_allowlist(data["facts"])
    if data["expect"] == "reject":
        with pytest.raises(GroundingError) as ei:
            validate_grounded(answer, allow)
        assert ei.value.ungrounded_tokens or ei.value.claims_without_evidence
    else:
        assert validate_grounded(answer, allow) is None


def test_strict_mode_raises():
    with pytest.raises(GroundingError) as ei:
        gemini_client.audit_section_numbers(
            "TCC is 1.031 and weight 3410.", FACTS, section_id="t", strict=True)
    assert set(ei.value.ungrounded_tokens) == {"1.031", "3410"}


def test_non_strict_still_logs(caplog):
    with caplog.at_level(logging.WARNING):
        res = gemini_client.audit_section_numbers(
            "TCC is 1.031.", FACTS, section_id="t")
    assert res["suspicious"] == ["1.031"]
    assert "Suspicious numeric tokens" in caplog.text


def test_empty_evidence_claim_rejected():
    a = AnalystAnswerV1(question="q", claims=[Claim(text="TCC 1.025", value=1.025)])
    with pytest.raises(GroundingError) as ei:
        validate_grounded(a, build_allowlist(FACTS))
    assert ei.value.claims_without_evidence == [0]


def test_insufficient_data_answer_with_no_claims_accepted():
    a = AnalystAnswerV1(question="q", insufficient_data=True, caveats=["no data"])
    assert validate_grounded(a, build_allowlist(FACTS)) is None
