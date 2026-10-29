"""Exports what an auditor asks for when testing the override and manual-file controls."""
import csv
import hashlib
import json
from pathlib import Path

from . import audit


def _dump(con, sql, params, path: Path):
    cur = con.execute(sql, params)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerows(rows)
    return rows


def export(con, month: str, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    ov = _dump(con, """select override_id, property_id, month, line_item, old_value, new_value, reason, ticket, status,
                              requested_by, requested_at, approved_by, approved_at, applied_by, applied_at, validation
                       from overrides where month = ? order by override_id""", [month], out_dir / "overrides.csv")
    fi = _dump(con, """select intake_id, file_name, sha256, rows, amount_total, status, problems, submitted_by, submitted_at,
                              approved_by, approved_at, loaded_by, loaded_at, reconciliation
                       from intake where month = ? order by intake_id""", [month], out_dir / "manual_files.csv")
    log = _dump(con, "select seq, logged_at, actor, action, object, details, prev_hash, hash from audit_log order by seq", [], out_dir / "audit_log.csv")
    refused = [r for r in log if ".refused" in r[3] or r[3].endswith(".failed") or r[3] == "intake.rejected"]
    ok, chain = audit.verify(con)

    applied = [r for r in ov if r[8] in ("applied", "reverted")]
    sod_breaks = [r for r in applied if r[9] == r[11] or r[11] == r[13]]
    lines = [f"# Control evidence: {month}", "",
             f"Audit log: {chain} ({'verified' if ok else 'FAILED'})", "",
             "## Overrides",
             f"- requested: {len(ov)}, applied: {sum(r[8] == 'applied' for r in ov)}, rejected: {sum(r[8] == 'rejected' for r in ov)}, reverted: {sum(r[8] == 'reverted' for r in ov)}",
             f"- applied overrides where requester, approver and operator were not all different people: {len(sod_breaks)}",
             f"- applied overrides without a ticket number: {sum(1 for r in applied if not r[7])}", "",
             "## Manual files",
             f"- submitted: {len(fi)}, loaded: {sum(r[5] == 'loaded' for r in fi)}, rejected at validation: {sum(r[5] == 'rejected' for r in fi)}",
             f"- loaded files that reconciled to their control totals: {sum(1 for r in fi if r[5] == 'loaded' and (r[13] or '').startswith('matched'))} of {sum(r[5] == 'loaded' for r in fi)}", "",
             "## Refused or failed actions (attempts the controls stopped)"]
    def why(details):
        d = json.loads(details)
        text = d.get("reason") or "; ".join(d.get("problems", [])) or details
        return text if len(text) <= 120 else text[:117] + "..."
    lines += [f"- {r[1]:%Y-%m-%d %H:%M} {r[2]} {r[3]} {r[4]}: {why(r[5])}" for r in refused] or ["- none"]
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n")

    hashes = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}" for p in sorted(out_dir.iterdir()) if p.name != "SHA256SUMS"]
    (out_dir / "SHA256SUMS").write_text("\n".join(hashes) + "\n")
    return out_dir, lines
