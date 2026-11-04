import pytest

from config import AgentSettings


@pytest.mark.parametrize("raw, expected", [("", False), ("  ", False), ("0", False), ("1", True), ("true", True), ("YES", True)])
def test_llm_fake_env_values(monkeypatch, raw, expected):
    monkeypatch.setenv("LLM_FAKE", raw)
    assert AgentSettings().llm_fake is expected


def test_llm_fake_unset_is_false(monkeypatch):
    monkeypatch.delenv("LLM_FAKE", raising=False)
    assert AgentSettings().llm_fake is False
