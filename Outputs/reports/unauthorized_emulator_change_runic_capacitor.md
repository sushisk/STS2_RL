# Unauthorized Emulator Change Audit - Runic Capacitor

Date: 2026-07-21
Authoring side: RL repo (`C:\STS2_RL`)
Scope: record and unwind the unauthorized direct edits that were briefly made under `C:\STS2_Emulator`

## 1. Current status

- Emulator source state: **B. RL-side change reverted**
- Current Emulator DLL state: rebuilt after source revert
- `fixed50:5483-41` RL handling: **treat as `exclude_emulator_issue`**
- 100-combat expansion: **not started** (`docs/rl_status.json` says `dev_100_batch_status: "not started"`)

## 2. Files edited under `C:\STS2_Emulator`

Only one source file was edited:

- `C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Commands\OrbCmd.cs`

No new Emulator script or test file was added.

## 3. Unauthorized source diff

Changed lines:

- `OrbCmd.cs:31`
- `OrbCmd.cs:49`

Edited patch is saved separately at:

- `C:\STS2_RL\Outputs\reports\unauthorized_emulator_change_runic_capacitor.patch`

### Before

```cs
NCombatRoom.Instance?.GetCreatureNode(player.Creature).OrbManager?.AddSlotAnim(amount);
...
NCombatRoom.Instance?.GetCreatureNode(player.Creature).OrbManager?.RemoveSlotAnim(amount);
```

### After

```cs
// Slot-capacity is real combat state and must update even in headless/TestMode restores where no
// creature node (and therefore no OrbManager visuals) exists.
NCombatRoom.Instance?.GetCreatureNode(player.Creature)?.OrbManager?.AddSlotAnim(amount);
...
NCombatRoom.Instance?.GetCreatureNode(player.Creature)?.OrbManager?.RemoveSlotAnim(amount);
```

### Stated reason at the time

- Investigative fix for `fixed50:5483-41`
- Intended to avoid `NullReferenceException` during `RunicCapacitor.AfterSideTurnStart -> OrbCmd.AddSlots`
- This was **not within RL ownership** and is therefore not an approved fix

## 4. Build commands that were executed

These commands were run against the Emulator repo:

```powershell
dotnet build C:\STS2_Emulator\Sts2EmulatorPhase1.sln
dotnet clean C:\STS2_Emulator\Sts2EmulatorPhase1.sln
dotnet build C:\STS2_Emulator\Sts2EmulatorPhase1.sln
```

## 5. Generated DLL

Path:

- `C:\STS2_Emulator\Sts2Emulator.Cli\bin\Debug\net8.0\Sts2Emulator.dll`

Current on-disk DLL after revert + rebuild:

- LastWriteTime: `2026-07-21 12:40:38`
- SHA256: `6E3D97425D629506559CE8898C8053FB1EB058FA50612B834EE3DCB204EA3FEE`

Notes:

- This SHA256 matches the previously recorded RL-consumed DLL hash from before the unauthorized edit was unwound.
- A separate archived copy of the transient modified DLL was **not** saved.
- Therefore, the current file is the reverted/rebuilt DLL, not a preserved "unauthorized build artifact."

## 6. Change-before / change-after restorability

- Source file revert: **completed**
- Clean rebuild after revert: **completed**
- Current `OrbCmd.cs` content is back to the original form:

```cs
NCombatRoom.Instance?.GetCreatureNode(player.Creature).OrbManager?.AddSlotAnim(amount);
...
NCombatRoom.Instance?.GetCreatureNode(player.Creature).OrbManager?.RemoveSlotAnim(amount);
```

Assessment:

- The one known RL-authored source change is revertible and has been reverted.
- No backup copy of a separately archived pre-change DLL was found, but the rebuilt DLL hash matches the previously recorded hash.

## 7. Verification code that was executed during investigation

These were ad hoc shell / inline Python investigation snippets and were **not saved into repo files**.

### A. Public-API reproduction

- `GameInstance.ResetFromScenario(...)`
- `GetObservation()`
- `GetLegalActions()`
- `Step(...)`

Used on:

- minimal `DEFECT + RUNIC_CAPACITOR`
- minimal `DEFECT + INFUSED_CORE + RUNIC_CAPACITOR`
- fixed50 RL scenario `fixed50:5483-41`

Observed result before the unauthorized source revert:

- minimal `DEFECT + RUNIC_CAPACITOR` reproduced the failure
- first `Step()` timed out while Emulator stderr logged:

```text
RunicCapacitor.AfterSideTurnStart
-> OrbCmd.AddSlots
-> NullReferenceException
```

### B. Reflection-only investigation

The following pattern was used transiently in inline investigation code:

```text
BindingFlags.Instance | BindingFlags.NonPublic
GetField("_player")
```

Purpose:

- inspect `PlayerCombatState.OrbQueue.Capacity`
- inspect `PlayerCombatState.OrbQueue.Orbs.Count`

Important boundary note:

- this reflection access was **investigation-only**
- it was **not** added to RL source
- it was **not** added to CombatEnv / teacher generation / scenario adapter / tests

Repository check:

- search of `C:\STS2_RL` found no `GetField("_player")` / `BindingFlags.Instance | BindingFlags.NonPublic` usage

## 8. Investigation results obtained before rollback

### Fixed50 scenario repro material

- `scenario_id`: `fixed50:5483-41`
- `character`: `DEFECT`
- encounter: `MECHA_KNIGHT_ELITE`
- relevant relic: `RUNIC_CAPACITOR`
- failure stage: first candidate-evaluation `Step`
- exception path:
  - `RunicCapacitor.AfterSideTurnStart`
  - `OrbCmd.AddSlots`
  - `NullReferenceException`
- reproducible in fresh process: **true**

### Public-API-visible symptoms

- `ResetFromScenario(...)`: succeeds
- `GetObservation()`: succeeds
- `GetLegalActions()`: succeeds
- first candidate `Step(...)`: candidate-side Emulator timeout
- stderr contains the `NullReferenceException` stack trace above

### Ad hoc validation under the temporary unauthorized fix

Observed during the temporary modified build, before rollback:

- minimal `DEFECT + RUNIC_CAPACITOR`: `Step()` completed instead of timing out
- minimal `DEFECT + INFUSED_CORE + RUNIC_CAPACITOR`: `Step()` completed
- fixed50 `5483-41`: reset, observation, legal actions, and a first card step completed

This is **reference information only** for the Emulator owner. It is **not an approved fix**.

## 9. Smoke tests after revert

After source revert and clean rebuild, the following existing Emulator smoke scripts were run successfully:

- `smoke_card_upgrade.py`
- `smoke_enemy_placement.py`
- `smoke_lost_coffer.py`
- `smoke_multi_enemy.py`
- `smoke_neows_bones.py`
- `smoke_player_hp.py`
- `smoke_potions.py`
- `smoke_pythonnet.py`
- `smoke_queen_amalgam.py`
- `smoke_relic_restore_without_after_obtained.py`
- `smoke_scenario_relics.py`
- `smoke_stars.py`

Result: all passed after revert.

## 10. Current responsibility-safe RL position

RL side may continue only with:

- public-API repro capture
- observation / legal action / stderr preservation
- impact counting
- teacher-data exclusion
- Emulator handoff material

RL side must not continue with:

- direct `C:\STS2_Emulator` source changes
- Emulator DLL changes
- private-field-dependent production logic
- Emulator design or API changes

## 11. RL-side temporary disposition

Until an Emulator-owned fix is supplied, `fixed50:5483-41` should remain:

```text
exclude_emulator_issue
```

No 100-combat run should be started from the RL side.
