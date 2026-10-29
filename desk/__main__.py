"""override-desk command line.

    python -m desk init
    python -m desk override request  --as priya --property P004 --month 2026-10 --item repairs --value 18250 --reason "..." --ticket FIN-123
    python -m desk override approve  --as marc 1
    python -m desk override apply    --as jainam 1
    python -m desk override list
    python -m desk intake submit     --as li samples/capex_budget_2026-10_v1.csv --on 2026-10-03
    python -m desk intake approve    --as sofia 1
    python -m desk intake load       --as jainam 1 samples/capex_budget_2026-10_v1.csv
    python -m desk report            --property P004 --month 2026-10
    python -m desk audit verify | show
    python -m desk evidence          --month 2026-10
"""
import argparse
import csv
import sys
from datetime import date
from pathlib import Path

from . import audit, db, evidence, intake, overrides
from .roles import NotAllowed, load_roles

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
DB_PATH = ROOT / "desk.duckdb"


def cmd_init(args, con, roles):
    con.execute("delete from noi_actuals")
    with (ROOT / "samples" / "noi_actuals.csv").open() as f:
        rows = [[r["property_id"], r["month"], r["line_item"], r["amount"]] for r in csv.DictReader(f)]
    con.executemany("insert into noi_actuals values (?, ?, ?, ?::decimal(14,2))", rows)
    audit.record(con, "system", "base.loaded", "noi_actuals", {"rows": len(rows)})
    print(f"Loaded {len(rows)} rows into noi_actuals")


def cmd_override(args, con, roles):
    if args.action == "request":
        oid = overrides.request(con, roles, args.user, args.property, args.month, args.item, args.value, args.reason, args.ticket)
        print(f"Override #{oid} requested by {args.user}; waiting for approval")
    elif args.action == "approve":
        overrides.approve(con, roles, args.user, args.id)
        print(f"Override #{args.id} approved by {args.user}")
    elif args.action == "reject":
        overrides.reject(con, roles, args.user, args.id, args.reason or "")
        print(f"Override #{args.id} rejected by {args.user}")
    elif args.action == "apply":
        validation, before, after = overrides.apply(con, roles, args.user, args.id)
        print(f"Override #{args.id} applied by {args.user}")
        print(f"  checks: {validation}")
        print(f"  report total for the month: {before:,.2f} -> {after:,.2f}")
    elif args.action == "revert":
        overrides.revert(con, roles, args.user, args.id, args.reason or "")
        print(f"Override #{args.id} reverted by {args.user}")
    else:
        rows = con.execute("""select override_id, property_id || ' ' || month || ' ' || line_item, old_value, new_value, status,
                                     requested_by, coalesce(approved_by, '-'), coalesce(applied_by, '-'), ticket
                              from overrides order by override_id""").fetchall()
        print(f"{'#':<4}{'value':<30}{'old':>12}{'new':>12}  {'status':<10}{'requested':<11}{'approved':<10}{'applied':<9}ticket")
        for r in rows:
            print(f"{r[0]:<4}{r[1]:<30}{r[2]:>12,.2f}{r[3]:>12,.2f}  {r[4]:<10}{r[5]:<11}{r[6]:<10}{r[7]:<9}{r[8]}")


def cmd_intake(args, con, roles):
    contracts = intake.load_contracts(CONFIG)
    if args.action == "submit":
        as_of = date.fromisoformat(args.on) if args.on else date.today()
        iid, status, problems = intake.submit(con, roles, contracts, args.user, Path(args.file), args.contract, as_of)
        print(f"Intake #{iid} {Path(args.file).name}: {status.upper()}")
        for p in problems:
            print(f"  - {p}")
    elif args.action == "approve":
        intake.approve(con, roles, contracts, args.user, args.id)
        print(f"Intake #{args.id} approved by {args.user}")
    elif args.action == "load":
        ok, rec = intake.load(con, roles, contracts, args.user, args.id, Path(args.file))
        print(f"Intake #{args.id} loaded by {args.user}; reconciliation {'matched' if ok else 'MISMATCH'} ({rec})")
    else:
        rows = con.execute("""select intake_id, file_name, status, coalesce(rows::varchar, '-'), coalesce(amount_total::varchar, '-'),
                                     submitted_by, coalesce(approved_by, '-'), coalesce(loaded_by, '-'), left(sha256, 12)
                              from intake order by intake_id""").fetchall()
        print(f"{'#':<4}{'file':<32}{'status':<11}{'rows':>5}{'total':>14}  {'submitted':<10}{'approved':<9}{'loaded':<8}sha256")
        for r in rows:
            print(f"{r[0]:<4}{r[1]:<32}{r[2]:<11}{r[3]:>5}{r[4]:>14}  {r[5]:<10}{r[6]:<9}{r[7]:<8}{r[8] or '-'}")


def cmd_report(args, con, roles):
    rows = con.execute("""select f.line_item, a.amount, f.amount, f.overridden, f.override_id
                          from noi_actuals_final f join noi_actuals a using (property_id, month, line_item)
                          where f.property_id = ? and f.month = ? order by f.line_item""", [args.property, args.month]).fetchall()
    print(f"NOI actuals for {args.property}, {args.month}  (what the report shows)")
    print(f"  {'line item':<18}{'source':>12}{'reported':>12}")
    for item, src, rep, over, oid in rows:
        print(f"  {item:<18}{src:>12,.2f}{rep:>12,.2f}" + (f"  <- override #{oid}" if over else ""))


def cmd_audit(args, con, roles):
    if args.action == "verify":
        ok, msg = audit.verify(con)
        print(("OK  " if ok else "BROKEN  ") + msg)
        return 0 if ok else 1
    rows = con.execute("select seq, logged_at, actor, action, object, left(hash, 10) from audit_log order by seq desc limit ?", [args.last]).fetchall()
    for r in reversed(rows):
        print(f"{r[0]:>4}  {r[1]:%Y-%m-%d %H:%M:%S}  {r[2]:<8}{r[3]:<26}{r[4]:<25}{r[5]}")


def cmd_evidence(args, con, roles):
    out, lines = evidence.export(con, args.month, ROOT / "evidence" / args.month)
    print("\n".join(lines))
    print(f"\nWritten to {out.relative_to(ROOT)}/: " + ", ".join(sorted(p.name for p in out.iterdir())))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="desk", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(DB_PATH))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")

    o = sub.add_parser("override")
    o.add_argument("action", choices=["request", "approve", "reject", "apply", "revert", "list"])
    o.add_argument("id", nargs="?", type=int)
    o.add_argument("--as", dest="user")
    for a in ("--property", "--month", "--item", "--value", "--reason", "--ticket"):
        o.add_argument(a)

    i = sub.add_parser("intake")
    i.add_argument("action", choices=["submit", "approve", "load", "list"])
    i.add_argument("rest", nargs="*")
    i.add_argument("--as", dest="user")
    i.add_argument("--contract", default="capex_budget")
    i.add_argument("--on", help="submission date (default today)")

    r = sub.add_parser("report")
    r.add_argument("--property", required=True)
    r.add_argument("--month", required=True)

    a = sub.add_parser("audit")
    a.add_argument("action", choices=["verify", "show"])
    a.add_argument("--last", type=int, default=20)

    e = sub.add_parser("evidence")
    e.add_argument("--month", required=True)

    args = ap.parse_args(argv)
    if args.cmd == "intake":
        rest = args.rest
        if args.action == "submit":
            args.file = rest[0]
        elif args.action == "approve":
            args.id = int(rest[0])
        elif args.action == "load":
            args.id, args.file = int(rest[0]), rest[1]
    con = db.connect(args.db)
    roles = load_roles(CONFIG)
    handler = {"init": cmd_init, "override": cmd_override, "intake": cmd_intake, "report": cmd_report,
               "audit": cmd_audit, "evidence": cmd_evidence}[args.cmd]
    try:
        return handler(args, con, roles) or 0
    except NotAllowed as e:
        print(f"REFUSED: {e}")
        return 2
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
