"""All user-facing setup copy, centralized for future translation.

Product names, commands, provider IDs, and model IDs are stable tokens and
stay verbatim inside the strings. English is the initial locale.
"""

from __future__ import annotations

LOCALE = "en"

BRAND_LINE = "== RECKONING setup =="
WELCOME = (
    "Connect an AI provider, choose how Reckoning works with you, and finish "
    "with a real first conversation."
)

PLACEMENT_TITLE = "Choose where your data lives"
PLACEMENT_OPTIONS = {
    "local": (
        "Local — everything stays on this device",
        (
            "Personal context, confirmed state, and approved sources are stored "
            "and processed locally."
        ),
    ),
    "personal-server": (
        "Personal server — private state lives on your server",
        (
            "Personal context and confirmed state are stored and processed on "
            "your own server."
        ),
    ),
    "hybrid": (
        "Hybrid — private data local, approved remote sources on your server",
        (
            "Private context stays local; approved remote sources live on your "
            "server."
        ),
    ),
}
PLACEMENT_CLOUD_NOTE = (
    "Note: with a cloud provider, selected prompt content is sent to that "
    "provider even when storage is local."
)

PERSONA_TITLE = "How should Reckoning work with you?"
PERSONA_PRESET_SIMON = "Simon — composed, direct, and demanding"
PERSONA_PRESET_STEADY = "Steady — reflective, warm, and probing"
PERSONA_AUTHOR = "Author an original persona"
PERSONA_MANAGE = "Manage authored personas"
PERSONA_BUILDER_START = "Start from a preset or a blank template"
PERSONA_BLANK = "Blank template — balanced defaults"
PERSONA_PREVIEW_TITLE = "Persona preview (deterministic; no provider involved)"
AUTONOMY_FLOOR_TITLE = "The autonomy floor — no persona can change these rules"

PROVIDER_TITLE = "Choose an AI provider"
PROVIDER_DETECTED_TITLE = "Detected existing access (values are never shown)"
PROVIDER_COMING_SOON = "More providers coming soon"
PROVIDER_RECOMMENDED = "recommended"
PROVIDER_MODEL_TITLE = "Choose a model"
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

PROFILE_TITLE = "About you"
PROFILE_SKIP = "Skip for now — Reckoning can learn later with your permission"
PROFILE_GUIDED = "Answer a few guided questions (every one is optional)"
PROFILE_STARTER = "Create a starter profile file"
PROFILE_IMPORT = "Import a UTF-8 Markdown profile"
PROFILE_SECTIONS = (
    ("address", "How should Reckoning address you?"),
    ("work", "What are you working on now?"),
    ("priorities", "What are your current priorities?"),
    ("preferences", "How do you prefer to work?"),
    ("boundaries", "What boundaries must Reckoning never cross?"),
)
PROFILE_STARTER_HELP = (
    "Example:\n"
    "## Current work\n"
    "- I am preparing for an exam and building one project.\n\n"
    "Use short statements under the headings. Remove any section you do not "
    "want to share."
)
PROFILE_STARTER_TEMPLATE = """# About me

## How to address me

-

## Current work

-

## Current priorities

-

## Working preferences

-

## Boundaries

-
"""
PROFILE_IMPORT_PREVIEW = "Parsed statements (remove any before saving):"
PROFILE_SAVED_AS_PROPOSALS = (
    "Saved as unconfirmed proposals; they join your context only after you "
    "confirm them in the normal review flow."
)

CONNECTOR_TITLE = "Ways to use Reckoning"
CONNECTOR_TELEGRAM = "Telegram"
CONNECTOR_PROMPT = "Configure Telegram?"
CONNECTOR_SKIP = "Skip for now"
CONNECTOR_COMING_SOON = "More messaging apps coming soon"
TELEGRAM_NOT_CONFIGURED = "Not configured"
TELEGRAM_BOT_VERIFIED = "Bot verified"
TELEGRAM_UNPAIRED = "Verified, not paired"
TELEGRAM_READY = "Ready"
TELEGRAM_ERROR = "Error"
TELEGRAM_STATUS_LABELS = {
    "not-configured": TELEGRAM_NOT_CONFIGURED,
    "bot-verified": TELEGRAM_BOT_VERIFIED,
    "verified-not-paired": TELEGRAM_UNPAIRED,
    "ready": TELEGRAM_READY,
    "error": TELEGRAM_ERROR,
}
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
FIRST_MESSAGE_ACTION_PROMPT = "What do you want to do with this exchange?"
FIRST_MESSAGE_ACTIONS = (
    ("accept", "Accept this exchange"),
    ("retry", "Retry with a new message"),
    ("edit-persona", "Edit persona"),
    ("change-model", "Change model"),
    ("change-provider", "Change provider"),
    ("exit", "Exit (keep the verified provider)"),
)

REVIEW_TITLE = "Review"
REVIEW_CONFIRM = "Continue to your first conversation"

INTERFACE_TERMINAL_READY = "Terminal: Ready. Start a conversation with `reckoning`."
INTERFACE_WEB_READY = "Web: Ready. Start it with `reckoning web`."
INTERFACE_TERMINAL_COMPLETE = "Terminal: Ready. Start with `reckoning`."
INTERFACE_WEB_COMPLETE = "Web: Ready. Start with `reckoning web`."
TELEGRAM_COMPLETE = "Telegram: {status}."
GATEWAY_COMPLETE_RUNNING = "Gateway: Running (pid {pid})."
GATEWAY_COMPLETE_STOPPED = "Gateway: Stopped. Start with `reckoning gateway`."

STATUS_TITLE = "Installation status"
STATUS_VERIFY_ALL = "Verify all (makes live checks)"
STATUS_REPAIR = "Repair a failing section"
STATUS_EDIT = "Edit one section"
STATUS_EXIT = "Exit"
STATUS_PROMPT = "What next?"
STATUS_REPAIR_PROMPT = "Repair which section?"
STATUS_EDIT_PROMPT = "Edit which section?"
STATUS_NOTHING_FAILING = "Nothing is failing."
STATUS_PAID_REFRESH = (
    "Refreshing this provider check can use paid credit. Refresh it now?"
)

STATUS_LABEL_STORAGE = "Storage"
STATUS_LABEL_STYLE = "Agent style"
STATUS_LABEL_ABOUT = "About you"
STATUS_LABEL_PROVIDER = "AI provider and model"
STATUS_LABEL_INTERFACES = "Interfaces"
STATUS_LABEL_TELEGRAM = "Telegram"
STATUS_LABEL_GATEWAY = "Gateway"
STATUS_LABEL_DRAFT = "Setup draft"
STATUS_LABEL_FORMAT = "Installation format"

GATEWAY_RUNNING = "Running (pid {pid})"
GATEWAY_STOPPED = "Stopped; start with `reckoning gateway`"

STATUS_EDIT_CHOICES = (
    ("provider", STATUS_LABEL_PROVIDER),
    ("persona", STATUS_LABEL_STYLE),
    ("profile", STATUS_LABEL_ABOUT),
    ("connectors", STATUS_LABEL_TELEGRAM),
)

RESUME_TITLE = "An interrupted setup draft exists"
RESUME_RESUME = "Resume where setup stopped"
RESUME_START_OVER = "Start over (preview what is removed first)"
RESUME_EXIT = "Exit without changes"
START_OVER_PREVIEW = "Starting over removes this draft; verified credentials stay."

HELP_HINT = "Enter '?' for help, 'back' to go back, 'exit' to save and exit."

# --- Signal renderer chrome -------------------------------------------------
# Short structural labels used by the interactive and plain renderers. Keys
# and symbols live in the renderer theme; these strings carry the meaning.

BRAND_NAME = "RECKONING"
PROGRESS_POSITION = "section {index} of {total}"
FILTER_LABEL = "Filter"
FILTER_PLACEHOLDER = "Start typing to filter"
FILTER_EMPTY = "No matches; Backspace changes the filter."
SELECTED_ECHO = "Selected: {label}"
MORE_ABOVE = "{count} more above"
MORE_BELOW = "{count} more below"

CONTROL_MOVE = "move"
CONTROL_FILTER = "type to filter"
CONTROL_SELECT = "select"
CONTROL_HELP = "help"
CONTROL_BACK = "back"
CONTROL_EXIT = "exit"
CONTROL_SAVE_AND_EXIT = "save & exit"

STATUS_OK_PREFIX = "OK"
STATUS_WARNING_PREFIX = "Warning"
STATUS_FAILURE_PREFIX = "Failed"

PLAIN_PROGRESS = (
    "Progress: {done} complete; current section {index} of {total} — {title}"
)
PLAIN_NOT_SELECTABLE = "(not selectable)"
PLAIN_BACK_HINT = "Type 'back' to return to the previous step."
PLAIN_EXIT_HINT = "Type 'exit' to save a draft and leave."
PLAIN_ENTER_NUMBER = "Enter a number from 1 to {count}."
PLAIN_VALUE_REQUIRED = "A value is required."
