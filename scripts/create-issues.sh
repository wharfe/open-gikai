#!/bin/bash
# Create GitHub issues for content engagement improvements
# Usage: gh auth login && bash scripts/create-issues.sh

set -euo pipefail

REPO="wharfe/open-gikai"

echo "Creating issues in $REPO..."

# Issue 1: Life-theme entry points (Priority: High)
gh issue create --repo "$REPO" \
  --title "feat: Add life-theme based entry points for content discovery" \
  --label "enhancement,priority:high" \
  --body "$(cat <<'EOF'
## Summary

現在フィードは委員会・日付ベースで並んでいるが、ユーザーの生活関心事からのナビゲーションがない。「教育・子育て」「税金・社会保険」「AI・テクノロジー」「医療」等のテーマタグでフィルターできるようにする。

## Motivation

「予算委員会」と言われてもピンとこないが、「電気代」と言われれば自分事になる。生活テーマ別のエントリーポイントを設けることで、ユーザーが自分に関係のある審議を発見しやすくする。

## Implementation

- `topicTag` を上位の生活テーマカテゴリにマッピング（例：config に定義）
- トップページにテーマカード UI を追加
- テーマ別のフィルタリング機能
- AI パイプラインで `lifeTheme` フィールドを自動分類

## Design Notes

- テーマ例：教育・子育て / 税金・社会保険 / AI・テクノロジー / 医療・健康 / エネルギー・環境 / 防衛・外交 / 雇用・労働
- カード表示例：「あなたの給料に関わる審議」
EOF
)"
echo "✓ Issue 1 created"

# Issue 2: Life impact card (Priority: Medium)
gh issue create --repo "$REPO" \
  --title "feat: Add life-impact summary line to thread cards" \
  --label "enhancement,priority:medium" \
  --body "$(cat <<'EOF'
## Summary

各スレッドに「生活への影響」を1行で示す `impact` フィールドを追加し、ThreadCard に表示する。

## Motivation

フィードをスクロール中に「これは自分に関係ある」と気づける情報があると、スレッドを開くモチベーションが上がる。

## Implementation

- `Thread` 型に `impact?: string` フィールドを追加
- AI 要約パイプラインで `impact` を同時生成
- `ThreadCard` コンポーネントに影響表示 UI を追加
- 例：「会社員の残業規制が変わる可能性」「電気代の補助が延長される見込み」
EOF
)"
echo "✓ Issue 2 created"

# Issue 3: Controversy highlights (Priority: High)
gh issue create --repo "$REPO" \
  --title "feat: Add controversy/debate highlights to thread headers" \
  --label "enhancement,priority:high" \
  --body "$(cat <<'EOF'
## Summary

スレッドヘッダーに「争点」を対立構造で可視化する。現在 `tension` タイプはあるが、何が争点なのかが一目でわからない。

## Motivation

「この議論で何が問題なのか」が3秒でわかると、読みたくなる。対立軸の明示は政治的中立性を保ちつつ、興味を喚起できる。

## Implementation

- `Thread` 型に `debate?: { position: string; counterPosition: string }` を追加
- AI パイプラインで自動抽出
- `ThreadCard` / `ThreadDetailView` に争点表示 UI を追加
- 表示例：「野党：AI規制を強化すべき ↔ 政府：自主規制で十分」

## Design Notes

- 対立構造がないスレッド（報告のみ等）では非表示
- 政治的中立性を保つため、両方の立場を等価に表示する
EOF
)"
echo "✓ Issue 3 created"

# Issue 4: Region filter (Priority: High)
gh issue create --repo "$REPO" \
  --title "feat: Add region/constituency-based filter" \
  --label "enhancement,priority:high" \
  --body "$(cat <<'EOF'
## Summary

ユーザーが自分の都道府県・選挙区を選択し、その地域の議員の発言を優先表示する機能。ログイン不要（localStorage保存）。

## Motivation

「自分が投票した（する）議員が何を言っているか」は最も直接的な自分事化。`Member.district` フィールドは既に存在する。

## Implementation

- 都道府県選択 UI（初回訪問時のオンボーディング or 設定から）
- `AppProvider` に `region` state を追加、localStorage で永続化
- フィードに「あなたの地域の議員の発言」セクションを追加
- Member リストでも地域フィルター対応
EOF
)"
echo "✓ Issue 4 created"

# Issue 5: Weekly digest (Priority: Low)
gh issue create --repo "$REPO" \
  --title "feat: Add weekly digest / highlights page" \
  --label "enhancement,priority:low" \
  --body "$(cat <<'EOF'
## Summary

毎日追わないユーザー向けに、週単位のまとめページを自動生成する。

## Motivation

毎日チェックするのはハードルが高い。週次ダイジェストがあれば、週末に10分で今週の国会を把握できる。

## Implementation

- `/weekly` ルートを追加
- 今週の注目審議トップ5（発言数・キーワード頻度でランキング）
- ステータス変化があったスレッド（`outcome.status` の変化追跡）
- SSG で静的生成
EOF
)"
echo "✓ Issue 5 created"

# Issue 6: Outcome tracker (Priority: Medium)
gh issue create --repo "$REPO" \
  --title "feat: Enhance outcome tracker with timeline and cross-thread linking" \
  --label "enhancement,priority:medium" \
  --body "$(cat <<'EOF'
## Summary

既存の `ThreadOutcome` を活用して、法案・議題の進捗追跡を強化する。

## Motivation

一度興味を持った話題の続きが追えると、継続的に訪問する理由になる。「結局どうなった？」に答えられるサイトは価値が高い。

## Implementation

- ステータス変化の時系列表示（タイムライン UI）
- `relatedThreads` を活用した同一テーマの横断表示
- 「この話題のその後」セクションを ThreadDetailView に追加
- `/tracker` ページで進行中の重要議題を一覧表示
EOF
)"
echo "✓ Issue 6 created"

# Issue 7: Anonymous reactions (Priority: Low)
gh issue create --repo "$REPO" \
  --title "feat: Add anonymous reaction/interest signals" \
  --label "enhancement,priority:low" \
  --body "$(cat <<'EOF'
## Summary

ログイン不要の匿名「気になる」ボタンを追加し、関心度を可視化する。

## Motivation

「○○人が注目」という社会的証明があると、他のユーザーも興味を持ちやすくなる。軽量なエンゲージメント手段として有効。

## Implementation

- 「気になる」ボタン UI を ThreadCard / ThreadDetailView に追加
- サーバーレス API でカウント集計（Vercel Edge Functions or Cloudflare Workers）
- クライアントサイドで非同期取得・表示
- レート制限・重複防止（IP or localStorage ベース）

## Notes

- SSG との整合性：カウントはクライアントサイドで非同期取得
- インフラ追加が必要なため優先度は低め
EOF
)"
echo "✓ Issue 7 created"

echo ""
echo "All 7 issues created successfully!"
