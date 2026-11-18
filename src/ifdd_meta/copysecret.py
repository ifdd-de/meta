"""Copy a secret value between instances without ever showing it.

Taken over from ``.adm_tools/copy_secrets.py`` of the former meta repository:

- a secret file gets the value without a trailing newline; a new file gets mode
  0644 (the containers read it with their own uid; ``secrets/`` itself is 0700),
  an existing file keeps its mode;
- in a ``.env`` file only the line of the variable is replaced, and the variable
  must already be there (protects against a typo in the name);
- a Kestra env file holds ``SECRET_<NAME>`` base64-encoded;
- writes are atomic; a value that is already there is not written again.
"""

from __future__ import annotations

import base64
import os
import re
import tempfile
from pathlib import Path

NEW_SECRET_FILE_MODE = 0o644


class SecretError(RuntimeError):
    """A secret cannot be copied; the message never contains the value."""


def read_env(path: Path, variable: str) -> str:
    if not path.is_file():
        raise SecretError(f"source missing: {path}")
    value = None
    pattern = re.compile(rf"\s*(?:export\s+)?{re.escape(variable)}=(.*)$")
    for line in path.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match:
            value = _unquote(match.group(1).strip())
    if value is None:
        raise SecretError(f"variable {variable} missing in {path}")
    if not value:
        raise SecretError(f"variable {variable} is empty in {path}")
    return value


def write_file(path: Path, value: str, new_mode: int = NEW_SECRET_FILE_MODE) -> bool:
    """Write a secret file; False if it already holds the value."""
    _check_directory(path)
    if path.is_file() and path.read_text(encoding="utf-8").strip() == value:
        return False
    mode = path.stat().st_mode & 0o777 if path.exists() else new_mode
    _write_atomically(path, value, mode)
    return True


def write_env(path: Path, variable: str, value: str, kestra: bool = False) -> bool:
    """Replace the line of ``variable`` in a .env file; False if unchanged."""
    _check_directory(path)
    if kestra:
        variable, value = f"SECRET_{variable}", base64.b64encode(value.encode()).decode()
    if not path.is_file():
        raise SecretError(f"target missing: {path}")
    if not re.fullmatch(r"[A-Za-z0-9._~+/=@-]+", value):
        raise SecretError(f"the value for {variable} would need quotes; not written")
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    pattern = re.compile(rf"(\s*(?:export\s+)?{re.escape(variable)}=)(.*?)(\r?\n)?$")
    found = changed = False
    for index, line in enumerate(lines):
        match = pattern.match(line)
        if match:
            found = True
            if _unquote(match.group(2).strip()) != value:
                lines[index] = f"{match.group(1)}{value}{match.group(3) or ''}"
                changed = True
    if not found:
        raise SecretError(f"variable {variable} missing in {path}")
    if changed:
        _write_atomically(path, "".join(lines), path.stat().st_mode & 0o777)
    return changed


def set_env(path: Path, variable: str, value: str, new_mode: int = 0o600) -> None:
    """Set ``variable`` in a .env file: a new file (``new_mode``), the line replaced or added."""
    _check_directory(path)
    if not re.fullmatch(r"[A-Za-z0-9._~+/=@-]+", value):
        raise SecretError(f"the value for {variable} would need quotes; not written")
    if not path.is_file():
        _write_atomically(path, f"{variable}={value}\n", new_mode)
        return
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(rf"^\s*(?:export\s+)?{re.escape(variable)}=", re.MULTILINE)
    if pattern.search(text):
        write_env(path, variable, value)
        return
    separator = "" if not text or text.endswith("\n") else "\n"
    _write_atomically(path, f"{text}{separator}{variable}={value}\n", path.stat().st_mode & 0o777)


def _check_directory(path: Path) -> None:
    if not path.parent.is_dir():
        raise SecretError(f"target directory missing: {path.parent}")


def _unquote(raw: str) -> str:
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    return raw.split(" #", 1)[0].strip()


def replace_file(path: Path, text: str) -> None:
    """Write ``text`` over an existing file atomically, keeping its mode."""
    _write_atomically(path, text, path.stat().st_mode & 0o777)


def _write_atomically(path: Path, text: str, mode: int) -> None:
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
