#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator

from server_runner import run_start_server_flow

EXPOSE_FULL_JWT = os.getenv("STARTSERVER_EXPOSE_FULL_JWT", "false").lower() in {
    "1",
    "true",
    "yes",
}

app = FastAPI(
    title="Start Server Relay",
    version="1.0.0",
    description=(
        "Simple unauthenticated API that refreshes the Clerk session via initsend.txt, "
        "injects the new JWT into the serverstart template, and calls StartSession."
    ),
)


class TriggerRequest(BaseModel):
    call: str = Field(..., description="Must be 'startserver'.")

    @field_validator("call")
    @classmethod
    def validate_call(cls, value: str) -> str:
        if value.lower() != "startserver":
            raise ValueError("call must be 'startserver'")
        return "startserver"


class CurlPayload(BaseModel):
    executed_at: str
    returncode: int
    stdout: str
    stderr: str


class TriggerResponse(BaseModel):
    status: str
    call: str
    token_preview: str
    token: str | None = Field(
        None,
        description=(
            "Full JWT (only when STARTSERVER_EXPOSE_FULL_JWT=true). "
            "Otherwise None for safety."
        ),
    )
    log_path: str
    initsend: CurlPayload
    startserver: CurlPayload


def _mask(token: str) -> str:
    if len(token) <= 12:
        return token
    return f"{token[:6]}...{token[-6:]}"


@app.get("/", tags=["meta"])
def root() -> dict[str, str]:
    return {
        "message": "Send POST /trigger with {'call': 'startserver'} to run the workflow.",
        "health": "/healthz",
    }


@app.get("/healthz", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/trigger", response_model=TriggerResponse, tags=["startserver"])
async def trigger(payload: TriggerRequest) -> TriggerResponse:
    try:
        result = await asyncio.to_thread(run_start_server_flow)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Unhandled error: {exc}") from exc

    response_status = "ok" if result.startserver.returncode == 0 else "error"

    return TriggerResponse(
        status=response_status,
        call=payload.call,
        token_preview=_mask(result.token),
        token=result.token if EXPOSE_FULL_JWT else None,
        log_path=str(result.log_path),
        initsend=CurlPayload(**result.initsend.to_payload()),
        startserver=CurlPayload(**result.startserver.to_payload()),
    )