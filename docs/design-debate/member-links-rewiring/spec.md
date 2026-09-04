# spec: /m の外部リンクを全メンバーへ行き渡らせる（Gate1 出力）

**確定仕様の本文は `verdict.md` の §3。** ここはその要約と、親セッションによる検証記録・
留保を持つ。実装は `verdict.md` §3 を正として読むこと（ここに写して二重管理しない）。

## 判定

- **勝ち: design B（codex）。** 分類を `map key の m_` に固定し、新しい人物判定を作らない点が核。
- **design A（Claude）から移植: 非 `m_` に Wikipedia を残す一点のみ。** 実測で決着（下記）。
- **design A の3分類は棄却。** `rank` / `party` を選出職の証拠に使うと、実データで34人を誤分類する。

| 枠 | 担当 | 結果 |
|---|---|---|
| brief | Claude (grilling) | 完了 |
| draft A | Claude | 完了（棄却） |
| draft B | codex | 完了（採用） |
| critique of A | codex | Gate1 不合格・critical 4 |
| critique of B | fresh Claude subagent | critical 2 / high 5 |
| 裁定 | **grok**（非参加者） | 完了 |

## この debate が実際に潰したもの

**design A の致命傷（codex が発見、親が実データで確認）**

`rank` / `party` を選出職の判定に使うと、キーが `m_` の34人に Wikipedia 直リンクが付く。
生成されるのは `/wiki/記者`・`/wiki/事務局`・`/wiki/内閣総理大臣`・`/wiki/平`・`/wiki/山本氏`。
**404 ではなく 200 で、全く別のページが開く**（職業・組織用語・役職の解説記事）。
「404 をばらまかない」を目的に据えた設計が、その目的を最も汚い形で破っていた。
`ministry.mjs:73` は「rank を使うな（気象庁長官が minister になる）」と明記しており、
親はその正本を読んだうえで別の判定で同じ罠を踏んだ。

**design B の致命傷（fresh Claude subagent が発見、親が file:line で確認）**

commit ステップ（`daily-batch.yml:285`）に `if:` が無く、手前で落ちれば publish 全体が消える。
`validate-data.mjs:84-90` は「`--fix` では**意図的に**非ゼロ終了しない、#52 の再来を防ぐため」と
明記している。裁定はこれを検討したうえで **fail-closed を維持**した — 理由は
「enrich が黙ってスキップすると `--fix` が足した空リンクがそのまま commit される」であり、
threads 破損で朝を落とす #52 とは失敗の種類が違う。**この判断は意図的なトレードオフであり、
見落としではない**（verdict.md §4-4 に残存リスクとして明記されている）。

**design B の主張の誤り（親が実測で反証、裁定が採用）**

「Wikipedia は存在を保証できないから全員から削除」→ 非 `m_` の的中率は **95%**
（40人サンプル中38人。`measurement-wikipedia.md`）。批評の独立計測でも下限 74%（485/652）。
外れた2人は「五十嵐えり」「いんどう周作」＝**平仮名通称で、記事の不在ではなく表記ゆれ**。
保証できないことと当たらないことを同一視していた。

## 親セッションによる裁定の検証（receiving-code-review）

裁定が挙げた事実主張を repo と実データで確認した。**5件すべて正確。**

| 主張 | 結果 |
|---|---|
| `actions/setup-node@v7` | ✓ この repo は v7/v8 系で揃っている（親は誤りを疑ったが repo が正しかった） |
| 林芳正は非 `m_` の `hayashiyoshimasa` に正規エントリがある | ✓ → design A が「バグ」と呼んだ件は正規エントリ側で正しく扱われる |
| 石破茂も `ishibashigeru` に重複 | ✓ |
| 発言ゼロの省庁解決メンバーは5人 | ✓ |
| `/gov` のダングリングは現在ゼロ | ✓ リンク slug 37 = 生成 slug 37、404 ゼロ |

## 不変条件

1. **人・党派で分岐しない。** 判別は `map key が m_ か` と `getMemberMinistry が解決するか` の2つだけ。
   `rank` / `party` / 役職キーワードによる分岐を新コードに残さない。
2. **省庁判定の正本は `ministry.mjs` ひとつ。** 写しも第二の判定も作らない。
3. **JS 側 atomic writer は1つ。** `scripts/lib/jsonio.mjs` へ移し、手順は変えない。第三の写しを作らない。
4. **LLM・外部 API・実行間の状態を使わない。** 同じ入力から同じ出力。
5. **`links` 以外のフィールドを書き換えない。** 正規化した `id` を JSON へ書き戻さない。
6. **走査は `Object.entries`。** 識別子の正本は map key（値の `id` は31件で欠けている）。

## やらないこと

bio / CTR / タイトル語順 / Wikipedia API での存在確認 / `m_` 側の政治家を rank・party で救済 /
`ministry.mjs` 以外の省庁判定 / atomic 手順の「改良」の同梱 / eslint を `scripts/` へ広げる。

## 受け入れコマンド

```
npm run lint && npm run validate
python -m pytest scripts/tests
```

**pytest の skip 数は 0**（PyYAML 未導入の静かな skip を合格にしない。依存は `requirements-dev.txt`、Python 3.12）。
テスト14本の内訳は `verdict.md` §3「テスト」節。**先に赤を確認してから実装する。**

`npm run lint` は `scripts/**` を見ない（`eslint.config.mjs:17`）。**受け入れ2本のうち1本は
新コードに対して無力**であることを承知のうえで採用する（eslint の拡張は別仕事）。

## 検討して採らなかった選択肢（記録）

**enrich を新ステップにせず `validate-data.mjs --fix` の中へ入れる。** 親が批評を読んだ後に
思いつき、debate を経ていない案。C1（commit 直前の新規 fail-close 点）・H4（atomic writer の抽出）・
M5（Vercel ビルドが enrich を通らない）・順序問題が構造的に消えるので一度検討した。

**採らない理由**: fail-close の問題は消えない。`validate-data.mjs` の fail-open は `error()` を
通る**検証エラー**にだけ効くもので、enrich のコード例外はプロセスごと落ちて同じ結果になる。
つまり利点は見かけより小さく、代わりに validate-data が2つの責務を持つ。M5 は裁定案が
`package.json` の `build` を直すことで既に解決している。

## ゲート区分と次の段

**(A) 不可逆 diff**（`daily-batch.yml` / `ci.yml` = CI 定義に触る）。
Gate2（plan の敵対レビュー）は省略しない。Gate3 は `/code-gate` 必須、critical 0 まで。

次の段: この spec を `superpowers:writing-plans` に渡す。
