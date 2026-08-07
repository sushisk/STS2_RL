"""Shared API fault-response construction for RL transports."""

from __future__ import annotations

from typing import Any

from API.dto import FAULT_EMULATOR_ERROR, SCHEMA_VERSION, STATUS_FAULTED


def fault_response(payload: Any, exc: BaseException) -> dict:
    request = payload if isinstance(payload, dict) else {}
    response = {
        "schema_version": SCHEMA_VERSION,
        "request_id": request.get("request_id"),
        "operation": request.get("operation"),
        "status": STATUS_FAULTED,
        "error": f"{type(exc).__name__}: {exc}",
        "fault_kind": FAULT_EMULATOR_ERROR,
    }
    if request.get("instance_id") is not None:
        response["instance_id"] = request["instance_id"]
    return response
