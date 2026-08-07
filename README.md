# STS2_RL

Slay the Spire 2 高勝率AI開発プロジェクトの学習・探索・評価実装領域。

**現在の到達点・作業の再開方法は `docs/RL_HANDOFF.md` を参照。**
機械可読な現状サマリは `docs/rl_status.json`。

## ディレクトリ概要

```text
Common/     ID辞書・Schema定義・バージョン管理
Combat/     戦闘AI(Heuristic、CombatEnv、データパイプライン、テスト)
Outputs/    報告書
docs/       引き継ぎ資料・現状サマリ
```

詳細な責務分担、外部依存(STS2_Emulator/STS2_Decompiled_v0109/STS2_Data)、
現在のデータ資産、既知の未解決事項は `docs/RL_HANDOFF.md` に集約している。
このファイルはナビゲーションのみを目的とする。

## Asyncio TCP server

Trainingとは別プロセスでRL APIを起動できます。DTO v0.5のTCP framing仕様は
`docs/contracts/rl_training_tcp_transport_v0_5.md` を参照してください。

```bash
python -m API.tcp_server --host 127.0.0.1 --port 8765
```

Emulatorを起動せずに疎通だけ確認する場合は、接続後に
`{"transport_operation":"ping"}` を1行送ると `{"transport_operation":"pong"}` が返ります。
Training側の確認コマンドは `STS2_Training` のREADMEを参照してください。
