"""Shape tests for AnalystAnswerV1 / Claim / EvidenceRef (AI-01-03)."""
import pytest
from pydantic import ValidationError

from irc_data.api.schemas.analyst import AnalystAnswerV1, Claim, EvidenceRef


def test_minimal_answer_defaults():
    a = AnalystAnswerV1(question="q")
    assert a.claims == [] and a.caveats == [] and a.confidence == {}
    assert a.insufficient_data is False


def test_question_is_required():
    with pytest.raises(ValidationError):
        AnalystAnswerV1()


def test_evidence_kinds_accepted():
    for kind in ["cert_field", "regression", "results_set", "polar", "rule_note", "knowledge"]:
        e = EvidenceRef(kind=kind, ref="x")
        assert e.kind == kind and e.version is None


def test_unknown_evidence_kind_rejected():
    with pytest.raises(ValidationError):
        EvidenceRef(kind="vibes", ref="x")


def test_claim_optional_fields_and_text_required():
    c = Claim(text="hello")
    assert c.value is None and c.unit is None and c.evidence == []
    with pytest.raises(ValidationError):
        Claim()


def test_full_answer_json_roundtrip():
    a = AnalystAnswerV1(
        question="q",
        claims=[Claim(text="TCC 1.025", value=1.025, unit="tcc",
                      evidence=[EvidenceRef(kind="cert_field", ref="tcc", version="2024")])],
        caveats=["c"],
        confidence={"level": "high"},
        insufficient_data=False,
    )
    assert AnalystAnswerV1.model_validate_json(a.model_dump_json()) == a
