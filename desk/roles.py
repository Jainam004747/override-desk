"""Separation of duties.

The rules this enforces:
  * only a requester can raise an override, only an approver can approve it,
    and only an operator can apply it
  * nobody approves their own request, and the person who applies it can't be the
    person who approved it
  * a manual file is approved by its named owner, who can't also be the submitter

Every refusal is written to the audit log too, so attempted bypasses show up in evidence.
"""
from pathlib import Path

import yaml


class NotAllowed(Exception):
    pass


def load_roles(config_dir: Path):
    return yaml.safe_load((config_dir / "roles.yml").read_text())["users"]


def require(roles, user, role):
    if role not in roles.get(user, []):
        raise NotAllowed(f"{user} does not have the {role} role")


def different(a_user, b_user, what):
    if a_user == b_user:
        raise NotAllowed(f"{a_user} cannot {what} (separation of duties)")
