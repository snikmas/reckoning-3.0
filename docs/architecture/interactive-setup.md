# Guided setup and configured-state management

Status: implemented (#80 replacement of the Quick and Custom wizard; #101
owns the production Signal presentation).

## Context

`reckoning setup` is the single configuration surface. On a fresh machine it
runs one guided journey; on a configured installation it opens an
installation status view for inspection and focused editing. The workflow
owns setup policy and never prints terminal UI directly: a `SetupUI` adapter
owns prompts, navigation, colors, and fallbacks.

## Decisions

### One guided journey

Fresh setup has five visible sections, in order:

1. **AI provider and model** — provider authentication, one consented
   non-personal verification request, and model selection.
2. **Ways to use Reckoning** — Terminal and Web are ready capabilities;
   Telegram is the optional connector.
3. **Agent style** — Simon, Steady, or an original persona, presented through
   the domain label *Desired-self persona*.
4. **About you** — optional guided questions, starter-file generation, a
   deterministic import, or Skip for now.
5. **Review** — every choice is shown and editable.

Review is followed by one real first conversation. Only an accepted exchange
is persisted. Activation is atomic, and setup reopens the installed state and
finds the accepted exchange before reporting success.

### One workflow for every renderer

`SetupWorkflow` (`src/reckoning/setup_workflow.py`) is the only policy
implementation. `InteractiveUI`, `PlainTextUI`, and `NonInteractiveUI`
(`src/reckoning/setup_terminal.py`) adapt presentation but cannot change
policy. The primary behavior seam is `reckoning setup` against isolated
state.

### Drafts and migration

Interrupted setup saves a non-secret draft (`schema_version: 2`). The draft
never contains credential values. A schema-1 draft migrates only when its
meaning maps unambiguously: the obsolete Quick/Custom mode is removed while
provider, Agent style, About you, Telegram, and storage choices are kept. An
incompatible draft is never deleted with credentials or installation data;
setup shows exactly what it would remove and asks first.

### Credential storage

`provider.json` holds one entry per provider plus a default-provider marker:

```json
{
  "schema_version": 2,
  "default_provider": "deepseek",
  "providers": {
    "deepseek": {"secret": "sk-...", "verified": true}
  }
}
```

The file is written atomically with owner-only permissions. Environment
references are stored as references (`env:NAME`), never copied. The legacy
single-slot format auto-migrates on load and is rewritten on the next save.

### Configured-state view

Re-running `reckoning setup` on a configured installation loads cached status
without any network request and reports provider and model, verification
freshness, Agent style, About you proposals, storage, interfaces, Telegram,
and Gateway runtime state. Live **Verify all** actions require explicit
selection and disclose possible paid use first. Section editing covers
provider, Agent style, About you, and Telegram.

Gateway runtime state comes from a pid record the foreground `reckoning
gateway` process writes while it runs (`src/reckoning/runtime_status.py`);
setup never guesses that the gateway is stopped.

### Command contract

`reckoning` starts a Terminal conversation, `reckoning web` starts the Web
interface, `reckoning gateway` runs configured messaging connectors in the
foreground, and `reckoning setup` owns first setup plus configured-state
inspection and editing. Diagnosis, reset, backup, restore, export, and
migration remain separate operational and recovery commands. The focused
`provider`, `persona`, and `channel` commands are removed.

## Tests

`tests/test_setup_workflow.py`, `tests/test_setup_terminal.py`,
`tests/test_setup_cli.py`, `tests/test_main_cli.py`,
`tests/test_terminal_conversation.py`, and `tests/test_runtime_status.py`
cover the guided journey, draft migration, configured-state editing, the
command contract, and truthful gateway runtime state at the user-visible
seams.
