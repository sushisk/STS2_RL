# STS2 RL Azure Spot VM 移行検証報告

## 概要

Azure Windows Spot VM 上に STS2 RL / Emulator 実行環境を配置し、小規模batch検証、resume確認、Blob退避確認を実施した。

Emulatorコードは変更していない。  
2,000件教師データ生成はまだ開始していない。

## 接続先

- VM: `sts2_vm`
- 接続先: `azureuser@52.231.66.228`
- OS: Windows
- Resource Group: `STS2`
- Location: `koreacentral`
- VM Size: `Standard_D2as_v4`
- vCPU: 2
- Memory: 7.99 GB

## 配置内容

- RL: `C:\STS2_RL`
- Emulator: `C:\STS2_Emulator`
- full reconstruction pool: 11 files / 224.41 MB
- smoke10 manifest:
  - `C:\STS2_RL\Combat\data\azure_smoke10\scenario_manifest.jsonl`
  - SHA256: `E7E751012C1E49D9B8FD2C4E0D26612EA2B0106F4B9DFCE1B9EBAC4A1087FC31`

元500件manifest:

- SHA256: `4373CEB0EA0CE3258D6FFCFA780F2D947ADAA6F9B73AA0F4F0455940637EED9F`

## 環境

- Python: `3.12.7`
- .NET SDK: `8.0.423`
- pythonnet: `3.1.0`
- numpy: `2.5.1`
- AzCopy: `10.32.6`

## Emulator

- 使用commit: `163bf040027abca2754393a949e612e42f46a3e7`
- VM上でDebug build成功
- DLL:
  - `C:\STS2_Emulator\Sts2Emulator.Cli\bin\Debug\net8.0\Sts2Emulator.dll`
- DLL SHA256:
  - `ACF324145D68DE40D56778EC005F07AFD0C6CA52F43407086A9208C6DA1CDE73`
- pythonnetからのDLLロード: 成功

## 小規模batch検証

実行条件:

- 対象: accepted 500 manifest 先頭10件
- workers: 4
- max decisions: 50
- determinism sample: 3
- 出力:
  - `C:\STS2_RL\Combat\data\azure_smoke10_run`

結果:

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
- elapsed: 76.04 sec
- avg time / combat: 17.345 sec
- avg time / decision: 0.3717 sec

## ローカル結果との差分

比較対象:

- `C:\STS2_RL\Combat\data\trajectories_train500_20260722_w4_regression3`

確認結果:

- Scenario数一致
- trajectory ID欠落なし
- trajectory ID重複なし
- selected action列一致
- status / data usage / termination reason一致
- 重大差分なし

## resume確認

同一出力directoryに対して `--resume` を再実行した。

- resume_used: true
- already_completed_before_resume: 10
- newly_executed_this_invocation: 0
- determinism: 3/3
- elapsed: 20.24 sec

完了済みScenarioを再実行しないことを確認した。

## メモリ使用量

追加の10件runでPython processのworking setをサンプリングした。

- workers: 4
- ok: 10
- quarantined: 0
- timeout: 0
- error events: 0
- elapsed: 57.55 sec
- peak total Python working set: 616.4 MB
- peak single Python process working set: 164.4 MB
- approximate memory / worker: 154.1 MB

## Blob退避確認

VM上の小規模batch出力をzip化し、AzCopyでBlob Storageへアップロードした。  
その後、ローカルへ再取得しSHA256一致を確認した。

- VM zip / downloaded zip SHA256:
  - `09310BFDD0D6FDD3BF33D2626EC4085A820DE847EA444F947FBE87A4A3C6BE24`
- upload: 成功
- download: 成功
- SHA256一致: 成功

SAS文字列および秘密情報は保存していない。

## 問題と対応

- SSH公開鍵投入用のAzure拡張が一時的に `Creating` / `Deleting` で詰まった。
- `%AZURE_KEY%` は秘密鍵ファイルではなく、SSH passwordとして有効だった。
- 初回zip展開が途中で残り、Emulator sourceが一部欠落した。
- Emulator sourceはSFTPで再同期し、ビルド成功を確認した。
- VMには当初 .NET SDK がなかったため、`C:\dotnet8` に .NET SDK 8.0.423 を導入した。

## 判定

Azure Windows Spot VM上で、RL / Emulator 実行環境は再現できた。

以下を満たしている。

- Emulator DLL build成功
- pythonnet DLL load成功
- 10件batch完走
- trajectory欠落・重複なし
- ローカル結果との重大差分なし
- determinism維持
- `--resume`動作確認済み
- Blob退避・再取得確認済み

## 次工程判断

2,000件教師データ生成へ進める状態である。

推奨条件:

- workers: 4
- fixed manifest使用
- `--resume`有効
- 新規run directory使用
- 一定間隔でBlobへ退避
- Emulatorコードは変更しない

2,000件生成は、本報告時点では未開始。
