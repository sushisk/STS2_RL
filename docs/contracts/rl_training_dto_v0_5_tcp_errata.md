# RL–Training DTO v0.5 TCP Errata

This errata is normative for DTO v0.5 when it is carried over the independent-process asyncio TCP transport.

It corrects statements in `rl_training_dto_documentation_v0_5.md` that predate the TCP process boundary.

## 1. `request_id` scope

The DTO document currently describes `request_id` as unique within an instance. That wording is insufficient for `start_instance`, because the request has no `instance_id` yet and RL deduplicates `start_instance` requests in a server-wide pre-instance ledger.

For TCP v0.5:

- `request_id` identifies one logical request.
- A newly created logical request MUST use an ID that is effectively unique for the lifetime of the RL server process, including `start_instance`.
- Training's default generator therefore uses UUID-based request IDs rather than restarting a short per-client counter.
- Retrying the same logical request MUST reuse the same payload and the same `request_id`.
- Reusing the same `request_id` with different content is rejected.

The server-wide uniqueness requirement prevents a restarted Training process or a second client from accidentally replaying an old `start_instance` response.

## 2. Timeout and cancellation are not DTO responses

The DTO document currently mentions timeout as a response that RL or Training may synthesize. The asyncio TCP path does not synthesize an API DTO response for socket timeout or task cancellation.

For TCP v0.5:

- timeout means Training did not observe completion before its deadline; RL may or may not have executed the operation;
- cancellation after sending has started has the same ambiguous-completion property;
- Training discards the TCP stream after timeout/cancellation before issuing another exchange;
- a later retry of the same logical request uses the same payload and `request_id` on a fresh connection;
- transport-only error objects such as an oversized-request `{"transport_error":"message_too_large", ...}` are raised as transport failures before API DTO validation.

Normal API failures remain DTO responses with `status="rejected"` or `status="faulted"`.

## 3. `close_instance` retry and retention

A lost `close_instance` response is also ambiguous completion. After removing the active instance, RL retains a compact tombstone containing only that completed close request/response so the exact same close request can be replayed successfully on a fresh connection.

RL does **not** retain the closed instance's full `RequestLedger`; historical `get_decision`, `commit_action`, `emulate_action`, and other responses are released when the instance closes.

A different `close_instance` request issued after the instance is already closed is not the same logical request and may be rejected as an unknown instance.

## 4. Response frame size

The 1 MiB TCP v0.5 frame limit applies to requests only. RL does not replace a completed API response with a response-direction `message_too_large` transport error.

This is required for retry correctness: if a non-idempotent operation such as `commit_action` or `emulate_action` succeeds and its response is larger than the request-frame limit, replacing that response after execution would make every same-ID retry replay the same undiscoverable oversized result. Training therefore reads the complete response frame to its newline without applying the request limit.

Any future response-size control must be defined at the API/payload level or by a replay-safe transport revision.

## 5. Precedence

For the asyncio TCP transport, this errata and `rl_training_tcp_transport_v0_5.md` take precedence over the older timeout and request-id-scope wording in `rl_training_dto_documentation_v0_5.md`. All other DTO field and operation semantics remain unchanged.
