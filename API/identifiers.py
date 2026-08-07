"""Session sequencing for the RL/Training Communication contract v0.6."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Optional

from API.validation import RequestRejected
from API.dto import FAULT_SESSION_SEQUENCE_CONFLICT, FAULT_SESSION_SEQUENCE_GAP


def _payload_digest(payload: dict) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class SessionLedger:
    """Keep only the most recent executable request for one logical client session.

    Training serializes API calls, so at most one sequence number may be unresolved.
    The server therefore needs constant memory per client session instead of retaining
    every request/response for an instance lifetime.
    """

    last_seq: int = 0
    last_digest: str | None = None
    last_response: Optional[dict] = None
    active_instance_id: str | None = None

    def begin(self, payload: dict) -> Optional[dict]:
        seq = payload["request_seq"]
        digest = _payload_digest(payload)

        if seq == self.last_seq:
            if digest != self.last_digest:
                raise RequestRejected(
                    f"request_seq {seq} reused with different content",
                    fault_kind=FAULT_SESSION_SEQUENCE_CONFLICT,
                )
            if self.last_response is None:
                raise RequestRejected(
                    f"request_seq {seq} is already in flight",
                    fault_kind=FAULT_SESSION_SEQUENCE_CONFLICT,
                )
            return self.last_response

        expected = self.last_seq + 1
        if seq != expected:
            raise RequestRejected(
                f"request_seq must be {expected}, got {seq}",
                fault_kind=FAULT_SESSION_SEQUENCE_GAP,
            )

        self.last_seq = seq
        self.last_digest = digest
        self.last_response = None
        return None

    def complete(self, response: dict) -> None:
        self.last_response = response
