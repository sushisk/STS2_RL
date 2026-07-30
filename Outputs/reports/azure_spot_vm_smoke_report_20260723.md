# Azure Spot VM Smoke Report 2026-07-23

## Scope

- VM: `azureuser@52.231.66.228`
- OS: Windows
- Emulator code was not modified.
- Large 2,000-scenario generation was not started.
- SAS and secret values were not written to this report.

## VM

- Resource group: `STS2`
- VM name: `sts2_vm`
- Location: `koreacentral`
- Size: `Standard_D2as_v4`
- Logical processors: 2
- Memory: 7.99 GB
- Free memory after setup check: 5.38 GB

## Environment

- Python: 3.12.7
- .NET SDK: 8.0.423 installed at `C:\dotnet8`
- pythonnet: 3.1.0
- numpy: 2.5.1
- AzCopy: 10.32.6

## Code And Data Placement

- RL execution tree: `C:\STS2_RL`
- Emulator source tree: `C:\STS2_Emulator`
- Full reconstruction pool present: 11 files, 224.41 MB
- Source 500 manifest SHA256: `4373CEB0EA0CE3258D6FFCFA780F2D947ADAA6F9B73AA0F4F0455940637EED9F`
- VM smoke10 manifest SHA256: `E7E751012C1E49D9B8FD2C4E0D26612EA2B0106F4B9DFCE1B9EBAC4A1087FC31`

## Emulator

- Expected commit: `163bf040027abca2754393a949e612e42f46a3e7`
- VM build: succeeded with .NET SDK 8.0.423
- DLL: `C:\STS2_Emulator\Sts2Emulator.Cli\bin\Debug\net8.0\Sts2Emulator.dll`
- DLL SHA256: `ACF324145D68DE40D56778EC005F07AFD0C6CA52F43407086A9208C6DA1CDE73`
- DLL load via pythonnet: succeeded

## 10-Scenario Batch

- Input: first 10 scenarios from the accepted 500 manifest
- Output: `C:\STS2_RL\Combat\data\azure_smoke10_run`
- workers: 4
- max decisions: 50
- determinism sample: 3
- elapsed: 76.04 s

Summary:

- total: 10
- ok: 10
- quarantined: 0
- usable_complete: 7
- usable_partial: 3
- timeout: 0
- illegal action: 0
- heuristic exception: 0
- emulator step exception: 0
- cycle: 0
- no progress: 0
- determinism: 3/3
- avg time per combat: 17.345 s
- avg time per decision: 0.3717 s

Local comparison:

- Compared against `Combat\data\trajectories_train500_20260722_w4_regression3`
- Scenario count matched: 10
- trajectory IDs matched with no duplicates
- selected action sequences matched
- status/classification/termination fields matched
- material local/VM diff: none

## Resume

- Command rerun with `--resume` against the same output directory.
- already completed before resume: 10
- newly executed: 0
- determinism after resume: 3/3
- elapsed: 20.24 s

## Memory Sampling

Additional 10-scenario run for process memory sampling:

- Output: `C:\STS2_RL\Combat\data\azure_smoke10_mem_run2`
- workers: 4
- ok: 10
- quarantined: 0
- timeout: 0
- error events: 0
- elapsed: 57.55 s
- peak total Python working set: 616.4 MB
- peak single Python process working set: 164.4 MB
- approximate per-worker memory: 154.1 MB

## Blob Transfer

- VM output zip was uploaded to Blob Storage with AzCopy.
- The uploaded zip was downloaded back locally with AzCopy.
- Downloaded SHA256 matched VM zip SHA256:
  `09310BFDD0D6FDD3BF33D2626EC4085A820DE847EA444F947FBE87A4A3C6BE24`

## Decision

The Azure Windows Spot VM environment is usable for the next stage.

Proceeding to 2,000-scenario generation is technically ready, but generation has not been started. Recommended initial settings:

- workers: 4
- use fixed manifest and `--resume`
- save output under a new run directory
- periodically upload run output to Blob Storage
- keep Emulator source unchanged
