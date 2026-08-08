# DTO v0.7 pair release assurance

DTO v0.7 is a hard-cutover wire-contract change shared by STS2_RL and STS2_Training. The two repositories therefore need two different kinds of assurance, and they must not be conflated.

## Repo-local required CI

The PR-required GitHub-hosted job in this repository is `rl-hosted-contract`. It validates RL-only protocol, coordinator, transport, masking, RNG rollback, BranchManager, and related Emulator-independent regressions.

A green `rl-hosted-contract` check does **not** prove that a particular STS2_Training commit is compatible with this RL commit.

The workflow file keeps its historical `paired-v07-counterpart-gate` identifier only for tooling compatibility. That identifier must not be interpreted as cross-repository attestation.

## Exact-pair release gate

A trusted paired gate, when used for release/deployment, must identify the exact pair `(rl_sha, training_sha)` and satisfy all of the following:

1. Resolve both PR/deployment SHAs before test execution.
2. Check out both repositories by immutable SHA, never by moving branch or PR refs.
3. Run paired wire/integration validation against that exact pair.
4. Re-read both source heads after validation and discard the result if either head moved.
5. Bind the result to both SHAs so a green result cannot be reused after one side changes.
6. Re-evaluate whenever either counterpart head changes.
7. Deploy the attested pair as one pinned compatibility unit.

If independent rolling deployment is required, v0.7 hard cutover is insufficient; dual-version support or explicit version/capability negotiation is required first.

## Execution-security boundary

Real-Emulator paired tests execute PR-controlled code and therefore require a stronger boundary than removing GitHub credentials from the test process. A trusted release gate should run that code in a disposable/ephemeral or equivalently isolated worker with no GitHub write credential, no developer credential store, minimal filesystem exposure, and restricted network access. A separate trusted controller may hold status-publishing credentials and publish a result only after verifying the pair identity and returned test evidence.

Until such an isolated exact-pair orchestrator exists, real-Emulator paired validation is advisory/manual validation and must not be represented as a branch-protection proof of exact-pair compatibility.
