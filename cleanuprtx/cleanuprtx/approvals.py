"""Approval ledger.

An audit proposes repairs, each repair is approved individually by ID, and
only approved repairs are ever applied. Approvals persist on disk so approve
and apply can be separate runs. Writes are atomic and the file is 0600.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

LEDGER_DIR = Path.home() / ".cleanuprtx"
LEDGER_PATH = LEDGER_DIR / "approvals.json"

PENDING = "pending"
APPROVED = "approved"
APPLIED = "applied"
REJECTED = "rejected"
STALE = "stale"       # target vanished or no longer matches; terminal
FAILED = "failed"     # last apply attempt failed; still approved, retried next run

TERMINAL = {APPLIED, REJECTED, STALE}


class LedgerError(RuntimeError):
    pass


@dataclass
class Repair:
    """One proposed change, tracked from proposal through to application."""

    repair_id: str
    site: str
    kind: str
    rule: str
    page_id: int
    page_url: str
    page_title: str
    message: str
    block_index: int
    target: Dict[str, Any]
    patch: Dict[str, Any]
    state: str = PENDING
    proposed_at: str = ""
    decided_at: str = ""
    applied_at: str = ""
    result: str = ""
    attempts: int = 0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def make_repair_id(site: str, kind: str, page_id: int, rule: str,
                   target_key: str, patch: Dict[str, Any]) -> str:
    """Stable short ID: same defect on the same node always hashes the same,
    two identical defects on different nodes never collide."""
    digest = hashlib.sha256(
        json.dumps([site, kind, page_id, rule, target_key, patch], sort_keys=True).encode("utf-8")
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
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            backup = self.path.with_suffix(".corrupt")
            try:
                os.replace(self.path, backup)
            except OSError:
                pass
            raise LedgerError(
                f"{self.path} is unreadable ({exc}); moved to {backup}. Re-run "
                "'cleanuprtx audit --propose' to rebuild it."
            ) from exc
        known = {f.name for f in Repair.__dataclass_fields__.values()}
        for key, value in (data.get("repairs") or {}).items():
            clean = {k: v for k, v in value.items() if k in known}
            try:
                self.repairs[key] = Repair(**clean)
            except TypeError:
                continue   # a row from an incompatible older version

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        payload = {"updated_at": _now(),
                   "repairs": {k: asdict(v) for k, v in self.repairs.items()}}
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".approvals-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def propose(self, site: str, finding: Any) -> Optional[Repair]:
        """Record an auto-fixable finding. Existing decisions are kept."""
        if not finding.is_auto_fixable:
            return None
        target = finding.target.to_dict()
        repair_id = make_repair_id(site, finding.kind, finding.page_id, finding.rule,
                                   finding.target.key(), finding.patch)
        existing = self.repairs.get(repair_id)
        if existing is not None:
            existing.block_index = finding.block_index   # may shift between renders
            existing.target = target
            return existing
        repair = Repair(
            repair_id=repair_id, site=site, kind=finding.kind, rule=finding.rule,
            page_id=finding.page_id, page_url=finding.page_url, page_title=finding.page_title,
            message=finding.message, block_index=finding.block_index, target=target,
            patch=finding.patch, proposed_at=_now(),
        )
        self.repairs[repair_id] = repair
        return repair

    def decide(self, repair_id: str, approved: bool) -> Repair:
        repair = self.repairs.get(repair_id)
        if repair is None:
            raise KeyError(f"No repair with ID {repair_id!r}")
        if repair.state in TERMINAL:
            raise ValueError(f"Repair {repair_id} is {repair.state} and cannot be changed.")
        repair.state = APPROVED if approved else REJECTED
        repair.decided_at = _now()
        return repair

    def mark_applied(self, repair_id: str, result: str) -> None:
        r = self.repairs[repair_id]
        r.state, r.applied_at, r.result = APPLIED, _now(), result

    def mark_failed(self, repair_id: str, result: str) -> None:
        r = self.repairs[repair_id]
        r.state, r.result, r.attempts = FAILED, result, r.attempts + 1

    def mark_stale(self, repair_id: str, result: str) -> None:
        r = self.repairs[repair_id]
        r.state, r.result = STALE, result

    def in_state(self, state: str, site: Optional[str] = None) -> List[Repair]:
        states = {APPROVED, FAILED} if state == APPROVED else {state}
        return [r for r in self.repairs.values()
                if r.state in states and (site is None or r.site == site)]
