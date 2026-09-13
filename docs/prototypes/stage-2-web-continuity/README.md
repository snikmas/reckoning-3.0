# Stage 2 web continuity prototype

Status: simulated review artifact for [#123](https://github.com/snikmas/reckoning-3.0/issues/123). Visual review by Mary is pending. This is review evidence only, not production code, and it is not merged into `main`.

## What this is

A single self-contained HTML file that adapts the selected Reckoning 2.0 system-panel visual language to the Stage 2 ordinary conversation journey. It exists so Mary can judge typography, information density, Inspector behavior, composer placement, and Simon's character treatment before any production visual work.

It is explicitly simulated:

- It calls no model and makes no network request.
- It saves no data; reloading the page discards every change.
- Every person, message, decision, and outcome is fictional.
- Status labels shown here are simulated; production must derive them from real application state.

## Run it

```bash
python -m http.server 8765 --directory docs/prototypes/stage-2-web-continuity
```

Then open <http://localhost:8765/>.

No build step and no new dependency are required.

## Scenes

Open **Scenes** in the top bar to load any required scene. Each scene is deterministic and keyboard reachable.

| Scene | Demonstrates |
| --- | --- |
| Empty first conversation | First use with no supplied profile and no stored decisions |
| Long multilingual conversation | English, Russian, Chinese, and mixed-language wrapping |
| Study-method proposal | One concise proposal with reasons, trade-offs, uncertainty, and Inspector evidence |
| Correction and version | A correction creates a visible new proposal version and a change note |
| Exact-version confirmation | Confirmation is bound to the displayed version |
| Stale confirmation | Confirming a superseded version is rejected |
| Ambiguous assent | A bare "yes" asks for clarification instead of confirming |
| Returning Home | One confirmed decision and one unfinished proposal |
| Review outcome | An outcome report and a corrected outcome with history |
| Provider failure | The received message stays visible and retry state is honest |
| Reply feedback | Separate optional useful and natural ratings plus comment-only feedback |

The composer also works for free text. Typing an ambiguous assent such as `yes`, `ok`, `да`, or `好` triggers the clarification path.

## Layout

- Home, Simon, Plan, Review, and Control remain the navigation contract.
- Simon is a spacious conversation workspace with a persistent composer.
- The Inspector opens for the selected reply, proposal, decision, or check-in.
- On wide screens the Inspector sits beside the workspace. On narrow screens it becomes an accessible detail view and returns focus to the opener when closed.

## Accessibility and resilience notes

- Every status color is paired with text and an icon.
- Focus is visible; `:focus-visible` uses a green outline.
- `prefers-reduced-motion: reduce` disables animation. The Scenes panel also has a reduced-motion toggle for review.
- Layout avoids required horizontal page scrolling at desktop, phone, and 200 percent zoom.
- Long English, Russian, and Chinese text wraps inside its containers.

## Review decisions pending

Mary's visual review settles these questions. They are not approved by this artifact.

1. Is the typography right?
2. Is the information density comfortable for a long conversation?
3. Is the Inspector behavior right on desktop and phone?
4. Is the composer placement right?
5. Is Simon's character treatment too weak, right, or distracting?

## Files

- `index.html` — the prototype.
- `README.md` — this file.
