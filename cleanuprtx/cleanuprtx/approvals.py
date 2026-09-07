"""Approval ledger.

An audit proposes repairs, each repair is approved individually by ID, and
only approved repairs are ever applied. Approvals persist on disk so approve
and apply can be separate runs. Writes are atomic and the file is 0600.
"""

from __future__ import annotations

import fcntl
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
    staged_modified_gmt: str = ""   # the page's modified_gmt when the autosave was staged


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def patch_identity(patch: Dict[str, Any]) -> Dict[str, Any]:
    """The part of a patch that names the defect, not the value it writes.

    Values derived from page metadata (a dateModified fix tracks modified_gmt)
    or from sibling repairs would otherwise renumber a repair on every save.
    """
    op = patch.get("op")
    if op == "set":
        return {"op": "set", "key": patch.get("key")}
    if op == "rename_id":
        return {"op": "rename_id", "old": patch.get("old"), "new": patch.get("new")}
    return dict(patch)


def make_repair_id(site: str, kind: str, page_id: int, rule: str,
                   target_key: str, patch: Dict[str, Any]) -> str:
    """Stable short ID: same defect on the same node always hashes the same,
    two identical defects on different nodes never collide."""
    digest = hashlib.sha256(
        json.dumps([site, kind, page_id, rule, target_key, patch_identity(patch)],
                   sort_keys=True).encode("utf-8")
    ).hexdigest()
    return digest[:8]


def defect_keys(rule: str, target: Any, patch: Optional[Dict[str, Any]] = None) -> set:
    """Identity of a defect independent of the repair id: the rule, what the
    patch changes (when known), and the node - by @id, or by the content
    fingerprint for id-less nodes, whose key() moves when a block or a
    sibling node is inserted ahead of it in post_content."""
    from .jsonld import Target   # jsonld does not import approvals
    if isinstance(target, dict):
        try:
            target = Target.from_dict(target)
        except (TypeError, KeyError, ValueError):
            return set()
    if target is None:
        return set()
    what = json.dumps(patch_identity(patch), sort_keys=True) if isinstance(patch, dict) else ""
    keys = {(rule, what, target.key())}
    node = target.node_id or target.fingerprint
    if node:
        keys.add((rule, what, node))
    return keys


def is_staged(repair: "Repair") -> bool:
    """An applied row whose write was an autosave the owner has not restored:
    the repair is approved and staged, not on the live page."""
    return repair.state == APPLIED and str(repair.result).startswith("autosave:")


def saved_since_staging(repair: "Repair", modified_gmt: str) -> bool:
    """An autosave never changes the parent's modified_gmt, so any change since
    staging is a later save - after which WordPress no longer offers the older
    autosave and the staged repair may no longer fit. Rows staged before
    staged_modified_gmt was recorded fall back to the time of staging."""
    if not modified_gmt:
        return False
    if getattr(repair, "staged_modified_gmt", ""):
        return modified_gmt != repair.staged_modified_gmt
    try:
        modified = datetime.fromisoformat(modified_gmt.replace("Z", "")).replace(tzinfo=timezone.utc)
        return modified > datetime.fromisoformat(repair.applied_at)
    except (ValueError, TypeError):
        return False


class _Lock:
    """Advisory lock; on acquisition the ledger is re-read from disk so the
    holder works from the state it is about to replace."""

    def __init__(self, ledger: "Ledger") -> None:
        self.ledger = ledger
        self.path = ledger.path.with_suffix(".lock")
        self._fd: Optional[int] = None

    def __enter__(self) -> "_Lock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(str(self.path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(self._fd)
            self._fd = None
            raise LedgerError("another cleanuprtx command holds the ledger; wait for it to finish") from exc
        try:
            self.ledger.load()
        except BaseException:
            self.__exit__()
            raise
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None


class Ledger:
    """Persisted set of proposed repairs."""

    def __init__(self, path: Path = LEDGER_PATH) -> None:
        self.path = path
        self.repairs: Dict[str, Repair] = {}
        self.load()

    def load(self) -> None:
        self.repairs = {}
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("repairs") or {}, dict):
                raise ValueError("not a ledger (expected a JSON object with a 'repairs' object)")
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
            if not isinstance(value, dict):
                continue
            clean = {k: v for k, v in value.items() if k in known}
            try:
                self.repairs[key] = Repair(**clean)
            except TypeError:
                continue   # a row from an incompatible older version
        self._migrate_ids()

    def _migrate_ids(self) -> None:
        """v0.2.2 keyed a rename by its old @id only; v0.2.3 keys it by old and
        new. Re-derive every rename row's id from its stored fields and re-key
        it when it differs, so apply finds the row among the fresh findings
        instead of declaring it absorbed. Idempotent for current rows."""
        from .jsonld import Target   # jsonld does not import approvals

        def rank(r: Repair) -> int:
            if is_staged(r):
                return 2                                   # names a staged write; must be carried
            return 0 if r.state in (PENDING, STALE) else 1  # carries a decision

        for old_id, r in list(self.repairs.items()):
            if not isinstance(r.patch, dict) or r.patch.get("op") != "rename_id" \
                    or not isinstance(r.target, dict):
                continue                                   # malformed rows are handled by apply
            try:
                new_id = make_repair_id(r.site, r.kind, r.page_id, r.rule,
                                        Target.from_dict(r.target).key(), r.patch)
            except (TypeError, KeyError, ValueError):
                continue
            if new_id == old_id:
                continue
            other = self.repairs.get(new_id)
            if other is not None and rank(other) >= rank(r):
                del self.repairs[old_id]                   # the newer row already holds the decision
                continue
            del self.repairs[old_id]
            r.repair_id = new_id
            self.repairs[new_id] = r

    def lock(self):
        """Advisory lock so 'audit --propose', 'approve'/'reject' and 'apply'
        cannot interleave; re-reads the file once held."""
        return _Lock(self)

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

    def propose(self, site: str, finding: Any, modified_gmt: str = "") -> Optional[Repair]:
        """Record an auto-fixable finding. Existing decisions are kept.

        modified_gmt is the page's current modified_gmt when known: a staged
        repair whose page was saved since staging lost its autosave and is
        re-opened for a fresh decision rather than silently kept."""
        if not finding.is_auto_fixable:
            return None
        target = finding.target.to_dict()
        repair_id = make_repair_id(site, finding.kind, finding.page_id, finding.rule,
                                   finding.target.key(), finding.patch)
        existing = self.repairs.get(repair_id)
        if existing is not None:
            existing.block_index = finding.block_index   # may shift between renders
            existing.target = target
            existing.message = finding.message
            if existing.state == REJECTED:
                old_patch = existing.patch if isinstance(existing.patch, dict) else {}
                if old_patch.get("expect") != (finding.patch or {}).get("expect"):
                    # The defect itself changed since the rejection: decide again.
                    existing.state, existing.decided_at = PENDING, ""
                    existing.result = "the defect changed since it was rejected; decide again"
                    existing.patch = finding.patch
                return existing                            # otherwise a rejection is final
            if existing.state == APPLIED:
                if str(existing.result).startswith("autosave:"):
                    if saved_since_staging(existing, modified_gmt):
                        # WordPress dropped the autosave when the page was saved
                        # again, and the defect is still there: decide afresh.
                        existing.state, existing.decided_at, existing.applied_at = PENDING, "", ""
                        existing.result = "autosave superseded by a later save; re-approve"
                        existing.attempts, existing.staged_modified_gmt = 0, ""
                        existing.patch = finding.patch
                        return existing
                    # Staged, not yet restored: the live page still shows the defect.
                    # Keep the row; refresh the patch so a derived value is current.
                    existing.patch = finding.patch
                    return existing
                # Restored/absorbed earlier and reported again: a regression.
                existing.state, existing.decided_at, existing.applied_at = PENDING, "", ""
                existing.result = "reported again after being applied; re-approve"
                existing.attempts = 0
                existing.patch = finding.patch
                return existing
            if existing.state == STALE:
                # The defect is back on the live page: ask for a fresh decision.
                existing.state, existing.decided_at, existing.applied_at = PENDING, "", ""
                existing.result, existing.attempts = "", 0
                existing.patch = finding.patch
            elif existing.state in (APPROVED, FAILED) and existing.patch != finding.patch:
                # Same defect, different repair value: the approval was for the old one.
                existing.state, existing.decided_at, existing.applied_at = PENDING, "", ""
                existing.result = "patch changed since approval; re-approve"
                existing.attempts = 0
                existing.patch = finding.patch
            elif existing.state == PENDING:
                existing.patch = finding.patch
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
        if is_staged(repair) and not approved:
            # Staged as an autosave, not live: the owner may still withdraw it. It
            # leaves the carried set; the autosave itself is never deleted by this
            # tool - the next write on the page supersedes it, or WordPress drops
            # it after the owner's next save.
            repair.state, repair.decided_at = REJECTED, _now()
            repair.result = "withdrawn after staging"
            return repair
        if repair.state in TERMINAL:
            raise ValueError(f"Repair {repair_id} is {repair.state} and cannot be changed.")
        repair.state = APPROVED if approved else REJECTED
        repair.decided_at = _now()
        return repair

    def mark_applied(self, repair_id: str, result: str, staged_modified_gmt: str = "") -> None:
        r = self.repairs[repair_id]
        r.state, r.applied_at, r.result = APPLIED, _now(), result
        r.staged_modified_gmt = staged_modified_gmt

    def mark_failed(self, repair_id: str, result: str) -> None:
        r = self.repairs[repair_id]
        r.state, r.result, r.attempts = FAILED, result, r.attempts + 1

    def mark_stale(self, repair_id: str, result: str) -> None:
        r = self.repairs[repair_id]
        r.state, r.result = STALE, result

    def retire_unreported(self, site: str, audited_pages: set, reported_ids: set) -> List[str]:
        """Rows for audited pages that this audit did not reproduce are no
        longer defects: mark them stale (revived if a later audit sees them)."""
        retired = []
        for r in self.repairs.values():
            if (r.site == site and r.state in (PENDING, APPROVED, FAILED)
                    and (r.kind, r.page_id) in audited_pages and r.repair_id not in reported_ids):
                self.mark_stale(r.repair_id, "no longer reported by the audit")
                retired.append(r.repair_id)
        return retired

    def absorb_unreported(self, site: str, audited_pages: set, reported_ids: set,
                          still_defects: set, modified: Dict[Any, str]) -> List[str]:
        """Staged rows (applied as an autosave, not yet restored) on audited pages
        whose page was saved since staging and whose defect this audit no longer
        reports have been absorbed: the owner restored the autosave and
        published, or fixed the page by hand. Mark them so a later regression
        re-proposes them. A staged row still reported under another id, or
        reported but no longer repairable, lost its autosave to that save and is
        retired instead. A page not saved since staging still holds the autosave:
        its rows are left alone. still_defects: defect_keys() of every finding
        with a target, patched or not."""
        done = []
        for r in self.repairs.values():
            if not (r.site == site and is_staged(r) and (r.kind, r.page_id) in audited_pages):
                continue
            if r.repair_id in reported_ids:
                continue
            if not saved_since_staging(r, modified.get((r.kind, r.page_id), "")):
                continue
            if (defect_keys(r.rule, r.target, r.patch) | defect_keys(r.rule, r.target, None)) & still_defects:
                self.mark_stale(r.repair_id, "earlier staged repair no longer fits after a later save; "
                                             "the defect is still reported")
            else:
                self.mark_applied(r.repair_id, "absorbed: no longer reported by the audit after a later save")
            done.append(r.repair_id)
        return done

    def in_state(self, state: str, site: Optional[str] = None) -> List[Repair]:
        states = {APPROVED, FAILED} if state == APPROVED else {state}
        return [r for r in self.repairs.values()
                if r.state in states and (site is None or r.site == site)]
