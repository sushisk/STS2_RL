# docs/ 編集ルール(wire protocol / paired release 文書)

`docs/wire_contract_v0.7.md`、`wire_contract_v0.8.md`、`emulate_actions_timeout_semantics_v0.7.md`、`dto_paired_release_gate_v0.7.md` の4ファイルは STS2_Training と共有する契約であり、**STS2_Training の `docs/` が正本(canonical source)**である。

- これらのファイルを編集する場合は、先に STS2_Training 側で編集・マージし、その後このリポジトリへ**バイト単位で同一の内容**をコピーする。この repo 側を先に、または独自に編集しない。
- `dto_paired_release_gate_v0.7_local.md` はこの repo だけの非同期ファイルで、STS2_Training 側とは異なる内容(必須 CI job 名など)を持つ。同期対象ではない。
- 完全な編集ポリシー(文章編集ポリシー、ファイル分割・統合の基準、命名規則、バージョン付番規則など)は STS2_Training の `docs/README.md` を正本として参照すること。ここでは重複させない。

`docs/RL_HANDOFF.md` はこのポリシーの対象外(この repo 固有の別文書)。
