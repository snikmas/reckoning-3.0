# Interactive setup wizard: multi-provider credentials

Status: implemented (#68 credential store and migration, #69 provider loop
and default choice, #70 verification-failure recovery, #71 re-run
management menu).

## Context

`reckoning setup` (`src/reckoning/setup.py`) is an interactive wizard that
configures a gateway and a model provider, verifies the provider API key live,
and saves it to `~/.config/reckoning/provider.json`. Today it stores exactly
one provider: `ProviderCredentials` is a single `(provider_name, api_key)`
pair (`src/reckoning/config.py`), so configuring a second provider overwrites
the first.

The goal is a setup flow modeled on Hermes Agent's `hermes setup` /
`hermes model` / `hermes auth` pattern: multiple providers configured in one
session, an explicit default, and re-runs that manage existing credentials
instead of blindly overwriting them.

## Decisions

### Provider loop

- Provider menu (fake / DeepSeek / OrcaRouter) marks already-configured
  providers.
- Key entry stays hidden (`getpass`) and is verified live via
  `verify_provider_api_key`.
- On verification failure the wizard offers **retry / save anyway / abort**.
  It never dies with a traceback. "Save anyway" covers the case where the
  provider's verification endpoint is down but the key is valid.
- After each provider: "Add another provider? [y/N]" loops back to the menu.

### Default provider

- After the loop, an explicit "Which is the default?" prompt among the
  configured providers.
- Runtime precedence is unchanged: environment variables (`.env`, process
  env) still override the credential file.

### Credential storage

- `provider.json` becomes:

  ```json
  {
    "default_provider": "deepseek",
    "providers": {
      "deepseek": "sk-...",
      "orcarouter": "sk-..."
    }
  }
  ```

- One file, atomic write (`atomic_write_json`), owner-only permissions
  (chmod 600) — the AWS `~/.aws/credentials` pattern: one credential store
  keyed by provider name plus a separate default marker.
- The old single-slot format (`{"provider_name": ..., "api_key": ...}`)
  auto-migrates on load and is rewritten in the new format on next save. Old
  files must keep working; users never lose a configured key over a format
  change.

### Gateway section

- The gateway section is structurally a loop like the provider section, but
  only Telegram is selectable; Discord/WhatsApp/Slack remain "coming later"
  placeholders.
- Real Discord/WhatsApp/Slack integrations are separate follow-up issues and
  are out of scope here.

### Re-run behavior

- When configuration already exists, `reckoning setup` detects it and opens a
  menu instead of the first-run wizard:

  **add provider / replace key / remove provider / change default / done**

- Current values are shown as defaults; pressing Enter keeps them. Nothing is
  blindly overwritten.

## Out of scope (future work)

- Real Discord/WhatsApp/Slack gateway integrations.
- Non-interactive setup (`--provider X --api-key ...` or env-driven,
  hermes `--non-interactive` style) for scripting and CI.

## Tests

Extending `tests/test_provider_setup.py` and `tests/test_provider_config.py`
with scripted `line_reader` / `secret_reader` inputs, as existing tests do:

- save/load round-trip with multiple providers and a default
- old single-slot `provider.json` migrates and still loads
- the loop configures two providers in one session
- re-run menu: replace key, remove provider, change default
- verification failure: retry, save-anyway, and abort paths
- fake provider still needs no key
- env var override still wins at runtime
