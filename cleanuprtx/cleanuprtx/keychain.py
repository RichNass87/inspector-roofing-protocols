"""macOS Keychain access.

Credentials live in the login Keychain and are read on demand by shelling out
to /usr/bin/security. Nothing is cached to disk. Values are never logged, and
never placed on a command line where 'ps' could see them.
"""

from __future__ import annotations

import subprocess
from typing import Optional

from .invocation import prog

SECURITY = "/usr/bin/security"


class KeychainError(RuntimeError):
    """A Keychain item was missing or could not be read or written."""


def _run(cmd: list, stdin: Optional[str] = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            cmd, input=stdin, capture_output=True, text=True, timeout=60
        )
    except FileNotFoundError as exc:  # pragma: no cover - macOS only
        raise KeychainError(
            "/usr/bin/security not found. cleanuprtx runs on macOS only."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise KeychainError(
            "Keychain did not answer in time. Unlock the login Keychain "
            "(or click Allow on its prompt) and try again."
        ) from exc


def item_exists(service: str, account: Optional[str] = None) -> bool:
    """True if an item exists. Reads attributes only; the password is never
    decrypted, so this does not trigger the Keychain permission prompt."""
    cmd = [SECURITY, "find-generic-password", "-s", service]
    if account:
        cmd += ["-a", account]
    return _run(cmd).returncode == 0


def read_secret(service: str, account: Optional[str] = None) -> str:
    """Return the password for a generic Keychain item.

    Raises KeychainError with a message naming the item, never its value.
    """
    cmd = [SECURITY, "find-generic-password", "-s", service]
    if account:
        cmd += ["-a", account]
    cmd.append("-w")

    result = _run(cmd)
    if result.returncode != 0:
        # Distinguish "no such item" from "user denied / keychain locked".
        err = (result.stderr or "").lower()
        if "could not be found" in err or result.returncode == 44:
            raise KeychainError(
                f"No Keychain item named {service!r}"
                + (f" for account {account!r}" if account else "")
                + f". Run '{prog()} doctor' to see which items are expected, "
                f"or '{prog()} auth' to create them."
            )
        raise KeychainError(
            f"Keychain refused to release {service!r} (locked, or access was "
            "denied on the prompt). Unlock the login Keychain and try again."
        )

    secret = result.stdout.strip()
    if not secret:
        raise KeychainError(f"Keychain item {service!r} is present but empty.")
    return secret


def store_secret(service: str, value: str, account: Optional[str] = None) -> None:
    """Create or replace a generic Keychain item.

    The value is passed to 'security' through stdin in interactive mode, so it
    never appears in this process's argument list.
    """
    if not value:
        raise KeychainError("Refusing to store an empty secret.")
    if any(ch in value for ch in "\n\r"):
        raise KeychainError("Secret contains a line break; refusing to store it.")

    parts = ["add-generic-password", "-U", "-s", _quote(service)]
    if account:
        parts += ["-a", _quote(account)]
    parts += ["-w", _quote(value)]
    result = _run([SECURITY, "-i"], stdin=" ".join(parts) + "\n")
    if result.returncode != 0:
        # Do not include stderr: in interactive mode it can echo the command.
        raise KeychainError(
            f"Keychain refused to store {service!r} (exit {result.returncode}). "
            "Unlock the login Keychain and try again."
        )


def _quote(value: str) -> str:
    """Quote for security's interactive command parser."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
