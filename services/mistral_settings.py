"""Small, explicit Mistral overrides. No network access or per-user configuration."""

import os

try:
    from .model_catalogue import valid_id
except ImportError:
    from model_catalogue import valid_id

GMAIL_KEYS = (
    "MISTRAL_GMAIL_MODEL",
    "MISTRAL_GMAIL_DRAFT_MODEL",
    "MISTRAL_GMAIL_DRAFT_REASONING_EFFORT",
)
REASONING_MODELS = {
    **{
        model: ("low", "medium", "high")
        for model in ("mistral-large-4", "mistral-large-4-0")
    },
    **{
        model: ("none", "high")
        for model in (
            "mistral-small-latest",
            "mistral-small-2603",
            "mistral-medium-latest",
            "mistral-medium-3-5",
            "mistral-medium-2604",
        )
    },
}


class SettingsError(ValueError):
    pass


def reasoning_options(model):
    # Deliberately explicit: an API-wide enum does not mean every model accepts it.
    return REASONING_MODELS.get(model, ())


def resolve(env=None, *, service="general", draft=False):
    """Task > service > global model. Blank effort leaves the provider default."""
    env = os.environ if env is None else env
    if service not in {"general", "gmail"} or (draft and service != "gmail"):
        raise SettingsError("invalid_mistral_task")
    model = env.get("MISTRAL_MODEL", "").strip() or "mistral-small-latest"
    effort = ""
    if service == "gmail":
        model = env.get(GMAIL_KEYS[0], "").strip() or model
        if draft:
            model = env.get(GMAIL_KEYS[1], "").strip() or model
            effort = env.get(GMAIL_KEYS[2], "").strip()
    if not valid_id(model):
        raise SettingsError("invalid_mistral_model")
    if effort and effort not in reasoning_options(model):
        raise SettingsError("invalid_gmail_draft_reasoning_effort")
    return {
        "key": env.get("MISTRAL_KEY", ""),
        "model": model,
        "reasoning_effort": effort,
    }
