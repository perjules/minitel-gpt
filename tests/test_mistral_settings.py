"""Model overrides use synthetic settings; no paid API calls."""

import pytest

from services import mistral_settings as settings


def test_inheritance_and_general_tasks_ignore_gmail_overrides():
    env = {"MISTRAL_KEY": "synthetic-key", "MISTRAL_MODEL": "general",
           "MISTRAL_GMAIL_MODEL": "mail", "MISTRAL_GMAIL_DRAFT_MODEL": "mistral-large-4-0",
           "MISTRAL_GMAIL_DRAFT_REASONING_EFFORT": "medium"}
    assert settings.resolve(env)["model"] == "general"
    assert settings.resolve(env)["reasoning_effort"] == ""
    assert settings.resolve(env, service="gmail")["model"] == "mail"
    assert settings.resolve(env, service="gmail", draft=True) == {
        "key": "synthetic-key", "model": "mistral-large-4-0", "reasoning_effort": "medium"}
    env.update(MISTRAL_GMAIL_DRAFT_MODEL="", MISTRAL_GMAIL_DRAFT_REASONING_EFFORT="")
    assert settings.resolve(env, service="gmail", draft=True)["model"] == "mail"
    env["MISTRAL_GMAIL_MODEL"] = ""
    assert settings.resolve(env, service="gmail", draft=True)["model"] == "general"
    assert settings.resolve({})["model"] == "mistral-small-latest"


@pytest.mark.parametrize("model,effort,valid", [
    ("mistral-large-4-0", "medium", True), ("mistral-large-4", "none", False),
    ("mistral-small-2603", "high", True), ("mistral-small-latest", "medium", False),
    ("mistral-medium-3-5", "none", True), ("mistral-large-2512", "high", False),
    ("custom-model", "", True), ("custom-model", "high", False),
    ("bad\nmodel", "", False),
])
def test_reasoning_is_validated_for_the_effective_model(model, effort, valid):
    env = {"MISTRAL_GMAIL_DRAFT_MODEL": model, "MISTRAL_GMAIL_DRAFT_REASONING_EFFORT": effort}
    if valid:
        assert settings.resolve(env, service="gmail", draft=True)["reasoning_effort"] == effort
    else:
        with pytest.raises(settings.SettingsError):
            settings.resolve(env, service="gmail", draft=True)
