#!/usr/bin/env python3
"""
Shared logic for retrieving a fresh JWT via the initsend curl call and launching
the StartSession curl call based on serverstart-orig.txt. Used by both the API
service and the CLI helper.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

CALL_TOKEN = "startserver"
INITSEND_FILE = Path("initsend.txt")
SERVERSTART_TEMPLATE_FILE = Path("serverstart-orig.txt")
LOG_FILE = Path("startserver_response.log")


class StartServerError(RuntimeError):
    """Raised when the start-server workflow cannot be completed."""


def _ensure_required_files() -> None:
    missing = [str(p) for p in (INITSEND_FILE, SERVERSTART_TEMPLATE_FILE) if not p.exists()]
    if missing:
        raise StartServerError(f"Missing required file(s): {', '.join(missing)}")


def _read_file_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise StartServerError(f"Unable to read {path}: {exc}") from exc


def normalize_and_extract_curl(text: str, *, source: str) -> str:
    cleaned = re.sub(r'\\\s*\r?\n', ' ', text)
    match = re.search(r'\bcurl\b', cleaned)
    if not match:
        raise StartServerError(f"Unable to find a curl command inside {source}.")
    command = cleaned[match.start():].strip()
    if not command:
        raise StartServerError(f"Curl command extracted from {source} is empty.")
    return command


def run_curl_command(command: str, *, label: str) -> subprocess.CompletedProcess[str]:
    args = shlex.split(command, posix=True)
    if not args:
        raise StartServerError(f"{label} curl command is empty.")
    if args[0] != "curl":
        try:
            idx = args.index("curl")
            args = args[idx:]
        except ValueError as exc:
            raise StartServerError(f"{label} command is missing the 'curl' executable.") from exc
    try:
        return subprocess.run(args, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise StartServerError("The 'curl' executable is not installed or not on PATH.") from exc


def extract_jwt_from_text(text: str) -> str | None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None

    def _find_key(obj: Any, key: str) -> Any:
        if isinstance(obj, dict):
            if key in obj:
                return obj[key]
            for value in obj.values():
                found = _find_key(value, key)
                if found is not None:
                    return found
        elif isinstance(obj, list):
            for item in obj:
                found = _find_key(item, key)
                if found is not None:
                    return found
        return None

    if data is not None:
        token_obj = _find_key(data, "last_active_token")
        if isinstance(token_obj, dict) and "jwt" in token_obj:
            return token_obj["jwt"]

    match = re.search(
        r'"last_active_token"\s*:\s*\{[^}]*"jwt"\s*:\s*"([^"]+)"',
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if match:
        return match.group(1)

    match_loose = re.search(r'"jwt"\s*:\s*"([^"]+)"', text)
    if match_loose:
        return match_loose.group(1)

    return None


def replace_bearer_token(template_text: str, jwt: str, *, source: str) -> str:
    pattern = re.compile(r"(-H\s+)(['\"])authorization:\s*Bearer\s+([^'\"]+)(\2)", re.IGNORECASE)

    def _repl(match: re.Match) -> str:
        prefix, quote = match.group(1), match.group(2)
        return f"{prefix}{quote}authorization: Bearer {jwt}{quote}"

    if pattern.search(template_text):
        return pattern.sub(_repl, template_text, count=1)

    fallback = re.compile(r"(authorization:\s*Bearer\s+)([^'\"]+)", re.IGNORECASE)
    if fallback.search(template_text):
        return fallback.sub(r"\1" + jwt, template_text, count=1)

    raise StartServerError(f"Could not replace Authorization header inside {source}.")


def _append_log(payload: dict[str, Any]) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry = {"timestamp": dt.datetime.utcnow().isoformat() + "Z", **payload}
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        json.dump(entry, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def run_startserver_sequence() -> dict[str, Any]:
    _ensure_required_files()

    initsend_raw = _read_file_text(INITSEND_FILE)
    initsend_command = normalize_and_extract_curl(initsend_raw, source=str(INITSEND_FILE))
    initsend_proc = run_curl_command(initsend_command, label="initsend")
    if initsend_proc.returncode != 0:
        raise StartServerError(
            f"initsend curl exited with code {initsend_proc.returncode}: {initsend_proc.stderr.strip()}"
        )

    jwt = extract_jwt_from_text(initsend_proc.stdout)
    if not jwt:
        raise StartServerError("Unable to extract last_active_token.jwt from initsend response.")

    template_text = _read_file_text(SERVERSTART_TEMPLATE_FILE)
    template_with_token = replace_bearer_token(
        template_text,
        jwt,
        source=str(SERVERSTART_TEMPLATE_FILE),
    )
    startserver_command = normalize_and_extract_curl(
        template_with_token,
        source=str(SERVERSTART_TEMPLATE_FILE),
    )

    startserver_proc = run_curl_command(startserver_command, label="startserver")
    if startserver_proc.returncode != 0:
        raise StartServerError(
            f"startserver curl exited with code {startserver_proc.returncode}: {startserver_proc.stderr.strip()}"
        )

    result: dict[str, Any] = {
        "jwt": jwt,
        "initsend": {
            "command": initsend_command,
            "returncode": initsend_proc.returncode,
            "stdout": initsend_proc.stdout,
            "stderr": initsend_proc.stderr,
        },
        "startserver": {
            "command": startserver_command,
            "returncode": startserver_proc.returncode,
            "stdout": startserver_proc.stdout,
            "stderr": startserver_proc.stderr,
        },
        "log_file": str(LOG_FILE.resolve()),
    }

    try:
        _append_log(result)
    except OSError as exc:
        result["log_error"] = f"Failed to append to log file: {exc}"

    return result