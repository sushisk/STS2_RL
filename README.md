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

Trainingとは別プロセスでRL APIを起動できます。非同期TCP/DTO v0.6契約は
`docs/contracts/rl_training_tcp_transport_v0_6.md` を参照してください。

```bash
python -m API.tcp_server --host 127.0.0.1 --port 8765
```

v0.6では各Training clientが`client_session_id`と単調増加`request_seq`を持ち、
RLはsessionごとに直前request/responseだけを保持します。同一seqの再送は再実行せず
replayされ、RL再起動は`server_epoch`変更として明示的に検出されます。

接続時はAPI trafficより先に次のhelloを送ります。

```json
{"transport_operation":"hello","client_session_id":"<uuid>"}
```

疎通確認だけなら `{"transport_operation":"ping"}` を送れます。pong/hello応答には
現在の`server_epoch`が含まれます。Training側の確認コマンドは`STS2_Training`のREADMEを参照してください。
