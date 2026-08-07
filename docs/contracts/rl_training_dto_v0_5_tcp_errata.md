# RL–Training DTO v0.5 TCP Errata

This errata is normative for DTO v0.5 when it is carried over the independent-process asyncio TCP transport.

It corrects two statements in `rl_training_dto_documentation_v0_5.md` that predate the TCP process boundary.

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
- transport-only error objects such as `{"transport_error":"message_too_large", ...}` are raised as transport failures before API DTO validation.

Normal API failures remain DTO responses with `status="rejected"` or `status="faulted"`.

## 3. `close_instance` retry

A lost `close_instance` response is also ambiguous completion. RL retains the completed close request ledger after removing the active instance so the exact same close request can be replayed successfully on a fresh connection.

A different `close_instance` request issued after the instance is already closed is not the same logical request and may be rejected as an unknown instance.

## 4. Precedence

For the asyncio TCP transport, this errata and `rl_training_tcp_transport_v0_5.md` take precedence over the older timeout and request-id-scope wording in `rl_training_dto_documentation_v0_5.md`. All other DTO field and operation semantics remain unchanged.
