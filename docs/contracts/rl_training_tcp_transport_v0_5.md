# RL–Training asyncio TCP Transport Contract v0.5

## 1. Purpose

This document defines how the existing RL–Training Communication DTO v0.5 is carried between independently started `STS2_RL` and `STS2_Training` processes.

The DTO contract remains the source of truth for request/response fields and semantics. TCP adds framing and connection behavior only; it does not add another API envelope.

## 2. Framing

- Transport: TCP.
- Encoding: UTF-8.
- Framing: newline-delimited JSON (NDJSON), one JSON object per line.
- One request DTO occupies exactly one frame.
- One response DTO occupies exactly one frame.
- The top-level JSON value for API traffic MUST be an object.
- The JSON object is the API v0.5 DTO itself. It is not wrapped in a transport-specific `payload` or `data` field.
- The current default maximum frame size is 1 MiB, including the trailing newline.

Example API request frame:

```json
{"schema_version":"0.5","request_id":"req-001","operation":"get_decision","instance_id":"inst-001","branch_id":"root"}
```

## 3. Request/response correlation

For API traffic, all correlation rules from `rl_training_dto_documentation_v0_5.md` apply unchanged.

In particular:

- `schema_version` MUST be supported by RL.
- `request_id` in the response MUST equal the request `request_id`.
- `operation` in the response MUST equal the request `operation`.
- For instance-targeting operations, response `instance_id` MUST equal request `instance_id`.
- `rejected` and `faulted` are normal API DTO responses, not transport failures.

A TCP connection is persistent and may carry multiple request/response pairs. The current Training transport serializes exchanges so there is at most one outstanding API request per connection. Responses are therefore consumed in request order; there is no multiplexing identifier beyond the DTO `request_id`.

## 4. Transport-only messages

The following message is reserved for transport liveness checks and is outside API DTO v0.5:

```json
{"transport_operation":"ping"}
```

The server replies:

```json
{"transport_operation":"pong"}
```

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

An oversized frame may return:

```json
{"transport_error":"message_too_large","max_message_bytes":1048576}
```

These objects are not API DTO responses because no valid API request DTO was accepted. Once a valid JSON object is forwarded to `RLApiServer`, API validation failures are represented by the normal DTO `status="rejected"` response.

## 6. Timeout and reconnect behavior

Training owns request timeouts. If a TCP request times out or the stream becomes unusable, Training MUST discard that connection before issuing another API request. This prevents a late response from a timed-out request from being mistaken for a later response.

The next call may establish a fresh TCP connection and continue with a new DTO `request_id`.

## 7. Process boundary

Recommended local startup:

```bash
python -m API.tcp_server --host 127.0.0.1 --port 8765
```

`STS2_Training` connects independently to the configured host/port. RL does not need to be imported into the Training process.

The current transport provides no authentication or TLS and defaults to loopback. It should not be exposed to an untrusted network without a separate security layer.

## 8. Compatibility rule

TCP framing does not change DTO semantics. Any future DTO revision should continue to be selected by the DTO `schema_version`; a separate transport schema version is not introduced by v0.5.
