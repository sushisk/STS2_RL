# RL–Training asyncio TCP Transport Contract v0.5

## 1. Purpose

This document defines how the existing RL–Training Communication DTO v0.5 is carried between independently started `STS2_RL` and `STS2_Training` processes.

The DTO contract remains the source of truth for API request/response fields and semantics. TCP adds framing and transport-only framing failures; it does not add another API envelope or redefine API lifecycle semantics.

## 2. Framing

- Transport: TCP.
- Encoding: UTF-8.
- Framing: newline-delimited JSON (NDJSON), one JSON object per line.
- One request DTO occupies exactly one frame.
- One response DTO occupies exactly one frame.
- A frame is complete only when its trailing newline delimiter has been received. If EOF arrives first, the partial bytes MUST NOT be dispatched as an API request.
- The top-level JSON value for API traffic MUST be an object.
- The JSON object is the API v0.5 DTO itself. It is not wrapped in a transport-specific `payload` or `data` field.
- The current default inbound request-frame limit is 1 MiB, including the trailing newline.
- An oversized inbound request is rejected at the transport layer.
- The RL server does not apply the 1 MiB request-frame limit to outbound response frames.
- A receiver MUST still bound response buffering independently. `STS2_Training.TcpConnection` defaults `max_response_bytes` to 64 MiB and may be configured higher when the API payload requires it.

Example API request frame:

```json
{"schema_version":"0.5","request_id":"req-123","operation":"get_decision","instance_id":"inst-001","branch_id":"root"}
```

## 3. Request/response correlation

For API traffic, the correlation rules from `rl_training_dto_documentation_v0_5.md` apply unchanged.

In particular:

- `schema_version` MUST be supported by RL.
- `request_id` in the response MUST equal the request `request_id`.
- `operation` in the response MUST equal the request `operation`.
- For instance-targeting operations, response `instance_id` MUST equal request `instance_id`.
- `rejected` and `faulted` are normal API DTO responses, not transport framing failures.

A TCP connection may carry multiple request/response pairs. Multiplexing is not defined by this transport-framing contract.

Timeout, cancellation, and a local response-size-limit failure can occur after RL has observed or completed a request. Training therefore treats such failures as completion-uncertain and discards the connection. If a caller retries the same logical request, it must preserve the same serialized payload and `request_id`; changing a local `max_response_bytes` value does not change the logical request. Automatic retry/reconciliation remains outside this framing contract.

Replay retention follows API object lifetimes rather than server lifetime. While an instance is active, its instance-scoped `RequestLedger` retains completed responses for that instance. The `start_instance` replay record is retained while the instance created by that start remains active and is discarded when that instance closes, so repeated start/close cycles do not make the server-wide pre-instance ledger grow monotonically. A successful `close_instance` keeps only a bounded server-wide tombstone for the close request/response (default: the most recent 1024 closed instances); the closed instance's full request history is released. If a close tombstone has already been evicted, the caller must use external reconciliation and MUST NOT convert the same completion-uncertain logical operation into a fresh `request_id` merely to make progress.

## 4. Transport-only messages

The following exact JSON object is reserved for a transport-path responsiveness check and is outside API DTO v0.5:

```json
{"transport_operation":"ping"}
```

The server replies:

```json
{"transport_operation":"pong"}
```

Only the exact one-field object above is a transport ping. An API DTO that contains an additional field named `transport_operation` is still API traffic and MUST be forwarded to `RLApiServer.handle_request` unchanged.

`ping` MUST NOT be forwarded to `RLApiServer.handle_request` and MUST NOT initialize the Emulator. Synchronous API handler calls are executed off the asyncio event loop and remain globally serialized, so a handler running on another connection does not by itself prevent `ping` from being processed. A successful `pong` does not guarantee that an API operation will complete within any particular timeout.

## 5. Transport framing errors

If a frame cannot be interpreted as a valid JSON object at the transport layer, RL may return a transport-only error object such as:

```json
{"transport_error":"invalid_json","error":"..."}
```

or:

```json
{"transport_error":"invalid_message","error":"top-level JSON value must be an object"}
```

An oversized inbound request may return:

```json
{"transport_error":"message_too_large","direction":"request","max_message_bytes":1048576}
```

These objects describe failures to obtain an API DTO request frame and are not API DTO responses. Once a valid JSON object is forwarded to `RLApiServer`, API validation and execution semantics remain those of the existing DTO contract.

A receiver-side `max_response_bytes` failure is different: the request may already have executed, so Training reports it as completion-uncertain and invalidates the stream instead of pretending the API operation was rejected.

## 6. Process boundary

Recommended local startup:

```bash
python -m API.tcp_server --host 127.0.0.1 --port 8765
```

`STS2_Training` connects independently to the configured host/port. RL does not need to be imported into the Training process.

The current transport provides no authentication or TLS and defaults to loopback. It should not be exposed to an untrusted network without a separate security layer.

## 7. Compatibility rule

TCP framing does not change API DTO semantics. Any future DTO revision should continue to be selected by the DTO `schema_version`; a separate transport schema version is not introduced by v0.5.
