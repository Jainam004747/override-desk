"""Append-only audit log with a hash chain.

Every entry stores the hash of the entry before it, and its own hash covers its content plus
that previous hash. Editing or deleting any old entry changes its hash, which no longer
matches what the next entry recorded, so verify() points at the first broken row.
"""
import hashlib
import json
from datetime import datetime, timezone

GENESIS = "0" * 64


def _digest(seq, at, actor, action, obj, details, prev_hash):
    payload = json.dumps([seq, at, actor, action, obj, details, prev_hash], separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def record(con, actor, action, obj, details=None):
    last = con.execute("select seq, hash from audit_log order by seq desc limit 1").fetchone()
    seq, prev = (last[0] + 1, last[1]) if last else (1, GENESIS)
    at = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="microseconds")
    details_json = json.dumps(details or {}, sort_keys=True, default=str)
    h = _digest(seq, at, actor, action, obj, details_json, prev)
    con.execute("insert into audit_log values (?, ?::timestamp, ?, ?, ?, ?, ?, ?)",
                [seq, at, actor, action, obj, details_json, prev, h])
    return seq


def verify(con):
    """Returns (ok, message)."""
    prev = GENESIS
    rows = con.execute("""select seq, strftime(logged_at, '%Y-%m-%dT%H:%M:%S.%f'), actor, action, object, details, prev_hash, hash
                          from audit_log order by seq""").fetchall()
    for expected_seq, (seq, at, actor, action, obj, details, prev_hash, h) in enumerate(rows, start=1):
        if seq != expected_seq:
            return False, f"entry {expected_seq} is missing (found {seq})"
        if prev_hash != prev:
            return False, f"entry {seq}: previous-hash link broken"
        if _digest(seq, at, actor, action, obj, details, prev_hash) != h:
            return False, f"entry {seq} ({action} by {actor}) was changed after it was written"
        prev = h
    return True, f"{len(rows)} entries, chain intact"
