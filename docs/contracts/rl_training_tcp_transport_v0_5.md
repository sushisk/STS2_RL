# RL–Training asyncio TCP Transport Contract v0.5

## 1. Purpose

This document defines how the existing RL–Training Communication DTO v0.5 is carried between independently started `STS2_RL` and `STS2_Training` processes.

The DTO contract remains the source of truth for API request/response fields and semantics. TCP adds framing and transport-only failure behavior; it does not add another API envelope.

## 2. Framing

- Transport: TCP.
- Encoding: UTF-8.
- Framing: newline-delimited JSON (NDJSON), one JSON object per line.
- One request DTO occupies exactly one frame.
- One response DTO occupies exactly one frame.
- The top-level JSON value for API traffic MUST be an object.
- The JSON object is the API v0.5 DTO itself. It is not wrapped in a transport-specific `payload` or `data` field.
- The current default inbound request-frame limit is 1 MiB, including the trailing newline.
- An oversized inbound request is rejected at the transport layer.

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
- `rejected` and `faulted` are normal API DTO responses, not transport failures.

A TCP connection may carry multiple request/response pairs. Multiplexing and cross-connection retry/replay semantics are not defined by this transport-framing PR.

## 4. Transport-only messages

The following exact JSON object is reserved for transport liveness checks and is outside API DTO v0.5:

```json
{"transport_operation":"ping"}
```

The server replies:

```json
{"transport_operation":"pong"}
```

Only the exact one-field object above is a transport ping. An API DTO that contains an additional field named `transport_operation` is still API traffic and MUST be forwarded to `RLApiServer.handle_request` unchanged.

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

Once a valid JSON object is forwarded to `RLApiServer`, API validation failures are represented by the normal DTO `status="rejected"` response.

## 6. Timeout and connection handling

Training owns TCP request timeouts. A socket timeout or task cancellation is a transport failure and does not synthesize an API DTO response.

If a client abandons an exchange after sending has started, it SHOULD discard that connection before issuing another request so a late response cannot be mistaken for a later response. The exact retry/reconciliation semantics for ambiguous completion, especially for non-idempotent API operations, are intentionally deferred to a separate contract change.

## 7. Process boundary

Recommended local startup:

```bash
python -m API.tcp_server --host 127.0.0.1 --port 8765
```

`STS2_Training` connects independently to the configured host/port. RL does not need to be imported into the Training process.

The current transport provides no authentication or TLS and defaults to loopback. It should not be exposed to an untrusted network without a separate security layer.

## 8. Compatibility rule

TCP framing does not change API DTO semantics. Any future DTO revision should continue to be selected by the DTO `schema_version`; a separate transport schema version is not introduced by v0.5.
