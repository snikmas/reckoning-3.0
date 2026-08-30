# Architecture for vertical slices 06 through 15

## Caller usage

The local web adapter keeps using `ReckoningApplication.send_message`. Selecting
DeepSeek changes the provider dependency, not the application operation or prompt
precedence.

```python
application = create_local_application(
    provider_name="deepseek",
    deepseek_api_key=key,
)
message = application.send_message("Help me compare these options.")
runs = application.inspect_model_runs()
```

The remaining slices use small domain services. Each service owns the rules for
one group of records.

```python
context.remember(...)
context.correct(...)
relevant = context.retrieve(query)

feasible = goals.calculate_feasible_set(candidate_goals, capacity)
check_in = plans.check_in(...)

research.complete(request, conclusion=answer, sources=sources, completed_at=now)
forecast = forecasts.create_confirmed(...)
proposal = forecasts.propose_revision(...)
```

## Type sketch

- `ProviderResponse` carries content, provider identity, calls, retries, latency,
  and token usage. `ModelRunRecord` records success or failure at the application
  boundary.
- `PersonalContextVersion` keeps original wording and canonical meaning. A
  correction creates a new version. Retrieval applies relevance, source,
  sensitivity, permission, freshness, and placement filters.
- `SuppressionMarker` contains only the deleted record ID and deletion time.
  `ContextLinkRemover` removes references from dependent stores.
- `DirectionVersion`, `GoalVersion`, and `PlanVersion` keep lifecycle history.
  `FeasibleGoalSet` explains included and displaced goals without applying the
  recommendation.
- `ResearchConclusion` contains bounded source metadata and no watch, routine, or
  broader permission.
- `GoalForecast` records ranges and evidence. `ForecastRevisionProposal` keeps a
  recalculation separate from the active forecast until acceptance.

## Module map

| Module | Responsibility |
| --- | --- |
| `application.py` | Conversation boundary, protected response policy, model run receipts |
| `providers.py` | DeepSeek transport, retries, response validation, token usage |
| `trials.py` | Private-slice evidence and acceptance gate |
| `personal_context.py` | Versioned context, retrieval filters, lifecycle, deletion |
| `memory_maintenance.py` | Atomic maintenance results and review-only pattern proposals |
| `planning.py` | Direction, goal, feasible-set, plan, and check-in rules |
| `research_forecasts.py` | Bounded research, forecasts, and revision proposals |
| `json_store.py` | Atomic JSON writes shared by file repositories |

## Synthesis decision

I compared two shapes. One generic record engine would share storage and lifecycle
code, but callers would need to know type tags, legal transitions, and filtering
rules. That moves domain complexity into every caller.

The selected design uses bounded services with typed records. It repeats a small
amount of repository code, but each public operation enforces the rules for its
record. The shared JSON helper handles only atomic file replacement. It does not
try to become a generic domain framework.
