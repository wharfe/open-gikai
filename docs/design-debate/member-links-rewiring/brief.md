# brief: /m ページの外部リンクを全メンバーに行き渡らせる

## 背景（実測 2026-09-04）

GSC 2026-08-05〜09-01: 検索クエリ上位40件に法令名はゼロ、ほぼ全部が官僚の人名
（「寺崎秀俊」「井口裕之 財務省」「石川泰三 警察庁 経歴」）。着地ページ上位30件のうち
29件が `/m/{memberId}`。掲載順位 6〜19位。この site の実際の流入は「官僚の名前を調べた人」。

`data/members.json` 1,298人の状態:

| | 人数 | bio 空 | links 0本 |
|---|---|---|---|
| 官僚 (`m_`) | 615 | 100% | 606 (98%) |
| 議員 | 683 | 100% | 635 (92%) |

links 本数の分布は 議員 {0: 635, 1: 1, 3: 47} / 官僚 {0: 606, 1: 9}。

## この機能を既に持っている仕組みを探したか

**探した。結果は「links は既存機構あり・未配線」「bio は未実装」。**

- `scripts/enrich_members.py`（103行）が既にある。ただし書くのは `links` のみで `bio` は触らない。
  そして **どのワークフローからも呼ばれていない**（`.github/workflows/*.yml` に grep ヒットゼロ）。
  一度手で走らせたきりで、その後に追加された1,200人以上が素通りしている。これが 98% 空の直接原因。
- `scripts/pipeline/members.py:224` は `"bio": ""` を常に固定で書き、:201 のコメントが
  「bio/stance/district/since は外部データが要る」と明言。bio は壊れているのではなく未実装。
- `src/lib/ministry.mjs` の `getMemberMinistry(member)` が省庁判定の正本として既にある。
  `generate-sitemap.mjs` / `notify-indexnow.mjs` が既に node から import している。
- 管理サービス（scheduler / queue / retry）は不要。日次バッチの1ステップで足りる。

## ゴール

`data/members.json` の全メンバーが、404 に着地しない外部・内部リンクを持つ。
配線が外れても気づかない状態を終わらせる。

## やること

1. `scripts/enrich_members.py` を **node スクリプトへ移す**。理由は `ministry.mjs`（省庁判定の正本）を
   そのまま import するため。LLM も外部 API も使わない純粋な文字列変換なので Python である必然性が無く、
   Python に写すと正本が2つになる（`jsonio` / `validate-data.mjs` の二重化は node が Python を
   import できないという不可避の事情によるもので、ここにはその事情が無い）。
2. 官僚には **Wikipedia リンクを出さない**。代わりに所属省庁の `/gov` ハブへの内部リンクと
   「氏名＋省庁」の検索リンクを出す。現行は全員に無条件で `ja.wikipedia.org/wiki/{氏名}` を付けており、
   そのまま配線すると 1,200人分の 404 をばらまく。
3. **全員分を決定的に再生成し、内容が現状と同じならファイルに書かない**（no-op）。
4. `daily-batch.yml` に enrich ステップを追加する。**`|| true` は付けない。**

## 不変条件（守るもの）

- **中立性**: この層は補助レイヤー。どのメンバーにも同じ規則を適用し、人・党派で分岐しない。
  LLM を使わない（決定的な文字列変換のみ）。
- **省庁判定の正本は `ministry.mjs` ひとつ**。写しを作らない。
- **`write_json_atomic` 相当の書き方を守る**（`validate-data.mjs` が持つ temp→fsync→rename→fsync-dir）。
  `members.json` は commit される JSON なので、途中で死んだら切り詰められた版がその後の正本になる。
- **失敗は朝を止めてよい**。`enrich-news.py` の `|| true` を真似しない。静かに空のまま増えるのが
  今回の失敗形そのもの。

## やらないこと（non-goals）

- **bio は埋めない。** 別立ての仕事。外部データ源の選定と中立性の線引きが要るので、この diff には入れない。
- **CTR の改善を目的にしない。** 同順位で CTR が 2.3%〜21% ばらつく件は仮説のまま。
  タイトルの語順いじりは測定困難なので、事後の観測指標に置くだけにする。
- 対象を官僚だけに絞らない（議員も同じ状態。絞ると `ministry.mjs` 以外の判定を新設することになる）。
- Wikipedia API での存在確認はしない（毎朝1,241人分の外部呼び出しとレート制御が新規に発生する）。

## 受け入れコマンド（機械判定）

```
npm run lint && npm run validate
python -m pytest scripts/tests
```

加えて新規テスト4本。**いずれも壊して赤くなることを確認してから緑にする**:

1. `members.json` の全員が links を1本以上持つ
2. 官僚（`m_` プレフィックス）に Wikipedia リンクが付かない
3. 同じ入力で2回走らせ、2回目がファイルに書き込まない（冪等）
4. `daily-batch.yml` に enrich ステップが存在し、`|| true` が付いていない
   （`test_systemic_failure.py` の YAML パース群に追加）

4 は「配線を外しても誰も気づかない」という今回の根本原因を塞ぐ fence。

## ゲート区分

**(A) 不可逆 diff**（`daily-batch.yml` = CI 定義に触るため。行数は理由にならない）。
Gate1 = `/design-debate` フル、Gate3 = `/code-gate` 必須。
