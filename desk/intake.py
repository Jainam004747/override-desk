"""Manual file intake: the checks a spreadsheet must pass before it reaches the warehouse.

  1. identity      file name follows the contract's pattern; SHA-256 recorded; same file twice is refused
  2. timing        submitted on or before the due day for its month
  3. format/schema exact columns, types parse, required values present, allowed values, ranges
  4. control file  the .ctl sent with it states row count and amount total; they must match
  5. approval      the file owner approves (and isn't the submitter)
  6. load + rec    loaded under its intake id, then reconciled again against the control totals
"""
import csv
import hashlib
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

from . import audit
from .roles import NotAllowed, different, require


def load_contracts(config_dir: Path):
    return yaml.safe_load((config_dir / "contracts.yml").read_text())


def validate(path: Path, contract: dict, as_of: date):
    """Returns (facts, problems). Never raises for bad content: every problem is reported."""
    problems, facts = [], {"file_name": path.name}
    m = re.fullmatch(contract["file_pattern"], path.name)
    if not m:
        return facts, [f"file name doesn't match {contract['file_pattern']}"]
    facts["month"], facts["version"] = m["month"], int(m["version"])
    facts["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()

    due = date.fromisoformat(f"{facts['month']}-01").replace(day=contract["submit_by_day"])
    if as_of > due:
        problems.append(f"late: due {due}, submitted {as_of}")

    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        rows = list(reader)
    expected = list(contract["columns"])
    if header != expected:
        missing = [c for c in expected if c not in header]
        extra = [c for c in header if c not in expected]
        problems.append("columns don't match contract" + (f"; missing {missing}" if missing else "") + (f"; unexpected {extra}" if extra else ""))
        return facts, problems

    total = Decimal(0)
    for i, row in enumerate(rows, start=2):  # line 1 is the header
        for col, rule in contract["columns"].items():
            v = (row.get(col) or "").strip()
            if not v:
                if rule.get("required"):
                    problems.append(f"line {i}: {col} is empty")
                continue
            if rule["type"] == "decimal":
                try:
                    d = Decimal(v.replace(",", ""))
                except InvalidOperation:
                    problems.append(f"line {i}: {col} '{v}' is not a number")
                    continue
                if "min" in rule and d < rule["min"]:
                    problems.append(f"line {i}: {col} {d} is below {rule['min']}")
                if "max" in rule and d > rule["max"]:
                    problems.append(f"line {i}: {col} {d} is above {rule['max']}")
                if col == "amount":
                    total += d
            if "pattern" in rule and not re.fullmatch(rule["pattern"], v):
                problems.append(f"line {i}: {col} '{v}' doesn't match {rule['pattern']}")
            if "allowed" in rule and v not in rule["allowed"]:
                problems.append(f"line {i}: {col} '{v}' is not one of {rule['allowed']}")
    facts["rows"], facts["amount_total"] = len(rows), total

    if contract.get("control_file"):
        ctl = path.with_suffix(".ctl")
        if not ctl.exists():
            problems.append(f"control file {ctl.name} is missing")
        else:
            declared = dict(line.split("=", 1) for line in ctl.read_text().split() if "=" in line)
            if int(declared.get("row_count", -1)) != len(rows):
                problems.append(f"control file says {declared.get('row_count')} rows, file has {len(rows)}")
            if Decimal(declared.get("amount_total", "-1")) != total:
                problems.append(f"control file says amount total {declared.get('amount_total')}, file adds up to {total}")
    return facts, problems


def submit(con, roles, contracts, user, path: Path, contract_name: str, as_of: date):
    require(roles, user, "submitter")
    contract = contracts[contract_name]
    facts, problems = validate(path, contract, as_of)
    if facts.get("sha256"):
        dup = con.execute("select intake_id, status from intake where sha256 = ? and status <> 'rejected'", [facts["sha256"]]).fetchone()
        if dup:
            problems.append(f"identical file already submitted as intake #{dup[0]} ({dup[1]})")
    status = "rejected" if problems else "validated"
    iid = con.execute("""insert into intake (contract, file_name, sha256, month, version, rows, amount_total, status, problems,
                         submitted_by, submitted_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?::timestamp) returning intake_id""",
                      [contract_name, path.name, facts.get("sha256"), facts.get("month"), facts.get("version"),
                       facts.get("rows"), facts.get("amount_total"), status, "; ".join(problems) or None, user,
                       f"{as_of} 12:00:00"]).fetchone()[0]
    audit.record(con, user, f"intake.{status}", f"intake#{iid}",
                 {"file": path.name, "sha256": facts.get("sha256"), "problems": problems})
    return iid, status, problems


def approve(con, roles, contracts, user, iid):
    row = con.execute("select contract, status, submitted_by from intake where intake_id = ?", [iid]).fetchone()
    try:
        if row is None:
            raise NotAllowed(f"intake #{iid} does not exist")
        contract, status, submitted_by = row
        require(roles, user, "file_owner")
        if contracts[contract]["owner"] != user:
            raise NotAllowed(f"{user} is not the owner of {contract} files")
        different(user, submitted_by, "approve a file they submitted")
        if status != "validated":
            raise NotAllowed(f"intake #{iid} is {status}; only validated files can be approved")
    except NotAllowed as e:
        audit.record(con, user, "intake.approve.refused", f"intake#{iid}", {"reason": str(e)})
        raise
    con.execute("update intake set status = 'approved', approved_by = ?, approved_at = now() where intake_id = ?", [user, iid])
    audit.record(con, user, "intake.approved", f"intake#{iid}")


def load(con, roles, contracts, user, iid, path: Path):
    """Loads an approved file. The bytes must still match the hash taken at submission."""
    require(roles, user, "operator")
    row = con.execute("select status, sha256, rows, amount_total, approved_by from intake where intake_id = ?", [iid]).fetchone()
    status, sha, rows, total, approved_by = row
    if status != "approved":
        raise NotAllowed(f"intake #{iid} is {status}; it needs the owner's approval first")
    if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
        audit.record(con, user, "intake.load.refused", f"intake#{iid}", {"reason": "file changed after approval"})
        raise NotAllowed("file changed after it was approved; submit it again")
    with path.open(newline="", encoding="utf-8-sig") as f:
        data = [[iid, r["property_id"], r["cost_code"], Decimal(r["amount"].replace(",", "")), r.get("comment") or None]
                for r in csv.DictReader(f)]
    con.execute("delete from capex_budget where intake_id = ?", [iid])
    con.executemany("insert into capex_budget values (?, ?, ?, ?, ?)", data)
    got_rows, got_total = con.execute("select count(*), sum(amount) from capex_budget where intake_id = ?", [iid]).fetchone()
    rec = f"rows {got_rows}/{rows}, total {got_total}/{total}"
    ok = got_rows == rows and got_total == total
    con.execute("update intake set status = ?, loaded_by = ?, loaded_at = now(), reconciliation = ? where intake_id = ?",
                ["loaded" if ok else "load_failed", user, ("matched: " if ok else "MISMATCH: ") + rec, iid])
    audit.record(con, user, "intake.loaded" if ok else "intake.load.mismatch", f"intake#{iid}", {"reconciliation": rec})
    return ok, rec
