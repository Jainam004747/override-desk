"""Business overrides: request -> approve -> apply, with a check at every step.

The base table is never edited. An applied override sits in its own table and the
reporting view puts it on top, so the original value is always still there and an
override can be reverted without anyone needing a backup.
"""
from decimal import Decimal, InvalidOperation

from . import audit
from .roles import NotAllowed, different, require

# Changes bigger than this share of the original value are flagged in the validation note,
# so the approver sees it in the evidence even though it's allowed.
LARGE_CHANGE = Decimal("0.5")


def request(con, roles, user, property_id, month, line_item, new_value, reason, ticket):
    try:
        require(roles, user, "requester")
        if not reason or not ticket:
            raise NotAllowed("an override needs a reason and a ticket number")
        try:
            value = Decimal(str(new_value))
        except InvalidOperation:
            raise NotAllowed(f"new value {new_value!r} is not a number")
        row = con.execute("select amount from noi_actuals where property_id = ? and month = ? and line_item = ?",
                          [property_id, month, line_item]).fetchone()
        if row is None:
            raise NotAllowed(f"no row for {property_id} / {month} / {line_item}")
        pending = con.execute("""select override_id from overrides where property_id = ? and month = ? and line_item = ?
                                 and status in ('requested', 'approved', 'applied')""", [property_id, month, line_item]).fetchone()
        if pending:
            raise NotAllowed(f"override #{pending[0]} already covers this value; revert it first")
    except NotAllowed as e:
        audit.record(con, user, "override.request.refused", f"{property_id}/{month}/{line_item}", {"reason": str(e)})
        raise
    oid = con.execute("""insert into overrides (property_id, month, line_item, old_value, new_value, reason, ticket,
                         status, requested_by, requested_at) values (?, ?, ?, ?, ?, ?, ?, 'requested', ?, timezone('UTC', now()))
                         returning override_id""",
                      [property_id, month, line_item, row[0], value, reason, ticket, user]).fetchone()[0]
    audit.record(con, user, "override.requested", f"override#{oid}",
                 {"key": f"{property_id}/{month}/{line_item}", "old": row[0], "new": value, "reason": reason, "ticket": ticket})
    return oid


def _get(con, oid):
    row = con.execute("select status, requested_by, approved_by, property_id, month, line_item, old_value, new_value from overrides where override_id = ?",
                      [oid]).fetchone()
    if row is None:
        raise NotAllowed(f"override #{oid} does not exist")
    return row


def approve(con, roles, user, oid):
    try:
        require(roles, user, "approver")
        status, requested_by, *_ = _get(con, oid)
        different(user, requested_by, "approve their own override")
        if status != "requested":
            raise NotAllowed(f"override #{oid} is {status}, not waiting for approval")
    except NotAllowed as e:
        audit.record(con, user, "override.approve.refused", f"override#{oid}", {"reason": str(e)})
        raise
    con.execute("update overrides set status = 'approved', approved_by = ?, approved_at = timezone('UTC', now()) where override_id = ?", [user, oid])
    audit.record(con, user, "override.approved", f"override#{oid}")


def reject(con, roles, user, oid, reason):
    require(roles, user, "approver")
    status, requested_by, *_ = _get(con, oid)
    if status != "requested":
        raise NotAllowed(f"override #{oid} is {status}")
    con.execute("update overrides set status = 'rejected', approved_by = ?, approved_at = timezone('UTC', now()), validation = ? where override_id = ?",
                [user, f"rejected: {reason}", oid])
    audit.record(con, user, "override.rejected", f"override#{oid}", {"reason": reason})


def apply(con, roles, user, oid):
    """Technical validation, then apply. Checks the value hasn't changed since the request."""
    try:
        require(roles, user, "operator")
        status, requested_by, approved_by, pid, month, item, old, new = _get(con, oid)
        if status != "approved":
            raise NotAllowed(f"override #{oid} is {status}; only approved overrides can be applied")
        different(user, approved_by, "apply an override they approved")
        current = con.execute("select amount from noi_actuals where property_id = ? and month = ? and line_item = ?",
                              [pid, month, item]).fetchone()
        if current is None or current[0] != old:
            raise NotAllowed(f"base value changed since the request ({old} -> {current[0] if current else 'missing'}); needs a new request")
    except NotAllowed as e:
        audit.record(con, user, "override.apply.refused", f"override#{oid}", {"reason": str(e)})
        raise
    notes = ["key exists", "base value unchanged since request", "approver differs from requester and operator"]
    if old and abs(new - old) > abs(old) * LARGE_CHANGE:
        notes.append(f"large change: {((new - old) / abs(old)):+.0%}")
    validation = "; ".join(notes)
    before = con.execute("select sum(amount) from noi_actuals_final where month = ?", [month]).fetchone()[0]
    con.execute("update overrides set status = 'applied', applied_by = ?, applied_at = timezone('UTC', now()), validation = ? where override_id = ?",
                [user, validation, oid])
    after = con.execute("select sum(amount) from noi_actuals_final where month = ?", [month]).fetchone()[0]
    # The report total must move by exactly the override's delta and nothing else.
    expected = before + (new - old)
    if after != expected:
        con.execute("update overrides set status = 'approved', applied_by = null, applied_at = null, validation = null where override_id = ?", [oid])
        audit.record(con, user, "override.apply.failed", f"override#{oid}", {"before": before, "after": after, "expected": expected})
        raise NotAllowed(f"downstream total moved by {after - before}, expected {new - old}; rolled back")
    audit.record(con, user, "override.applied", f"override#{oid}",
                 {"validation": validation, "report_total_before": before, "report_total_after": after})
    return validation, before, after


def revert(con, roles, user, oid, reason):
    require(roles, user, "operator")
    status, *_ = _get(con, oid)
    if status != "applied":
        raise NotAllowed(f"override #{oid} is {status}, not applied")
    con.execute("update overrides set status = 'reverted', validation = validation || '; reverted: ' || ? where override_id = ?", [reason, oid])
    audit.record(con, user, "override.reverted", f"override#{oid}", {"reason": reason})
