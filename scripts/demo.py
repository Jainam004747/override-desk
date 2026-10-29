"""Walks through a month of override and manual-file activity, including the things the
controls are meant to stop. Starts from an empty database each time.

    python scripts/demo.py
"""
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

STEPS = [
    ("Load the curated NOI data the reports read", "init"),
    ("A steward asks to correct a repairs figure that was posted twice",
     'override request --as priya --property P004 --month 2026-10 --item repairs --value -9875.40 '
     '--reason "Invoice INV-4471 posted twice by the property manager" --ticket FIN-2291'),
    ("She tries to approve it herself", "override approve --as priya 1"),
    ("The controller raises his own request and tries to approve it",
     'override request --as marc --property P005 --month 2026-10 --item utilities --value -21000 --reason "Accrual estimate" --ticket FIN-2297'),
    (None, "override approve --as marc 2"),
    ("Another approver rejects it", 'override reject --as sofia 2 --reason "Wait for the actual invoice"'),
    ("The operator tries to apply it before anyone approved it", "override apply --as jainam 1"),
    ("The finance controller approves it", "override approve --as marc 1"),
    ("The controller then tries to apply it himself", "override apply --as marc 1"),
    ("The operator applies it", "override apply --as jainam 1"),
    ("What the report shows now", "report --property P004 --month 2026-10"),
    ("A second request with no ticket number", 'override request --as priya --property P002 --month 2026-10 --item utilities --value -20000 --reason "estimate"'),
    ("A request that's approved, then reverted after month-end review",
     'override request --as priya --property P002 --month 2026-10 --item recoveries --value 51000 --reason "CAM true-up" --ticket FIN-2304'),
    (None, "override approve --as sofia 3"),
    (None, "override apply --as jainam 3"),
    (None, 'override revert --as jainam 3 --reason "true-up moved to November"'),
    ("Override register", "override list"),
    ("The accountant sends the capex budget file on time", "intake submit --as li samples/capex_budget_2026-10_v1.csv --on 2026-10-03"),
    ("A second version with mistakes", "intake submit --as li samples/capex_budget_2026-10_v2.csv --on 2026-10-04"),
    ("Sending the first file again", "intake submit --as li samples/capex_budget_2026-10_v1.csv --on 2026-10-04"),
    ("The operator tries to load the file before the owner approved it", "intake load --as jainam 1 samples/capex_budget_2026-10_v1.csv"),
    ("The file owner approves, and the operator loads it", "intake approve --as sofia 1"),
    (None, "intake load --as jainam 1 samples/capex_budget_2026-10_v1.csv"),
    ("Manual file register", "intake list"),
    ("Audit trail (last 12 entries)", "audit show --last 12"),
    ("Check that nobody edited the audit trail", "audit verify"),
    ("Evidence pack for the auditors", "evidence --month 2026-10"),
]

# What happens if someone with database access quietly edits the trail afterwards
TAMPER = """update audit_log set details = '{"reason": "approved by phone"}' where action = 'override.approve.refused'"""


def main():
    db = ROOT / "desk.duckdb"
    for p in (db, ROOT / "desk.duckdb.wal"):
        p.unlink(missing_ok=True)
    for title, cmd in STEPS:
        if title:
            print(f"\n## {title}")
        print(f"$ python -m desk {cmd}")
        out = subprocess.run([sys.executable, "-m", "desk", *shlex.split(cmd)], cwd=ROOT, capture_output=True, text=True)
        sys.stdout.write(out.stdout + out.stderr)

    print("\n## Someone edits an old audit entry directly in the database")
    print(f'$ duckdb desk.duckdb "{TAMPER}"')
    import duckdb
    con = duckdb.connect(str(db))
    con.execute(TAMPER)
    con.close()
    print("$ python -m desk audit verify")
    out = subprocess.run([sys.executable, "-m", "desk", "audit", "verify"], cwd=ROOT, capture_output=True, text=True)
    sys.stdout.write(out.stdout + out.stderr)


if __name__ == "__main__":
    main()
