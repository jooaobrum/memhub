from __future__ import annotations

from datetime import datetime, timezone

import pytest

from memhub.signals import detect_signals, is_correction
from memhub.sources.base import Interaction


def _msg(content, role="user", **metadata):
    return Interaction("t", "u", "w", "m1", role, content, datetime(2026, 1, 1, tzinfo=timezone.utc), "tr", metadata)


@pytest.mark.parametrize("text", [
    "Não, eu moro em Lyon", "na verdade eu prefiro respostas curtas", "isso está errado", "Você errou o prazo",
    "Não é o passaporte, mas sim o titre de séjour", "No, that's wrong", "Actually, I live in Paris",
    "that's not right", "It's not Tuesday but Wednesday", "you are wrong about the deadline",
])
def test_corrections_are_detected(text):
    assert is_correction(text)


@pytest.mark.parametrize("text", [
    "Não sei como fazer isso", "Não, obrigado", "No problem, thanks", "I do not know but I can check",
    "Qual o prazo para o visto?", "Actually useful answer, thanks",
])
def test_ordinary_text_is_not_a_correction(text):
    assert not is_correction(text)


def test_detect_signals_returns_message_id_and_detail():
    got = detect_signals([_msg("Olá"), _msg("Na verdade é outra coisa")])
    assert got == [{"kind": "correction", "message_id": "m1", "detail": "Na verdade"}]


def test_assistant_text_is_never_a_correction():
    assert detect_signals([_msg("that's wrong", role="assistant")]) == []


def test_error_metadata_becomes_an_error_signal():
    got = detect_signals([_msg("sorry", role="assistant", error="timeout"), _msg("ok", role="assistant", error="")])
    assert [(s["kind"], s["detail"]) for s in got] == [("error", "timeout")]


def test_llm_fallback_is_off_by_default_and_only_used_on_regex_misses():
    assert detect_signals([_msg("hmm, I meant something else")]) == []
    calls = []

    def llm(text):
        calls.append(text)
        return True

    got = detect_signals([_msg("hmm, I meant something else"), _msg("Não, isso não")], llm_check=llm)
    assert [s["detail"] for s in got] == ["llm", "Não, isso"]
    assert calls == ["hmm, I meant something else"]


def test_llm_check_treats_a_malformed_judge_reply_as_no_correction():
    from langchain_core.exceptions import OutputParserException
    from pydantic import ValidationError

    from memhub.signals import _IsCorrection, make_llm_check

    def broken(exc):
        class R:
            def invoke(self, _messages):
                raise exc
        class LLM:
            def with_structured_output(self, _schema):
                return R()
        return LLM()

    try:
        _IsCorrection.model_validate({})  # what a truncated reply '{"' ends up as
    except ValidationError as e:
        validation_error = e
    for exc in (validation_error, OutputParserException("truncated")):
        check = make_llm_check(broken(exc))
        assert check("hmm, I meant something else") is False
        assert detect_signals([_msg("hmm, I meant something else")], llm_check=check) == []


def test_llm_check_returns_the_judges_verdict_when_the_reply_is_well_formed():
    from memhub.signals import _IsCorrection, make_llm_check

    class R:
        def __init__(self, v):
            self.v = v
        def invoke(self, _messages):
            return _IsCorrection(is_correction=self.v)

    class LLM:
        def __init__(self, v):
            self.v = v
        def with_structured_output(self, _schema):
            return R(self.v)

    assert make_llm_check(LLM(True))("x") is True and make_llm_check(LLM(False))("x") is False
