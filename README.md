# override-desk

A small tool for the two places where people, not pipelines, change reporting data: **business overrides** (correcting a number the source system got wrong) and **manual files** (budgets and adjustments that arrive as spreadsheets).

Both are risky. A quiet edit in a reporting table can move a dashboard by thousands of dollars with no trace of who did it or why. In a SOC 1 or SOX environment the questions are always the same: who asked for the change, who approved it, was the approver someone else, was it checked before it went live, and can you prove all of that months later?

override-desk answers those questions with code instead of a spreadsheet log.

## What it enforces

**Overrides**

- Three separate roles: a **requester** raises it (with a reason and a ticket number), an **approver** approves it, an **operator** applies it.
- Nobody approves their own request. The person who applies it can't be the approver.
- Before applying, the operator's step checks that the value still exists and hasn't changed since the request. If the source moved, the request has to be raised again.
- The source table is never edited. Applied overrides live in their own table and the reporting view puts them on top, so the original value is always visible and reverting is one command.
- After applying, the month's report total must move by exactly the override amount. If anything else moved, the change is rolled back.

**Manual files** (rules per file type in `config/contracts.yml`)

- File name must follow the agreed pattern. The SHA-256 of the file is recorded and the same file can't be submitted twice.
- Submitted by the due day of the month.
- Exact columns, types that parse, required values, allowed codes, value ranges. Every problem is reported at once, with line numbers, so the sender can fix the file in one go.
- A control file (`.ctl`) with the row count and amount total comes with each file and must match.
- The file's owner approves it, and the owner can't be the person who sent it.
- At load time the file must still have the same hash as when it was approved; it's loaded under its intake id and reconciled again against the control totals.

**Audit trail**

- Every action, including refused ones, goes into an append-only log. Refusals matter: they show the controls stopping something.
- Each entry stores the hash of the one before it. Editing or deleting any entry breaks the chain at that point, and `audit verify` names the entry.
- `evidence --month` exports what an auditor asks for: the override register, the manual file register, the full audit log, a summary of exceptions, and SHA-256 sums of the pack.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python scripts/demo.py        # runs the whole month below from an empty database
pytest -q                     # 12 tests
```

Or step by step:

```bash
python -m desk init
python -m desk override request --as priya --property P004 --month 2026-10 --item repairs \
    --value -9875.40 --reason "Invoice INV-4471 posted twice" --ticket FIN-2291
python -m desk override approve --as marc 1
python -m desk override apply   --as jainam 1
python -m desk report --property P004 --month 2026-10
python -m desk audit verify
python -m desk evidence --month 2026-10
```

Users and roles are in `config/roles.yml` (`--as` stands in for a real login).

## Output

This is the output of `python scripts/demo.py`: a month of activity including the things the controls are there to stop.

#### Load the curated NOI data the reports read

```text
$ python -m desk init
Loaded 72 rows into noi_actuals
```

#### A steward asks to correct a repairs figure that was posted twice

```text
$ python -m desk override request --as priya --property P004 --month 2026-10 --item repairs --value -9875.40 --reason "Invoice INV-4471 posted twice by the property manager" --ticket FIN-2291
Override #1 requested by priya; waiting for approval
```

#### She tries to approve it herself

```text
$ python -m desk override approve --as priya 1
REFUSED: priya does not have the approver role
```

#### The controller raises his own request and tries to approve it

```text
$ python -m desk override request --as marc --property P005 --month 2026-10 --item utilities --value -21000 --reason "Accrual estimate" --ticket FIN-2297
Override #2 requested by marc; waiting for approval
$ python -m desk override approve --as marc 2
REFUSED: marc cannot approve their own override (separation of duties)
```

#### Another approver rejects it

```text
$ python -m desk override reject --as sofia 2 --reason "Wait for the actual invoice"
Override #2 rejected by sofia
```

#### The operator tries to apply it before anyone approved it

```text
$ python -m desk override apply --as jainam 1
REFUSED: override #1 is requested; only approved overrides can be applied
```

#### The finance controller approves it

```text
$ python -m desk override approve --as marc 1
Override #1 approved by marc
```

#### The controller then tries to apply it himself

```text
$ python -m desk override apply --as marc 1
REFUSED: marc does not have the operator role
```

#### The operator applies it

```text
$ python -m desk override apply --as jainam 1
Override #1 applied by jainam
  checks: key exists; base value unchanged since request; approver differs from requester and operator
  report total for the month: 1,321,175.77 -> 1,324,442.04
```

#### What the report shows now

```text
$ python -m desk report --property P004 --month 2026-10
NOI actuals for P004, 2026-10  (what the report shows)
  line item               source    reported
  management_fee      -11,521.38  -11,521.38
  property_tax        -30,369.57  -30,369.57
  recoveries           22,808.94   22,808.94
  rental_income       249,369.59  249,369.59
  repairs             -13,141.67   -9,875.40  <- override #1
  utilities           -32,988.01  -32,988.01
```

#### A second request with no ticket number

```text
$ python -m desk override request --as priya --property P002 --month 2026-10 --item utilities --value -20000 --reason "estimate"
REFUSED: an override needs a reason and a ticket number
```

#### A request that's approved, then reverted after month-end review

```text
$ python -m desk override request --as priya --property P002 --month 2026-10 --item recoveries --value 51000 --reason "CAM true-up" --ticket FIN-2304
Override #3 requested by priya; waiting for approval
$ python -m desk override approve --as sofia 3
Override #3 approved by sofia
$ python -m desk override apply --as jainam 3
Override #3 applied by jainam
  checks: key exists; base value unchanged since request; approver differs from requester and operator
  report total for the month: 1,324,442.04 -> 1,330,819.53
$ python -m desk override revert --as jainam 3 --reason "true-up moved to November"
Override #3 reverted by jainam
```

#### Override register

```text
$ python -m desk override list
#   value                                  old         new  status    requested  approved  applied  ticket
1   P004 2026-10 repairs            -13,141.67   -9,875.40  applied   priya      marc      jainam   FIN-2291
2   P005 2026-10 utilities          -26,116.35  -21,000.00  rejected  marc       sofia     -        FIN-2297
3   P002 2026-10 recoveries          44,622.51   51,000.00  reverted  priya      sofia     jainam   FIN-2304
```

#### The accountant sends the capex budget file on time

```text
$ python -m desk intake submit --as li samples/capex_budget_2026-10_v1.csv --on 2026-10-03
Intake #1 capex_budget_2026-10_v1.csv: VALIDATED
```

#### A second version with mistakes

```text
$ python -m desk intake submit --as li samples/capex_budget_2026-10_v2.csv --on 2026-10-04
Intake #2 capex_budget_2026-10_v2.csv: REJECTED
  - line 3: cost_code 'HVAC2' is not one of ['ROOF', 'HVAC', 'ELEV', 'PARK', 'LOBBY', 'OTHER']
  - line 5: amount -31250.50 is below 0
  - line 7: property_id 'P05' doesn't match P\d{3}
  - control file says amount total 541850.50, file adds up to 479349.50
```

#### Sending the first file again

```text
$ python -m desk intake submit --as li samples/capex_budget_2026-10_v1.csv --on 2026-10-04
Intake #3 capex_budget_2026-10_v1.csv: REJECTED
  - identical file already submitted as intake #1 (validated)
```

#### The operator tries to load the file before the owner approved it

```text
$ python -m desk intake load --as jainam 1 samples/capex_budget_2026-10_v1.csv
REFUSED: intake #1 is validated; it needs the owner's approval first
```

#### The file owner approves, and the operator loads it

```text
$ python -m desk intake approve --as sofia 1
Intake #1 approved by sofia
$ python -m desk intake load --as jainam 1 samples/capex_budget_2026-10_v1.csv
Intake #1 loaded by jainam; reconciliation matched (rows 7/7, total 541850.50/541850.50)
```

#### Manual file register

```text
$ python -m desk intake list
#   file                            status      rows         total  submitted approved loaded  sha256
1   capex_budget_2026-10_v1.csv     loaded         7     541850.50  li        sofia    jainam  b6daecbe9a68
2   capex_budget_2026-10_v2.csv     rejected       7     479349.50  li        -        -       80a078f5a962
3   capex_budget_2026-10_v1.csv     rejected       7     541850.50  li        -        -       b6daecbe9a68
```

#### Audit trail (last 12 entries)

```text
$ python -m desk audit show --last 12
   9  2026-10-29 03:41:54  marc    override.apply.refused    override#1               506f3afe4e
  10  2026-10-29 03:41:54  jainam  override.applied          override#1               6b6ff3301c
  11  2026-10-29 03:41:55  priya   override.request.refused  P002/2026-10/utilities   14d6098b8c
  12  2026-10-29 03:41:55  priya   override.requested        override#3               3ef8141ff5
  13  2026-10-29 03:41:55  sofia   override.approved         override#3               2682e87018
  14  2026-10-29 03:41:55  jainam  override.applied          override#3               d722df1719
  15  2026-10-29 03:41:55  jainam  override.reverted         override#3               91b9af06ad
  16  2026-10-29 03:41:56  li      intake.validated          intake#1                 970390df33
  17  2026-10-29 03:41:56  li      intake.rejected           intake#2                 e99f540016
  18  2026-10-29 03:41:56  li      intake.rejected           intake#3                 c9f88f2929
  19  2026-10-29 03:41:56  sofia   intake.approved           intake#1                 c4fc88d156
  20  2026-10-29 03:41:57  jainam  intake.loaded             intake#1                 f4bb355fca
```

#### Check that nobody edited the audit trail

```text
$ python -m desk audit verify
OK  20 entries, chain intact
```

#### Evidence pack for the auditors

```text
$ python -m desk evidence --month 2026-10
# Control evidence: 2026-10

Audit log: 20 entries, chain intact (verified)
```

#### Overrides

```text
- requested: 3, applied: 1, rejected: 1, reverted: 1
- applied overrides where requester, approver and operator were not all different people: 0
- applied overrides without a ticket number: 0
```

#### Manual files

```text
- submitted: 3, loaded: 1, rejected at validation: 2
- loaded files that reconciled to their control totals: 1 of 1
```

#### Refused or failed actions (attempts the controls stopped)

```text
- 2026-10-29 03:41 priya override.approve.refused override#1: priya does not have the approver role
- 2026-10-29 03:41 marc override.approve.refused override#2: marc cannot approve their own override (separation of duties)
- 2026-10-29 03:41 jainam override.apply.refused override#1: override #1 is requested; only approved overrides can be applied
- 2026-10-29 03:41 marc override.apply.refused override#1: marc does not have the operator role
- 2026-10-29 03:41 priya override.request.refused P002/2026-10/utilities: an override needs a reason and a ticket number
- 2026-10-29 03:41 li intake.rejected intake#2: line 3: cost_code 'HVAC2' is not one of ['ROOF', 'HVAC', 'ELEV', 'PARK', 'LOBBY', 'OTHER']; line 5: amount -31250.50 ...
- 2026-10-29 03:41 li intake.rejected intake#3: identical file already submitted as intake #1 (validated)

Written to evidence/2026-10/: SHA256SUMS, audit_log.csv, manual_files.csv, overrides.csv, summary.md
```

#### Someone edits an old audit entry directly in the database

```text
$ duckdb desk.duckdb "update audit_log set details = '{"reason": "approved by phone"}' where action = 'override.approve.refused'"
$ python -m desk audit verify
BROKEN  entry 3 (override.approve.refused by priya) was changed after it was written
```

The evidence folder:

```text
evidence/2026-10/
  overrides.csv       every override with requester, approver, operator, timestamps and validation notes
  manual_files.csv    every file with its hash, checks, approvals and load reconciliation
  audit_log.csv       the full hash-chained trail
  summary.md          the summary printed above
  SHA256SUMS          hashes of the four files, so the pack itself can be checked later
```

## How it's put together

| File | What it does |
|---|---|
| `desk/db.py` | tables, and the `noi_actuals_final` view the reports read (source + applied overrides) |
| `desk/roles.py` | role checks and the "not the same person" rule |
| `desk/overrides.py` | request, approve, reject, apply, revert |
| `desk/intake.py` | manual file validation, approval, load and reconciliation |
| `desk/audit.py` | append-only log with a SHA-256 chain, and verification |
| `desk/evidence.py` | monthly evidence pack |
| `config/contracts.yml` | the rules for each type of manual file |
| `config/roles.yml` | who can request, approve, operate, submit, own files |

## Decisions

- **Overrides are a layer, not an edit.** Keeping the source value means an override can always be explained ("the system said X, finance approved Y on this date for this reason") and undone.
- **Refused actions are logged.** An empty exceptions list could mean nobody tried anything or that nothing was recorded. Logging refusals tells those apart.
- **The tool validates; people approve.** The operator role can apply and load, but it can't approve anything. Business owners decide what the numbers should be; the data team makes sure the change is applied correctly.
- **Problems are reported all at once.** Rejecting a file for its first error and then its second wastes the sender's morning.
- **DuckDB and YAML.** Easy to run anywhere. In a real setup the same rules would sit in front of a warehouse table, and roles would come from AD groups.

## Limitations

- `--as` is trust-based. A real deployment needs an actual login so the recorded user can't be typed in.
- Someone with direct database access could rebuild the whole audit chain from scratch. Sending each day's last hash to write-once storage (or a ticket) would make that detectable.
- One file type (`capex_budget`) is configured; adding another is a new block in `contracts.yml` plus a target table.
