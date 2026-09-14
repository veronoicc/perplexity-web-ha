"""Constants for the Perplexity Web integration."""

DOMAIN = "perplexity_web"

CONF_SESSION_TOKEN = "session_token"
CONF_MODEL_PREFERENCE = "model_preference"
CONF_SEARCH_FOCUS = "search_focus"
CONF_REASONING = "reasoning"

DEFAULT_MODEL = "experimental"
DEFAULT_SEARCH_FOCUS = "internet"
DEFAULT_REASONING = False

SEARCH_FOCUS_OPTIONS = [
    "internet",
    "scholar",
    "writing",
    "wolfram",
    "youtube",
    "reddit",
]
