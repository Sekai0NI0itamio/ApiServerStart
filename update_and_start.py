#!/usr/bin/env python3
"""
CLI helper that runs the same workflow as the FastAPI endpoint.
Useful for debugging locally or triggering the flow without hitting HTTP.
"""
from __future__ import annotations

import sys

from server_runner import run_start_server_flow


def _mask(token: str) -> str:
    if len(token) <= 12:
        return token
    return f"{token[:6]}...{token[-6:]}"


def main() -> None:
    try:
        result = run_start_server_flow()
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)

    print("Start server flow completed successfully.")
    print(f"JWT (masked): {_mask(result.token)}")
    print(f"Log file: {result.log_path}\n")

    print("---- initsend stdout ----")
    print(result.initsend.stdout or "<empty>")
    print("---- initsend stderr ----")
    print(result.initsend.stderr or "<empty>")
    print()

    print("---- startserver stdout ----")
    print(result.startserver.stdout or "<empty>")
    print("---- startserver stderr ----")
    print(result.startserver.stderr or "<empty>")


if __name__ == "__main__":
    main()