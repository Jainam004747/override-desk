import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from desk import audit, db, intake, overrides
from desk.roles import NotAllowed, load_roles

ROOT = Path(__file__).resolve().parent.parent
ROLES = load_roles(ROOT / "config")
CONTRACTS = intake.load_contracts(ROOT / "config")


@pytest.fixture()
def con(tmp_path):
    c = db.connect(tmp_path / "t.duckdb")
    c.execute("insert into noi_actuals values ('P001', '2026-10', 'repairs', -1000.00), ('P001', '2026-10', 'rental_income', 50000.00)")
    return c


def new_override(con, user="priya", value="-800"):
    return overrides.request(con, ROLES, user, "P001", "2026-10", "repairs", value, "double posting", "FIN-1")


def test_full_override_flow_changes_only_the_report_view(con):
    oid = new_override(con)
    overrides.approve(con, ROLES, "marc", oid)
    _, before, after = overrides.apply(con, ROLES, "jainam", oid)
    assert after - before == Decimal("200.00")
    assert con.execute("select amount from noi_actuals where line_item = 'repairs'").fetchone()[0] == Decimal("-1000.00")
    assert con.execute("select amount from noi_actuals_final where line_item = 'repairs'").fetchone()[0] == Decimal("-800.00")
    overrides.revert(con, ROLES, "jainam", oid, "test")
    assert con.execute("select amount from noi_actuals_final where line_item = 'repairs'").fetchone()[0] == Decimal("-1000.00")


def test_nobody_approves_their_own_override(con):
    oid = new_override(con, user="marc")
    with pytest.raises(NotAllowed, match="separation of duties"):
        overrides.approve(con, ROLES, "marc", oid)


def test_roles_are_enforced(con):
    with pytest.raises(NotAllowed):
        new_override(con, user="jainam")             # operators don't raise requests
    oid = new_override(con)
    with pytest.raises(NotAllowed):
        overrides.apply(con, ROLES, "jainam", oid)   # not approved yet
    overrides.approve(con, ROLES, "marc", oid)
    with pytest.raises(NotAllowed):
        overrides.apply(con, ROLES, "marc", oid)     # approvers don't apply


def test_override_refused_if_source_changed_after_request(con):
    oid = new_override(con)
    overrides.approve(con, ROLES, "marc", oid)
    con.execute("update noi_actuals set amount = -1100 where line_item = 'repairs'")
    with pytest.raises(NotAllowed, match="base value changed"):
        overrides.apply(con, ROLES, "jainam", oid)


def test_one_active_override_per_value(con):
    new_override(con)
    with pytest.raises(NotAllowed, match="already covers"):
        new_override(con)


def test_refusals_are_audited(con):
    oid = new_override(con, user="marc")
    with pytest.raises(NotAllowed):
        overrides.approve(con, ROLES, "marc", oid)
    actions = [a for (a,) in con.execute("select action from audit_log").fetchall()]
    assert "override.approve.refused" in actions


def test_audit_chain_detects_edits_and_deletes(con):
    for i in range(3):
        audit.record(con, "jainam", "test.action", f"x{i}", {"i": i})
    assert audit.verify(con)[0]
    con.execute("update audit_log set actor = 'someone' where seq = 2")
    ok, msg = audit.verify(con)
    assert not ok and "entry 2" in msg
    con.execute("update audit_log set actor = 'jainam' where seq = 2")
    assert audit.verify(con)[0]
    con.execute("delete from audit_log where seq = 2")
    assert not audit.verify(con)[0]


def copy_sample(tmp_path, name, new_name=None):
    for ext in (".csv", ".ctl"):
        shutil.copy(ROOT / "samples" / f"{name}{ext}", tmp_path / f"{new_name or name}{ext}")
    return tmp_path / f"{new_name or name}.csv"


def test_good_file_passes_and_bad_file_lists_every_problem(tmp_path):
    facts, problems = intake.validate(copy_sample(tmp_path, "capex_budget_2026-10_v1"), CONTRACTS["capex_budget"], date(2026, 10, 3))
    assert problems == [] and facts["rows"] == 7
    _, problems = intake.validate(copy_sample(tmp_path, "capex_budget_2026-10_v2"), CONTRACTS["capex_budget"], date(2026, 10, 3))
    assert len(problems) == 4


def test_late_file_and_bad_name(tmp_path):
    f = copy_sample(tmp_path, "capex_budget_2026-10_v1")
    _, problems = intake.validate(f, CONTRACTS["capex_budget"], date(2026, 10, 9))
    assert any(p.startswith("late") for p in problems)
    g = copy_sample(tmp_path, "capex_budget_2026-10_v1", "capex budget october")
    _, problems = intake.validate(g, CONTRACTS["capex_budget"], date(2026, 10, 3))
    assert "doesn't match" in problems[0]


def test_missing_control_file(tmp_path):
    f = copy_sample(tmp_path, "capex_budget_2026-10_v1")
    f.with_suffix(".ctl").unlink()
    _, problems = intake.validate(f, CONTRACTS["capex_budget"], date(2026, 10, 3))
    assert any("control file" in p for p in problems)


def test_file_edited_after_approval_is_not_loaded(con, tmp_path):
    f = copy_sample(tmp_path, "capex_budget_2026-10_v1")
    iid, status, _ = intake.submit(con, ROLES, CONTRACTS, "li", f, "capex_budget", date(2026, 10, 3))
    assert status == "validated"
    with pytest.raises(NotAllowed, match="file_owner"):
        intake.approve(con, ROLES, CONTRACTS, "li", iid)          # the submitter isn't the owner
    intake.approve(con, ROLES, CONTRACTS, "sofia", iid)
    f.write_text(f.read_text().replace("185000.00", "285000.00"))
    with pytest.raises(NotAllowed, match="changed after it was approved"):
        intake.load(con, ROLES, CONTRACTS, "jainam", iid, f)


def test_load_reconciles(con, tmp_path):
    f = copy_sample(tmp_path, "capex_budget_2026-10_v1")
    iid, _, _ = intake.submit(con, ROLES, CONTRACTS, "li", f, "capex_budget", date(2026, 10, 3))
    intake.approve(con, ROLES, CONTRACTS, "sofia", iid)
    ok, rec = intake.load(con, ROLES, CONTRACTS, "jainam", iid, f)
    assert ok and "7/7" in rec
