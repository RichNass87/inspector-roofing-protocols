"""Approval ledger.

Mirrors Colten's model: an audit proposes repairs, each repair is approved
individually by ID, and only approved repairs are ever applied. Approvals are
recorded on disk so the approve and apply steps can be separate runs.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

LEDGER_DIR = Path.home() / ".cleanuprtx"
LEDGER_PATH = LEDGER_DIR / "approvals.json"

PENDING = "pending"
APPROVED = "approved"
APPLIED = "applied"
REJECTED = "rejected"


@dataclass
class Repair:
    """One proposed change, tracked from proposal through to application."""

    repair_id: str
    site: str
    rule: str
    page_id: int
    page_url: str
    page_title: str
    message: str
    fix: Dict[str, Any]
    state: str = PENDING
    proposed_at: str = ""
    decided_at: str = ""
    applied_at: str = ""
    result: str = ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def make_repair_id(site: str, page_id: int, rule: str, fix: Dict[str, Any]) -> str:
    """Stable short ID, so re-running an audit does not renumber repairs."""
    digest = hashlib.sha256(
        json.dumps([site, page_id, rule, fix], sort_keys=True).encode("utf-8")
    ).hexdigest()
    return digest[:8]


class Ledger:
    """Persisted set of proposed repairs."""

    def __init__(self, path: Path = LEDGER_PATH) -> None:
        self.path = path
        self.repairs: Dict[str, Repair] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.repairs = {
            k: Repair(**v) for k, v in data.get("repairs", {}).items()
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": _now(),
            "repairs": {k: asdict(v) for k, v in self.repairs.items()},
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self.path.chmod(0o600)

    def propose(self, site: str, finding: Any) -> Optional[Repair]:
        """Record a finding as a proposed repair. Existing decisions are kept."""
        if not finding.is_auto_fixable:
            return None
        repair_id = make_repair_id(site, finding.page_id, finding.rule, finding.fix)
        if repair_id in self.repairs:
            return self.repairs[repair_id]

        repair = Repair(
            repair_id=repair_id,
            site=site,
            rule=finding.rule,
            page_id=finding.page_id,
            page_url=finding.page_url,
            page_title=finding.page_title,
            message=finding.message,
            fix=finding.fix,
            proposed_at=_now(),
        )
        self.repairs[repair_id] = repair
        return repair

    def decide(self, repair_id: str, approved: bool) -> Repair:
        repair = self.repairs.get(repair_id)
        if repair is None:
            raise KeyError(f"No repair with ID {repair_id!r}")
        if repair.state == APPLIED:
            raise ValueError(f"Repair {repair_id} was already applied.")
        repair.state = APPROVED if approved else REJECTED
        repair.decided_at = _now()
        return repair

    def mark_applied(self, repair_id: str, result: str) -> None:
        repair = self.repairs[repair_id]
        repair.state = APPLIED
        repair.applied_at = _now()
        repair.result = result

    def in_state(self, state: str, site: Optional[str] = None) -> List[Repair]:
        return [
            r for r in self.repairs.values()
            if r.state == state and (site is None or r.site == site)
        ]
