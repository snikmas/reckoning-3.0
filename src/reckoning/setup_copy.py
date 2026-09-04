"""All user-facing setup copy, centralized for future translation.

Product names, commands, provider IDs, and model IDs are stable tokens and
stay verbatim inside the strings. English is the initial locale.
"""

from __future__ import annotations

LOCALE = "en"

BRANDING = (
    "┌─ RECKONING ─┐",
    "  setup",
)

BRAND_LINE = "== RECKONING setup =="

MODE_TITLE = "Choose how to set up Reckoning"
MODE_QUICK = "Quick Setup — persona and one provider; sensible defaults"
MODE_CUSTOM = "Custom Setup — every choice, step by step"
QUICK_EXPLANATION = (
    "Quick Setup configures your desired-self persona and one primary "
    "provider. Placement defaults to local storage on this device (shown "
    "again before activation). Profile onboarding and Telegram are optional "
    "steps afterward."
)

PLACEMENT_TITLE = "Choose where your data lives"
PLACEMENT_OPTIONS = {
    "local": (
        "Local — everything stays on this device",
        "Personal context, confirmed state, and approved sources are stored "
        "and processed locally.",
    ),
    "personal-server": (
        "Personal server — private state lives on your server",
        "Personal context and confirmed state are stored and processed on "
        "your own server.",
    ),
    "hybrid": (
        "Hybrid — private data local, approved remote sources on your server",
        "Private context stays local; approved remote sources live on your "
        "server.",
    ),
}
PLACEMENT_LOCAL_DEFAULT = (
    "Placement: local storage on this device (the Quick Setup default)."
)
PLACEMENT_CLOUD_NOTE = (
    "Note: with a cloud provider, selected prompt content is sent to that "
    "provider even when storage is local."
)

PERSONA_TITLE = "Choose your desired-self persona"
PERSONA_PRESET_SIMON = "Simon — composed, direct, and demanding"
PERSONA_PRESET_STEADY = "Steady — reflective, warm, and probing"
PERSONA_AUTHOR = "Author an original persona"
PERSONA_MANAGE = "Manage authored personas"
PERSONA_BUILDER_START = "Start from a preset or a blank template"
PERSONA_BLANK = "Blank template — balanced defaults"
PERSONA_PREVIEW_TITLE = "Persona preview (deterministic; no provider involved)"
AUTONOMY_FLOOR_TITLE = "The autonomy floor — no persona can change these rules"

PROVIDER_TITLE = "Choose a provider"
PROVIDER_DETECTED_TITLE = "Detected existing access (values are never shown)"
PROVIDER_ASK_TEST = (
    "Test a detected provider now? Detection itself made no network requests."
)
PROVIDER_PREFERENCE_TITLE = "No existing access found. What do you prefer?"
PROVIDER_PREFERENCE_CLOUD = "A cloud provider (Direct)"
PROVIDER_PREFERENCE_GATEWAY = "A routing gateway (one key, many models)"
PROVIDER_PREFERENCE_LOCAL = "Local inference on this machine"
PROVIDER_COMING_SOON = "Coming soon"
PROVIDER_RECOMMENDED = "recommended"
PROVIDER_MODEL_TITLE = "Choose a model"
PROVIDER_MODEL_SEARCH = "Type to filter the discovered models"
PROVIDER_MODEL_MANUAL = "Enter a model ID manually (Advanced)"
PROVIDER_VERIFY_INTRO = (
    "Reckoning will send one small fixed test message to prove this provider. "
    "No profile or personal content is included."
)
PROVIDER_VERIFY_CHARGE = "This request may use a small amount of paid credit."
PROVIDER_VERIFY_FREE = "This request is free."
PROVIDER_VERIFIED = "Real provider ready"
PROVIDER_DEMO = "Demo mode"
PROVIDER_UNVERIFIED_SAVED = (
    "Saved the credential unverified; it stays inactive and cannot complete "
    "setup. Verify it from the status view later."
)
PROVIDER_FAILURE_ACTIONS = "The provider test failed. What now?"
PROVIDER_LOCAL_MISSING = "No local runtime responded at the documented addresses."
PROVIDER_LIVE_SAMPLE = "Request one live persona sample with the verified provider?"

PROFILE_TITLE = "Profile onboarding (optional)"
PROFILE_SKIP = "Skip — set up the profile later"
PROFILE_GUIDED = "Answer a few guided questions (every one is optional)"
PROFILE_IMPORT = "Import a UTF-8 Markdown profile"
PROFILE_SECTIONS = (
    ("address", "How should Reckoning address you?"),
    ("situation", "What is your current situation?"),
    ("goals", "What goals are active right now?"),
    ("constraints", "What constraints matter?"),
    ("preferences", "What preferences should Reckoning respect?"),
    ("commitments", "What important commitments exist?"),
    ("boundaries", "What boundaries must Reckoning never cross?"),
)
PROFILE_IMPORT_PREVIEW = "Parsed statements (remove any before saving):"
PROFILE_SAVED_AS_PROPOSALS = (
    "Saved as unconfirmed proposals; they join your context only after you "
    "confirm them in the normal review flow."
)

CONNECTOR_TITLE = "Messaging connectors (optional)"
CONNECTOR_TELEGRAM = "Telegram"
CONNECTOR_COMING_SOON = "Coming soon"
TELEGRAM_NOT_CONFIGURED = "Not configured"
TELEGRAM_BOT_VERIFIED = "Bot verified"
TELEGRAM_UNPAIRED = "Verified, not paired"
TELEGRAM_READY = "Ready"
TELEGRAM_ERROR = "Error"
TELEGRAM_PAIRING_PROMPT = "Pair your private chat now? You can also do it later."
TELEGRAM_GATEWAY_NOTE = (
    "Setup does not start the Gateway. To run configured connectors: "
    "reckoning gateway"
)

FIRST_MESSAGE_TITLE = "Your first conversation"
FIRST_MESSAGE_INTRO = (
    "Write a real first message. The installed persona and provider answer "
    "with tools, external writes, routines, and memory confirmation disabled. "
    "Only an exchange you accept is saved."
)
FIRST_MESSAGE_ACTIONS = (
    "Accept this exchange",
    "Retry with a new message",
    "Edit persona",
    "Change model",
    "Change provider",
    "Exit (keep the verified provider)",
)

REVIEW_TITLE = "Final review"
REVIEW_CONFIRM = "Activate this installation?"

STATUS_TITLE = "Installation status"
STATUS_VERIFY_ALL = "Verify all (makes live checks)"
STATUS_REPAIR = "Repair a failing section"
STATUS_EDIT = "Edit one section"
STATUS_EXIT = "Exit"
STATUS_PAID_REFRESH = (
    "Refreshing this provider check can use paid credit. Refresh it now?"
)

RESUME_TITLE = "An interrupted setup draft exists"
RESUME_RESUME = "Resume where setup stopped"
RESUME_START_OVER = "Start over (preview what is removed first)"
RESUME_EXIT = "Exit without changes"
START_OVER_PREVIEW = "Starting over removes this draft; verified credentials stay."

ERROR_RECOVERY = "You can retry, go back, or exit; nothing was activated."

HELP_HINT = "Enter '?' for help, 'back' to go back, 'exit' to save and exit."
