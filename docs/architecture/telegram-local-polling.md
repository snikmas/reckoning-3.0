# Local Telegram polling

## Caller usage

```text
reckoning setup
  -> choose a messaging gateway from the setup menu
  -> choose a model provider from the setup menu
  -> skip the provider API-key prompt when Fake is selected
  -> ask for the BotFather token without echoing it
  -> verify the bot with getMe
  -> ask the user to send a one-time /connect code
  -> record that private chat as the only allowed chat

reckoning-telegram
  -> load the local Telegram credential file
  -> long-poll Telegram for text messages
  -> pass each allowed message through TelegramGateway
  -> send the plain-text Reckoning reply to the same chat
```

The bot works while the command is running. It uses the existing Reckoning
instance and channel history. It does not expose an HTTP server to the internet.

## Design sketch

```python
@dataclass(frozen=True)
class TelegramPollingSettings:
    bot_token: str
    allowed_chat_ids: tuple[str, ...]
    bot_username: str
    next_update_offset: int | None
    gateway_name: str
    provider_name: str

    @classmethod
    def load(cls, path: Path) -> TelegramPollingSettings: ...
    def save(self, path: Path) -> None: ...


class TelegramBotApi:
    def get_me(self) -> dict[str, object]: ...
    def get_webhook_info(self) -> dict[str, object]: ...
    def delete_webhook(self) -> None: ...
    def get_updates(self, *, offset: int | None, timeout: int) -> tuple[dict[str, object], ...]: ...
    def send_message(self, chat_id: str, text: str) -> None: ...


class TelegramUpdateAdapter:
    def handle_update(self, update: dict[str, object]) -> TelegramReply: ...


class TelegramPollingApplication:
    def run_once(self, offset: int | None) -> int | None: ...
    def run_forever(self, *, initial_offset: int | None) -> None: ...
```

`TelegramUpdateAdapter` owns Telegram update parsing. Both the existing webhook
and the new polling runtime use it. `TelegramGateway` remains the only path into
Reckoning, so polling does not gain Control or operational authority. Setup
saves the next update offset. Polling starts at that offset and does not discard
messages that arrive between pairing and startup.

The first setup menu displays Telegram, Discord, WhatsApp, and Slack. Only
Telegram is enabled. The provider menu displays Fake, DeepSeek, and OrcaRouter.
Only Fake is enabled in the menu, and Fake needs no API key. The setup saves both
choices so that a later provider setup can add the API-key step without changing
the gateway flow.

## Alternatives considered

The first option was to keep the webhook as the only runtime and add a setup
command for a tunnel, TLS endpoint, and `setWebhook`. It changes less Python but
makes a local trial depend on public networking and another long-running tool.

The second option was local long polling through `getUpdates`. It adds a small
Bot API client, but needs no public port. Telegram permits either polling or a
webhook, not both, so setup checks for an existing webhook before switching.

## Synthesis decision

Use polling as the default local runtime and keep `webhook` as an explicit
deployment command. Store the bot token outside the repository in a mode-0600
configuration file. Pair one private Telegram chat with a random one-time code
instead of trusting the first pending update. Use the saved Fake provider when
the user starts `reckoning-telegram` without `--provider`. Keep data placement
unchanged.
