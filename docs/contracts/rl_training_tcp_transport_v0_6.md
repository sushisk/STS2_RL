# RL–Training asyncio TCP / DTO Contract v0.6

## 1. Goal

`STS2_Training` and `STS2_RL` run as independent processes. Training sends plain JSON DTOs asynchronously over TCP; RL executes them and returns correlated JSON DTO responses.

v0.6 deliberately does **not** claim process-wide exactly-once execution. It provides at-most-once execution for one logical Training session **within one RL `server_epoch`**, plus explicit detection when that epoch changes.

The old synchronous/non-TCP client path is not part of this contract.

## 2. Framing

- TCP, UTF-8, NDJSON.
- One JSON object per frame, terminated by `\n`.
- EOF before `\n` is an incomplete frame and MUST NOT be dispatched.
- API requests are limited by the RL inbound frame limit (default 1 MiB including newline).
- Training bounds response buffering independently (default 64 MiB) and may raise that local receiver limit before replaying the same unresolved request.
- There is no request multiplexing on one logical Training session. Training serializes public API calls.

## 3. Transport handshake and server epoch

Every newly opened TCP stream MUST perform this exact transport-only handshake before sending API traffic:

```json
{"transport_operation":"hello","client_session_id":"<uuid>"}
```

RL replies:

```json
{"transport_operation":"hello","client_session_id":"<same uuid>","server_epoch":"<uuid>"}
```

`server_epoch` is generated when `RLApiServer` starts. All API responses also contain the same `server_epoch`.

A reconnect for an existing Training client MUST observe the same epoch. If the epoch changes, Training MUST invalidate the logical session and MUST NOT retry an unresolved operation into the new epoch. The caller creates a new Training client/session instead.

This makes RL restart semantics explicit: in-memory emulator/session state is not pretended to survive a process restart.

`{"transport_operation":"ping"}` remains an exact transport-only message. Its response is:

```json
{"transport_operation":"pong","server_epoch":"<uuid>"}
```

## 4. API request identity

Every executable API request contains:

```json
{
  "schema_version":"0.6",
  "client_session_id":"<uuid>",
  "request_seq":1,
  "request_id":"<client_session_id>:1",
  "operation":"start_instance"
}
```

Rules:

1. `request_seq` is a positive integer.
2. `request_id` is exactly `<client_session_id>:<request_seq>`.
3. A new session starts at sequence 1.
4. Training does not advance the sequence until it observes a definitive API response for the current sequence.
5. RL accepts a new executable request only when `request_seq == last_seq + 1` for that session.
6. Repeating the current `last_seq` with identical payload replays the cached terminal response verbatim and MUST NOT execute the operation again.
7. Reusing the current sequence with different content is rejected with `session_sequence_conflict`.
8. Skipping forward or replaying an older-than-last sequence is rejected with `session_sequence_gap`; it is never interpreted as a new command.

RL therefore stores only the most recent executable request digest and terminal response per logical session. Memory is O(number of sessions), not O(number of requests).

## 5. Session retention and capacity

RL does not evict a live session record during an epoch because eviction would make an old retry indistinguishable from a new session and could re-execute a command.

The server instead has a bounded session capacity (default 4096). When capacity is exhausted, creation of another session fails closed with `session_capacity_exceeded`; existing sessions are not evicted to make room.

A future protocol may add an acknowledged session-retirement operation. v0.6 intentionally does not invent one without a safe lost-response story.

## 6. Instance ownership

A logical Training session owns at most one active RL instance.

- `start_instance` is rejected while that session already owns an active instance.
- Every instance-scoped request must target the active instance owned by the same `client_session_id`.
- Another Training session cannot operate or close that instance merely by learning its `instance_id`.
- Successful `close_instance` removes the instance.
- If `instance.close()` faults after partial teardown, RL returns a terminal `faulted` response and still quarantines/removes the instance. Training clears its local active-instance state after receiving that correlated fault.

## 7. Completion-uncertain recovery

A timeout, cancellation, connection loss, malformed response, or local response-size overflow can happen after RL observed the request.

Training therefore keeps the exact serialized DTO for the unresolved `request_seq` and fails closed: no fresh API operation may use the next sequence until the unresolved request is recovered.

Recovery within the same epoch is:

1. reconnect;
2. perform `hello` and verify the same `server_epoch`;
3. resend the exact same serialized request with the same session id, sequence, request id, and payload;
4. RL replays the terminal response if the operation already completed, or executes it once if the original frame never reached the API dispatcher.

This rule applies to **all API requests**, not only state-changing operations, because sequence advancement itself is part of the protocol.

If reconnect reports a different epoch, recovery is impossible in-band. Training marks the session invalid and the caller starts a new session.

## 8. Response correlation

Every API response contains:

- `schema_version = "0.6"`
- `server_epoch`
- `client_session_id`
- `request_seq`
- `request_id`
- `operation`
- `status`

For instance-scoped requests, `instance_id` must match the request. Training treats a correlation/protocol mismatch as completion-uncertain, invalidates the TCP stream, and does not advance the sequence.

`rejected` and `faulted` are definitive API responses and consume the sequence.

## 9. Execution model

The asyncio TCP event loop never executes the synchronous emulator handler directly. Handler work runs off-loop and is globally serialized to preserve the current emulator execution model. Parallel same-session execution and multiplexing are outside v0.6.

A process-level forced-shutdown policy for a permanently stuck synchronous emulator call remains an operational concern outside this wire contract.

## 10. Security

The default server binds to loopback and provides no TLS or authentication. Do not expose it to an untrusted network without a separate security layer.
