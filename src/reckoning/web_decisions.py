"""Rendering for the web decision area: proposal review, correction, confirmation.

Pure functions: decision data in, HTML out. One correction form and one
confirmation form serve every render path, so recovery after a failed request
cannot diverge from the normal page.
"""

from __future__ import annotations

from html import escape
from secrets import token_urlsafe

from reckoning.continuity import OperationRecord, PersonalRecordVersion, Reckoning


def fresh_operation_id() -> str:
    return token_urlsafe(16)


def render_decision_content(
    decision: Reckoning | None,
    csrf_token: str,
    *,
    error: str | None = None,
    pending_meanings: dict[str, str] | None = None,
) -> str:
    if decision is None:
        return (
            '<header><p class="eyebrow">Decision</p><h1>Not found</h1></header>'
            '<p class="empty">No decision is selected.</p>'
        )
    error_markup = f'<p class="error">{escape(error)}</p>' if error else ""
    overrides = pending_meanings or {}
    records_html = "".join(
        _render_record_card(
            decision.id,
            record,
            csrf_token,
            expected_revision=decision.version,
            decision_status=decision.status,
            pending_meaning=overrides.get(record.record_id, ""),
        )
        for record in decision.current_records
    )
    confirm = ""
    if decision.status == "proposed" and decision.current_records:
        confirm = _confirmation_form(
            decision.id, csrf_token, decision.version, decision.draft.conflict
        )
    uncertainties = render_list(decision.draft.uncertainties)
    return f"""
      <header>
        <p class="eyebrow">Decision</p>
        <h1>{escape(decision.draft.conflict)}</h1>
      </header>
      <div class="status status-{escape(decision.status)}" aria-live="polite">
        {escape(decision.status)}
      </div>
      {error_markup}
      <section class="card proposal-card" aria-labelledby="proposal-heading">
        <h2 id="proposal-heading">Proposal</h2>
        <p><strong>Conflict:</strong> {escape(decision.draft.conflict)}</p>
        <p><strong>Matters now:</strong> {escape(" ".join(decision.draft.matters_now))}</p>
        <p><strong>Maintained:</strong> {escape(" ".join(decision.draft.maintained))}</p>
        <p><strong>Parked:</strong> {escape(" ".join(decision.draft.parked))}</p>
        <h2>Uncertainty</h2>
        {uncertainties}
        <p class="next-action"><strong>Next step:</strong> {escape(decision.draft.next_step)}</p>
        {records_html}
        {confirm}
      </section>
    """


def render_pending_input_card(
    operation: OperationRecord, csrf_token: str
) -> str:
    """Retry control for a proposal whose input survived a provider failure."""
    element_id = f"pending-{escape(operation.operation_id)}"
    return f"""
      <article class="card" data-pending-operation="{escape(operation.operation_id)}">
        <p><strong>Unsent proposal</strong> · the provider failed before Simon
          could answer. Your input was kept; retrying continues the same
          operation instead of duplicating it.</p>
        <form action="/decisions" method="post">
          <input type="hidden" name="_csrf_token"
            value="{escape(csrf_token, quote=True)}">
          <input type="hidden" name="operation_id"
            value="{escape(operation.operation_id, quote=True)}">
          <label for="{element_id}">Your input</label>
          <textarea id="{element_id}" name="situation"
            required>{escape(operation.pending_input)}</textarea>
          <button type="submit">Retry proposal</button>
        </form>
      </article>
    """


def _render_record_card(
    reckoning_id: str,
    record: PersonalRecordVersion,
    csrf_token: str,
    *,
    expected_revision: int,
    decision_status: str,
    pending_meaning: str,
) -> str:
    if decision_status == "confirmed":
        return (
            f'<article class="record" data-record-id="{escape(record.record_id)}">'
            f'<p><strong>{escape(record.record_type)}</strong> · '
            f'<span class="status status-confirmed">confirmed</span></p>'
            f"<p>{escape(record.meaning)}</p></article>"
        )
    return f"""
      <article class="record" data-record-id="{escape(record.record_id)}">
        <p><strong>{escape(record.record_type)}</strong> · <span class="status status-proposed">proposed</span></p>
        {_correction_form(
            reckoning_id,
            record.record_id,
            csrf_token,
            expected_revision,
            meaning=pending_meaning or record.meaning,
        )}
      </article>
    """


def _correction_form(
    reckoning_id: str,
    record_id: str,
    csrf_token: str,
    expected_revision: int,
    *,
    meaning: str,
) -> str:
    return f"""
      <form action="/decisions/{escape(reckoning_id)}/records/{escape(record_id)}/correct" method="post">
        <input type="hidden" name="_csrf_token" value="{escape(csrf_token, quote=True)}">
        <input type="hidden" name="expected_revision" value="{expected_revision}">
        <input type="hidden" name="operation_id" value="{escape(fresh_operation_id())}">
        <label for="meaning-{escape(record_id)}">Edit meaning</label>
        <textarea id="meaning-{escape(record_id)}" name="meaning" required>{escape(meaning)}</textarea>
        <button type="submit">Save correction</button>
      </form>
    """


def _confirmation_form(
    reckoning_id: str, csrf_token: str, expected_revision: int, conflict: str
) -> str:
    return f"""
      <form action="/decisions/{escape(reckoning_id)}/confirm" method="post">
        <input type="hidden" name="_csrf_token" value="{escape(csrf_token, quote=True)}">
        <input type="hidden" name="expected_revision" value="{expected_revision}">
        <input type="hidden" name="operation_id" value="{escape(fresh_operation_id())}">
        <p class="next-action">
          Confirming saves <strong>{escape(conflict)}</strong> exactly as shown
          (revision {expected_revision}).
        </p>
        <button type="submit">Confirm this version</button>
      </form>
    """


def render_list(items: tuple[str, ...]) -> str:
    if not items:
        return '<p class="empty">None recorded.</p>'
    return "<ul>" + "".join(f"<li>{escape(item)}</li>" for item in items) + "</ul>"
