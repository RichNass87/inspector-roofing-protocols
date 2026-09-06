"""macOS Keychain access.

Credentials live in the login Keychain and are read on demand by shelling out
to /usr/bin/security. Nothing is cached to disk. Values are never logged.
"""

from __future__ import annotations

import subprocess
from typing import Optional

SECURITY = "/usr/bin/security"


class KeychainError(RuntimeError):
    """A Keychain item was missing or could not be read."""


def read_secret(service: str, account: Optional[str] = None) -> str:
    """Return the password for a generic Keychain item.

    Raises KeychainError with a message naming the item, never its value, so a
    failure is safe to print or paste into a bug report.
    """
    cmd = [SECURITY, "find-generic-password", "-s", service, "-w"]
    if account:
        cmd[2:2] = ["-a", account]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError as exc:  # pragma: no cover - macOS only
        raise KeychainError(
            "/usr/bin/security not found. cleanuprtx runs on macOS only."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise KeychainError(
            f"Keychain read for {service!r} timed out. Unlock the login Keychain "
            "and try again."
        ) from exc

    if result.returncode != 0:
        raise KeychainError(
            f"No Keychain item named {service!r}"
            + (f" for account {account!r}" if account else "")
            + ". Run 'cleanuprtx doctor' to see which items are expected."
        )

    secret = result.stdout.strip()
    if not secret:
        raise KeychainError(f"Keychain item {service!r} is present but empty.")
    return secret


def has_secret(service: str, account: Optional[str] = None) -> bool:
    """True if the item exists and is readable, without returning its value."""
    try:
        read_secret(service, account)
    except KeychainError:
        return False
    return True
