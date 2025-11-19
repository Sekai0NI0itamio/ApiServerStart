#!/usr/bin/env python3
"""
Core logic shared by the FastAPI service and the CLI helper.

It pulls the curl command from initsend.txt, executes it to obtain a fresh
last_active_token.jwt, injects that JWT into serverstart-orig.txt (without
writing any backups), executes the resulting curl, and logs the whole exchange.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

INITSEND_FILE = Path(os.getenv("INITSEND_FILE", "initsend.txt"))
START_TEMPLATE_FILE = Path(os.getenv("START_TEMPLATE_FILE", "serverstart-orig.txt"))
LOG_DIR = Path(os.getenv("STARTSERVER_LOG_DIR", "logs"))

LINE_CONTINUATION_PATTERN = re.compile(r'\\\s*\r?\n')
AUTH_HEADER_PATTERN = re.compile(
    r"(-H\s+['\"]authorization:\s*Bearer\s*)([^'\"]+)(['\"])",
    flags=re.IGNORECASE,
)
AUTH_FALLBACK_PATTERN = re.compile(
    r"(authorization:\s*Bearer\s+)(\S+)",
    flags=re.IGNORECASE,
)


@dataclass
class CurlResult:
    command: str
    returncode: int
    stdout: str
    stderr: str
    executed_at: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "executed_at": self.executed_at,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


@dataclass
class FlowResult:
    token: str
    initsend: CurlResult
    startserver: CurlResult
    log_path: Path


def run_start_server_flow() -> FlowResult:
    _ensure_required_files()

    initsend_cmd = _command_from_file(INITSEND_FILE)
    initsend_result = _execute_curl(initsend_cmd)

    token = _extract_jwt_from_text(initsend_result.stdout)
    if not token:
        raise RuntimeError("Unable to locate last_active_token.jwt in initsend response.")

    startserver_cmd = _build_startserver_command(token)
    startserver_result = _execute_curl(startserver_cmd)

    log_path = _log_flow(token, initsend_result, startserver_result)

    return FlowResult(
        token=token,
        initsend=initsend_result,
        startserver=startserver_result,
        log_path=log_path,
    )


def _ensure_required_files() -> None:
    missing = [str(p) for p in (INITSEND_FILE, START_TEMPLATE_FILE) if not p.is_file()]
    if missing:
        raise FileNotFoundError(f"Missing required file(s): {', '.join(missing)}")


def _command_from_file(path: Path) -> str:
    raw = path.read_text(encoding="utf-8")
    return _normalize_curl_text(raw)


def _normalize_curl_text(raw: str) -> str:
    idx = raw.find("curl")
    if idx == -1:
        raise RuntimeError("Unable to find 'curl' in the provided text.")
    stripped = raw[idx:]
    single_line = LINE_CONTINUATION_PATTERN.sub(" ", stripped)
    return single_line.strip()


def _split_curl_command(command: str) -> list[str]:
    try:
        args = shlex.split(command, posix=True)
    except ValueError as exc:
        raise RuntimeError(f"Unable to parse curl command: {exc}") from exc

    if not args:
        raise RuntimeError("The curl command is empty.")

    if args[0] != "curl":
        try:
            idx = args.index("curl")
        except ValueError as exc:
            raise RuntimeError("Command must include the 'curl' executable.") from exc
        args = args[idx:]

    return args


def _execute_curl(command: str) -> CurlResult:
    args = _split_curl_command(command)
    timestamp = _utc_now()
    try:
        proc = subprocess.run(args, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise RuntimeError("curl executable is not available on PATH.") from exc

    return CurlResult(
        command=" ".join(args),
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        executed_at=timestamp,
    )


def _extract_jwt_from_text(text: str) -> Optional[str]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None

    if data is not None:
        token = _dig_for_key(data, "last_active_token")
        if isinstance(token, dict) and "jwt" in token:
            return token["jwt"]

    match = re.search(
        r'"last_active_token"\s*:\s*\{[^}]*"jwt"\s*:\s*"([^"]+)"',
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if match:
        return match.group(1)

    match = re.search(r'"jwt"\s*:\s*"([^"]+)"', text)
    if match:
        return match.group(1)

    return None


def _dig_for_key(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for value in obj.values():
            found = _dig_for_key(value, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _dig_for_key(value, key)
            if found is not None:
                return found
    return None


def _build_startserver_command(jwt: str) -> str:
    template_text = START_TEMPLATE_FILE.read_text(encoding="utf-8")
    updated_text = _inject_jwt(template_text, jwt)
    return _normalize_curl_text(updated_text)


def _inject_jwt(text: str, jwt: str) -> str:
    if AUTH_HEADER_PATTERN.search(text):
        return AUTH_HEADER_PATTERN.sub(
            lambda m: f"{m.group(1)}{jwt}{m.group(3)}",
            text,
            count=1,
        )
    if AUTH_FALLBACK_PATTERN.search(text):
        return AUTH_FALLBACK_PATTERN.sub(
            r"\1" + jwt,
            text,
            count=1,
        )
    raise RuntimeError("Authorization header not found inside serverstart template.")


def _log_flow(token: str, init_res: CurlResult, start_res: CurlResult) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = LOG_DIR / f"startserver-{timestamp}.log"
    log_path.write_text(
        "\n".join(
            [
                f"[{timestamp}] startserver run",
                f"TOKEN: {token}",
                "",
                "== initsend ==",
                f"command: {init_res.command}",
                f"returncode: {init_res.returncode}",
                "stdout:",
                init_res.stdout,
                "stderr:",
                init_res.stderr,
                "",
                "== startserver ==",
                f"command: {start_res.command}",
                f"returncode: {start_res.returncode}",
                "stdout:",
                start_res.stdout,
                "stderr:",
                start_res.stderr,
                "",
            ]
        ),
        encoding="utf-8",
    )
    return log_path


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()