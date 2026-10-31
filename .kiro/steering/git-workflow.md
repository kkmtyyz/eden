<!-- このファイルはKiroのsteering用ドキュメントです。開発時に参照してください。 -->

# Git ワークフロー

## ブランチマージ方針

- プルリクエストやブランチをマージする際は、**必ずマージコミットを作成する**（`--no-ff`）。
- fast-forward マージは使わない。
- これにより、マージの境界がコミット履歴上で明確になり、revert も容易になる。

```bash
# 例: feature ブランチを kiro/dev にマージする場合
git checkout kiro/dev
git merge --no-ff feature/xxx -m "Merge feature/xxx into kiro/dev"
```

## ブランチ命名規則

- Kiro作業用の統合ブランチ: `kiro/dev`
- 機能ブランチ: `feature/<機能名>`
- 修正ブランチ: `fix/<内容>`
- Kiroでの作業はすべて `kiro/dev` にマージする
- `kiro/dev` がまとまったら `main` に戻す
