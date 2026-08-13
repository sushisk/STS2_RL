# DTO v0.7 paired release gate — STS2_RL local specifics

Repo-local supplement to [dto_paired_release_gate_v0.7.md](dto_paired_release_gate_v0.7.md), which is a canonical file synced byte-identical with STS2_Training's copy. **This file is NOT synced** — STS2_Training maintains its own version of this file describing its own CI.

The PR-required GitHub-hosted job in this repository is `rl-hosted-contract` (workflow `paired-v07-counterpart-gate.yml`, historical identifier `paired-v07-counterpart-gate`). It validates RL-only protocol, coordinator, transport, masking, RNG rollback, BranchManager, and related Emulator-independent regressions.

The real-Emulator paired test in this repository is advisory/manual validation only (see the shared doc's "Execution-security boundary" section).
