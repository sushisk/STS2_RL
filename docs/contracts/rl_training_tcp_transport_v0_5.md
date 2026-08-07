# RL–Training asyncio TCP Transport Contract v0.5

## 1. Purpose

This document defines how the existing RL–Training Communication DTO v0.5 is carried between independently started `STS2_RL` and `STS2_Training` processes.

The DTO contract remains the source of truth for API request/response fields and semantics. TCP adds framing, connection lifecycle, and transport-failure behavior only; it does not add another API envelope.

For the TCP path, a socket timeout or cancellation is a transport failure and does **not** synthesize an API DTO response. This transport-specific rule supersedes older wording in the DTO documentation that described timeout as a locally synthesized response.

## 2. Framing

- Transport: TCP.
- Encoding: UTF-8.
- Framing: newline-delimited JSON (NDJSON), one JSON object per line.
- One request DTO occupies exactly one frame.
- One response DTO occupies exactly one frame.
- The top-level JSON value for API traffic MUST be an object.
- The JSON object is the API v0.5 DTO itself. It is not wrapped in a transport-specific `payload` or `data` field.
- The current default **request** frame limit is 1 MiB, including the trailing newline.
- RL rejects an oversized request at the transport layer.
- API response frames are not replaced by a transport size error. A completed non-idempotent operation must keep its actual correlated response observable and replayable; replacing a large success response after execution would make same-ID retry unable to reconcile completion.
- Training therefore reads one complete response frame to its terminating newline without applying the request-frame limit to that response.

Example API request frame:

```json
{"schema_version":"0.5","request_id":"req-550e8400-e29b-41d4-a716-446655440000","operation":"get_decision","instance_id":"inst-001","branch_id":"root"}
```

## 3. Request/response correlation and request identifiers

For API traffic, the correlation rules from `rl_training_dto_documentation_v0_5.md` apply.

In particular:

- `schema_version` MUST be supported by RL.
- `request_id` in the response MUST equal the request `request_id`.
- `operation` in the response MUST equal the request `operation`.
- For instance-targeting operations, response `instance_id` MUST equal request `instance_id`.
- `rejected` and `faulted` are normal API DTO responses, not transport failures.

`request_id` identifies one **logical request**, not one TCP attempt. Training's default generator uses UUID-based IDs so newly created clients and restarted Training processes do not restart from a shared short counter.

A new logical request MUST use a new `request_id`. A retry of the same logical request MUST reuse the same API payload and the same `request_id`; changing only the `request_id` turns it into a different logical request and may execute a non-idempotent operation again.

RL keeps request-ledger state long enough to deduplicate concurrent/in-flight retries and to replay completed responses. For `close_instance`, RL retains only a compact tombstone containing that completed close request/response after the active instance has been removed. Historical Decision/simulation responses from the closed instance are released.

A TCP connection is persistent and may carry multiple request/response pairs. The current Training transport serializes exchanges so there is at most one outstanding API request per connection. Responses are therefore consumed in request order; there is no multiplexing identifier beyond the DTO `request_id`.

## 4. Transport-only messages

The following exact JSON object is reserved for transport liveness checks and is outside API DTO v0.5:

```json
{"transport_operation":"ping"}
```

The server replies:

```json
{"transport_operation":"pong"}
```

Only the exact one-field object above is a transport ping. An API DTO that happens to contain an additional unknown field named `transport_operation` is still API traffic and MUST be forwarded to `RLApiServer.handle_request` unchanged.

`ping` MUST NOT be forwarded to `RLApiServer.handle_request` and MUST NOT initialize the Emulator.

## 5. Transport errors

If a frame cannot be interpreted as a valid JSON object at the transport layer, RL may return a transport-only error object such as:

```json
{"transport_error":"invalid_json","error":"..."}
```

or:

```json
{"transport_error":"invalid_message","error":"top-level JSON value must be an object"}
```

An oversized request may return:

```json
{"transport_error":"message_too_large","direction":"request","max_message_bytes":1048576}
```

Transport-only error objects are not API DTO responses. Training MUST classify them as transport failures before API envelope/correlation validation.

There is no response-direction `message_too_large` substitution in v0.5. If response-size control becomes necessary later, it must be defined at the API/payload level or by a replay-safe transport revision rather than by discarding an already-completed non-idempotent result.

Once a valid JSON object is forwarded to `RLApiServer`, API validation failures are represented by the normal DTO `status="rejected"` response.

## 6. Timeout, cancellation, reconnect, and retry behavior

Training owns TCP request timeouts. A timeout means only that Training did not observe the result before its deadline; it does not prove that RL did not execute the operation.

If a TCP request times out, the stream becomes unusable, or a task is cancelled after sending has started, Training MUST discard that connection before any later exchange. This prevents a late response from the abandoned attempt from being consumed as the response to a later request.

After a protocol/correlation failure such as request_id or operation mismatch, Training MUST also discard the connection because stream alignment can no longer be trusted.

The next TCP attempt may use a fresh connection. Retry semantics are:

- Retrying the **same logical request**: resend the same payload with the same `request_id`.
- Starting a **new logical request**: construct a new payload with a new `request_id`.
- Do not convert an ambiguous completion into a retry merely by calling the high-level operation again with a newly generated ID; for non-idempotent operations such as `start_instance`, `commit_action`, and `emulate_action`, that can execute the operation twice.
- `close_instance` follows the same same-ID retry rule; RL retains the compact completed-close tombstone for replay after instance removal.

## 7. Execution model

The asyncio TCP event loop MUST remain responsive while synchronous RL/Emulator work executes. API handling therefore runs outside the event-loop thread.

Requests targeting the same `instance_id` are serialized so one instance is not mutated concurrently. Requests for different instances may execute concurrently, and transport ping does not wait for a slow API operation on another connection.

## 8. Process boundary

Recommended local startup:

```bash
python -m API.tcp_server --host 127.0.0.1 --port 8765
```

`STS2_Training` connects independently to the configured host/port. RL does not need to be imported into the Training process.

The current transport provides no authentication or TLS and defaults to loopback. It should not be exposed to an untrusted network without a separate security layer.

Because v0.5 does not impose a transport response-size cap, the endpoint is intended for the trusted local RL/Training process boundary described above. Future untrusted-network deployment should add explicit payload/resource controls together with authentication/TLS.

## 9. Compatibility rule

TCP framing does not change API DTO semantics. Any future DTO revision should continue to be selected by the DTO `schema_version`; a separate transport schema version is not introduced by v0.5.
