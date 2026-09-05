両方の設計とリポジトリの根拠を突き合わせて裁定します。まず全文と関連コードを読みます。批評の主張をコードとデータで突き合わせます。分類規則・ワークフロー順・atomic writer が論点です。批評の具体例を、validator・ワークフロー・実データで照合します。裁定の根拠はコードと実データで確認済みです。Codex を土台に、Claude 側の Wikipedia 判断と批評で当たっていた穴だけ接ぎます。

---

## 1. 判定

**勝つのは Codex（design B）**。決め手は分類と順序で、どちらも「壊すと brief の受け入れが翌朝から偽になる」種類だからです。

Claude の3分類は「Wikipedia が当たりそうか」を `rank` / `party` / 政治肩書で近似しますが、実データではその近似が壊れます。`m_40667609` 野村竜一は `role=気象庁長官` なのに `rank=minister`、`m_5474c5dc` は name/party/role が全部「記者」です。`ministry.mjs:79-80` はまさに「rank で官僚を弾くな」と書いてあります。この分類を採ると、brief が禁じた「官僚への Wikipedia」を、別の入り口から入れ直します。

順序も同じです。`validate-data.mjs --fix` は threads にだけいる memberId を `links` なしで足します（175–183行。`id` も足さない）。enrich をその前に置くと、同じ朝の commit に空リンクが混ざります。いまの31件の stub（キーは `m_`、値に `id` なし、`name === キー`、`role` 空）がまさにその残骸です。

Codex は境界を **map key の `m_`** に固定し、省庁は `getMemberMinistry` だけ、それ以外は検索に落とします。新しい人物分類を足さない。brief の受け入れ2と、省庁正本を増やさない不変条件にそのまま沿います。

Codex 側の弱い点は別レイヤーです。Wikipedia を非 `m_` からも消す解釈は、brief が言っていない上に実測と合いません（選出職サンプル40人で38ヒット、既存57本は全部実在 URL）。`links` の `/gov` を外部リンクとして描く点、テストが生成器ではなく committed JSON だけを見る点も欠陥ですが、分類を捨てずに直せます。Claude の分類は核なので接ぎ木できません。

## 2. 移植する部品

Claude および両批評から、次だけ移します。

| 部品 | 出典 | 理由 |
|---|---|---|
| 非 `m_` に Wikipedia を残す | Claude + measurement-wikipedia.md + critique-of-codex M2 | brief が禁じているのは官僚側。選出職は95%着地。57本を Google 検索に置き換える代償に見合わない |
| `/gov/{slug}` は生成される slug だけ | critique-of-claude I2 / critique-of-codex H3 | `dynamicParams=false`。`MINISTRIES` にあるだけでは 404 を防げない。member page は既に `getMinistrySlugs()` と交差している |
| 相対 URL は内部リンクとして描く | critique-of-codex M1 | プロファイルは全 `links` を `target=_blank` + `open_in_new` で描いている。このまま `/gov` を足すと内部ページが外部扱いになる |
| 生成器 fixture テスト | critique-of-codex H2 | committed JSON だけ見ると、生成器が落ちてファイルが古いままでもテスト1・2は緑のまま |
| Wikipedia 禁止は hostname 全体 | critique-of-codex L2 | `ja.wikipedia.org` だけ弾くと `ja.m.wikipedia.org` が通る |
| `package.json` の `build` にも enrich を挟む | critique-of-codex M5 | ワークフローだけだと、Vercel の `--fix` がリンクなしメンバーを足したデプロイが残る |
| `ci.yml` python-tests に `setup-node` | critique-of-codex M4 | CLI を subprocess するテストが runner 既定の node に暗黙依存する |
| 既定パスは CWD ではなくスクリプト位置 | critique-of-codex L1 | `cd scripts && python -m pytest` が既定 |
| 役職は全文を検索語にする | critique-of-claude Minor 1 | 「role 先頭語」は未定義。同じ入力から実装者ごとに URL が分かれる |
| atomic helper のテストは本体の手順を見る | 両案 + critique-of-codex H4 | 呼び出し行の文字列だけだと、関数を移した瞬間に fence が空振りする |

**採らないもの**

- Claude の区分 A（`rank` / `party` / 政治肩書）。`気象庁長官` と `記者` を政治家にする
- 「`rank ∈ {pm,minister,viceminister}` は全員 A」テスト。上のバグを仕様にする
- `is_elected_member` の順序入れ替え。関数ごと廃止する（`官房長` が `内閣官房長官` に部分一致するバグは、関数を捨てれば消える。`林芳正` は既に非 `m_` の `hayashiyoshimasa` として別エントリがある）
- `POLITICAL_TITLE_PREFIXES` の export。分類を map key に戻すなら不要。いま非 export（`ministry.mjs:61`）
- Wikipedia 全削除
- enrich を validate より前に置く
- データ異常を annotation して commit を通す第三の型。`--fix` が足した空リンクが commit されるのが今回の失敗形。`|| true` の言い換えになる
- 空の `name` でジョブ全体を落とす fail-closed。該当0件。1件のためにその朝の threads を捨てる必要はない。検索リンクに落とす

---

## 3. 確定仕様

### 目的

`data/members.json` の全メンバーが、存在を確認できる着地先へのリンクを1本以上持つ。配線が外れてもテストが赤くなる。bio は触らない。LLM / 外部 API / 実行間の状態は使わない。

### 範囲（この diff が触るファイル）

- 追加: `scripts/enrich-members.mjs`（生成器 + CLI）
- 追加: `scripts/lib/jsonio.mjs`（JS 側 atomic writer の正本）
- 変更: `scripts/validate-data.mjs`（ローカル `writeJsonAtomic` を上記から import）
- 削除: `scripts/enrich_members.py`
- 変更: `.github/workflows/daily-batch.yml`（ステップ分割）
- 変更: `.github/workflows/ci.yml`（python-tests に setup-node）
- 変更: `package.json` の `build`
- 変更: `src/components/member/member-profile-view.tsx`（相対 URL の描画）
- 変更: `scripts/tests/test_jsonio.py`、`scripts/tests/test_systemic_failure.py`
- 追加: `scripts/tests/test_member_links.py`
- 変更: `data/members.json`（全件再生成を1回 commit）
- 変更: `CLAUDE.md`（JS atomic writer が `validate-data.mjs` 内包だとしている記述を、共有 helper へ更新）

`ministry.mjs` の判定ロジックは変えない。写しを作らない。

### 識別子

走査は必ず `Object.entries(members)`。正本は **map key**。値の `id` が欠けていても key を `memberId` として使う。

> **[Gate2 で覆された — 2026-09-04]** 元の指示は「`getMemberMinistry` へ渡すときだけ
> `{ ...member, id: memberId }` と正規化する」だった。**実装計画側では採らない。**
> 理由は実測2点: (1) 正規化しても省庁の解決件数は **395 → 395** で1件も増えない（`id` を欠く31件は
> `role` も空で、`getMemberMinistry` は `id` を見る前に null を返す）。(2) `src/lib/data.ts:352-354`
> は保存されたオブジェクトのまま解決するので、こちらだけ正規化すると **`data.ts` が生成しない
> `/gov` ページへリンクしうる** — `dynamicParams=false` なので hard 404。正規化は何も買わずに
> 潜在的な404だけを持ち込んでいた。**正しくは `getMemberMinistry(member)`**（`data.ts` と同じ）。
> 詳細は `docs/superpowers/plans/2026-09-04-member-links-rewiring.md`。

触ってよいフィールドは `links` だけ。走査は `Object.entries` で、`m_` かどうかの判定には map key を使う（この半分は覆っていない）。

実測（2026-09-04）: `m_` キー 646、うち値に `id` なし 31件。31件は全員 `role=""` かつ `name === key`。id を足しても省庁解決は 395 のまま増えない。呼び出し形は保存オブジェクトそのまま（`getMemberMinistry(member)`）— 上の Gate2 注記のとおり key への正規化はしない。31件は `id` を欠くため `getMemberMinistry` の `m_` ガードを満たせず、常に null になる。

### 生成規則（決定的。人・党派で分岐しない）

`buildMemberLinks(memberId, member, { ministry, liveSlugs })` が配列を返す。既存 `links` との merge はしない。完全置換。

`ministry = getMemberMinistry(member)`（**上の Gate2 注記で正規化は撤回**。`src/lib/data.ts:352-354`
と同じく保存オブジェクトのまま渡す）。`liveSlugs` は後述。

検索 URL は `URLSearchParams` で組む。氏名・役職は保存文字列を trim しただけ。先頭語抽出も別名正規化もしない。

**1. map key が `m_` で始まらない（議員名簿と突き合った人）**

この順で3本。ラベルは現行57件に合わせる。

1. `{ label: "Wikipedia", url: "https://ja.wikipedia.org/wiki/" + encodeURIComponent(name) }`
2. `{ label: "公式サイト検索", url: google("氏名 公式サイト") }`
3. `{ label: "X (Twitter) 検索", url: google("氏名 site:x.com OR site:twitter.com") }`

Wikipedia の存在確認はしない（brief の non-goal）。選出職サンプルでは95%着地。残差は平仮名通称など表記ゆれで、この diff では扱わない。

**2. map key が `m_` で、`ministry` が非 null で、`liveSlugs` がその `slug` を含む**

1. `{ label: "{ministry.name}の発言者一覧", url: "/gov/{ministry.slug}" }`
2. `{ label: "所属・経歴を検索", url: google("氏名 省庁名") }`

slug も表示名も戻り値から作る。対応表を複製しない。

**3. それ以外の `m_`（省庁不明、またはその省庁の `/gov` ページが今のビルドに無い）**

1. `{ label: "所属・経歴を検索", url: google(role があれば "氏名 役職全文"、なければ "氏名") }`

`name` が空または非文字列なら検索語は `memberId`。0本にはしない。省庁を role キーワードから推測しない（第二の判定正本になる）。汎用 `/gov` を所属不明者へ付けない。

**禁止**

- `m_` キーのメンバーに、hostname が `wikipedia.org` で終わる URL（`ja.` / `en.` / `ja.m.` を含む）を付けない
- `is_elected_member`、`BUREAUCRAT_ROLES`、`rank`、`party` による分岐を新コードに残さない
- 既存リンクのスキップ・追記

### `/gov` の 404 防止

`/gov/[slug]` は `dynamicParams = false`。生成される slug は「`getMemberMinistry` が解決し、かつ1件以上発言があるメンバーがいる省庁」だけ（`src/lib/data.ts` の `getMinistryRosters`）。

enricher は TypeScript の `data.ts` を import しない。同じ条件をこう再現する。

1. `members.json` と同じディレクトリの `threads/` を読む（validate-data と同じく `.json` かつ `.progress.json` でないもの）
2. 読めないファイルは skip（validate-data と同じ。enrich を threads 破損で落とさない）
3. `spokenIds` = 1件以上 speeche がある `memberId`
4. `liveSlugs` = `spokenIds` に入り、かつ `getMemberMinistry(member)`（保存オブジェクトのまま。正規化しない）が非 null のメンバーから集めた slug

`threads/` が無い・空なら `liveSlugs` は空。その場合 `/gov` は出さず検索だけ。exit は 0 のまま。

現行データでは解決済み slug と roster の差は 0。発言0の省庁解決メンバーは5人いるが、同僚が話すので slug 自体は live。この5人には `/gov` を付けてよい。

### 言語と CLI

`scripts/enrich-members.mjs`。`getMemberMinistry` は `../src/lib/ministry.mjs` から import（sitemap / IndexNow と同じ）。

```
node scripts/enrich-members.mjs
node scripts/enrich-members.mjs --members-path /tmp/x/members.json
```

既定の members パスは `import.meta.url` 基準で repo の `data/members.json`。CWD 相対にしない。threads は `join(dirname(membersPath), "threads")`。隠れた環境変数は使わない。

公開する純粋関数:

```js
export function buildMemberLinks(memberId, member, { ministry, liveSlugs })
export function enrichMembers(members, { liveSlugs })
```

CLI がファイル I/O・liveSlugs 計算・no-op 判定・atomic write を担う。

### 入力の失敗の扱い

| 入力 | 動作 |
|---|---|
| ファイルなし / JSON でない（パース失敗） | 書かない。非ゼロ終了 |
| トップレベルがオブジェクトでない（`[]`・文字列・数値など） | 書かない。`::error::` annotation を出して exit 0。publish は続行 |
| メンバー値が非オブジェクト | 素通し（`links` を触らない）。警告に該当キーを名指しして exit 0 |
| 個別の `name` が空・非文字列 | ジョブは落とさない。検索語を `memberId` にして1本出す |

> **[Gate3 で覆された — 2026-09-05]** 元の指示（Gate2 確定時点）は「トップレベルがオブジェクトで
> ない」も「ファイルなし / JSON でない」と同じ行 = 書かない・非ゼロ終了だった。**実装では採らない
> ことにした。** レビュアーの指摘: 同じジョブの他の消費者（`validate-data.mjs` の
> `checkMembers`・`generate-feeds.js`・`generate-sitemap.mjs`）は全員この形（`[]` など）を
> 生き延びる — `Object.entries([])` は単に空を返すだけでクラッシュしない。落ちるのは
> `enrich-members.mjs` だけだった。加えて、この形は今日的な原因（hand edit・不正な merge）で
> 生まれるので **HEAD に既に入っている** — その朝の実行が壊したものではなく、この実行が
> exit 1 しても直らない。**なのに** このステップは `bash -e` の下、`git add
> data/members.json` の数ステップ手前に座るので、非ゼロ終了はその朝に組み上がった無関係な
> threads を巻き込んで消す。壊れたファイルは直らないまま、無実の threads だけ失う二重の損失。
> **正しくは**: 何も書かず、`::error::` annotation でその旨を記録し、exit 0 で publish を
> 続行する。ジョブ全体を赤くする責務は `.github/workflows/daily-batch.yml` の最終ステップ
> （`Fail the run on a systemic summary failure`）へ、`members_shape_invalid` という
> 独立した step output 経由で移した — `held`/`abandoned`/`broken_json` と同じ形。
> 実装は `scripts/enrich-members.mjs` の `main()`（非ゼロ終了だった分岐を annotation + exit 0
> に変更）と `.github/workflows/daily-batch.yml` の `enrich_members` ステップ・最終失敗ステップ。
> 詳細は `.superpowers/sdd/2026-09-04-member-links-rewiring/gate3-fix-report.md`。

> **[Gate2 で覆された — 2026-09-04]** 元の指示は「メンバー値が非オブジェクト」の行も
> 「同上（ファイル全体が壊れている） = 書かない・非ゼロ終了」だった。**実装計画側では採らない。**
> 理由: このステップは `bash -e` の下、`git add data/members.json` の数ステップ手前に座る。
> ファイル全体が読めないことと、1行の値が変なことは別の失敗で、後者を理由に非ゼロ終了すると、
> その朝に組み上がった threads ごと消える — #52 / #74 で一度ずつ実際に起きた種類の増幅を、
> 別の入り口から持ち込むことになる。**正しくは**、不正な行はそのまま素通しし（`links` を触らず）、
> 警告に該当キーを名指しして exit 0 のまま続行する。実装は `scripts/enrich-members.mjs` の
> `enrichMembers()`（素通しの本体）と `main()` の `skipped` 収集（警告の名指し）。
> 詳細は `docs/superpowers/plans/2026-09-04-member-links-rewiring.md`。

スクリプト自身の例外（bug）は非ゼロのまま上げる。`|| true` / `continue-on-error` / 日付条件 / 成否を捨てる shell 分岐は付けない。

これは brief の「失敗は朝を止めてよい」を、**コードとファイル単位の契約違反**に適用したもの。threads 破損で朝を落とす #52 とは別で、そちらの fail-open をここへ移植しない。`--fix` の直後に enrich が黙ってスキップすると、空リンクの追加分がそのまま commit される。それが今回止める失敗形。`generate-feeds.js` / `generate-sitemap.mjs` も同じ位置で既に crash しうる。

### 全件再生成と no-op

1. 読む → shape 検証
2. キー順・各メンバーのフィールド順を維持したコピーを作り、`links` だけ置換
3. 新旧オブジェクトを `JSON.stringify(x)` 同士で比較（メモリ上。元ファイルの空白差は「リンクが変わった」にしない）
4. 同じなら書かない。`Member links unchanged` を出して exit 0
5. 違うときだけ `JSON.stringify(next, null, 2) + "\n"` を atomic writer へ

### atomic writer

`scripts/validate-data.mjs` の `writeJsonAtomic`（44–65行）を **手順を変えずに** `scripts/lib/jsonio.mjs` へ移す。シグネチャは現行どおり `(path, text)`。Python の `write_json_atomic(path, obj)` と混ぜない。

手順（現行を写す）:

同一ディレクトリの temp → descriptor へ `writeFileSync`（`writeSync` は短書きする）→ file fsync → close → rename → directory fsync（best-effort）→ 失敗時は temp だけ消して元を残す。

import するのは `validate-data.mjs` と `enrich-members.mjs` だけ。第三の写しを足さない。atomic 手順の「改良」をこの diff に混ぜない。

### 日次配線

`daily-batch.yml` の `Validate and generate` を3つに割る。

```text
validate-data.mjs --fix
→ enrich-members.mjs
→ generate-feeds.js / generate-sitemap.mjs
→ metrics
→ commit
```

`--fix` が欠落メンバーを足した**あと**で enrich する。逆順は禁止。空の日程でも毎回走らせる。

`package.json` の `build`:

```
node scripts/validate-data.mjs --fix && node scripts/enrich-members.mjs && node scripts/generate-feeds.js && node scripts/generate-sitemap.mjs && next build
```

Vercel の `--fix` がメンバーを足しても、そのデプロイの SSG はリンク付きを読む。no-op ならファイルに触らない。

### UI

`member-profile-view.tsx` の links 行:

- `url` が `/` で始まる → `next/link`、同じタブ、`open_in_new` なし
- それ以外 → 現行どおり `<a target="_blank" rel="noopener noreferrer">` + アイコン

役職の `/gov` リンク（113–119行、既に `getMinistrySlugs` でゲート済み）は残す。`links` の `/gov` は chip と MCP 用。重複してよい。描画だけ外部扱いにしない。

### テスト（先に赤、実装後に緑。壊して赤になることを確認する）

受け入れコマンド:

```
npm run lint && npm run validate
python -m pytest scripts/tests
```

pytest の skip 数は 0。PyYAML 未導入の skip は合格にしない。依存は `requirements-dev.txt`。Python 3.12。

**A. 生成器（fixture。committed JSON を正にしない）**

一時ディレクトリに `members.json` + 必要なら `threads/*.json` を書き、`node scripts/enrich-members.mjs --members-path ...` を subprocess する。

1. 省庁解決できる `m_` + その省庁に発言あり → `/gov/{slug}` と氏名+省庁名検索。slug は `MINISTRIES` に実在
2. 省庁解決できない `m_` → 検索1本。Wikipedia なし
3. 非 `m_` → Wikipedia + 公式サイト検索 + X検索
4. map key が `m_`、値に `id` なし、`role` 空、`name === key` → 1本以上。Wikipedia なし
5. `rank: "minister"` かつ `role: "気象庁長官"` の `m_` → Wikipedia なし。省庁が解決でき live なら `/gov/jma`
6. `party: "記者"` の `m_` → Wikipedia なし
7. 省庁は解決できるが、その slug の発言者が threads にゼロ → `/gov` を付けない（検索だけ）
8. 同じ fixture で2回。2回目は inode・`mtime_ns`・バイト列が不変（同一テキストの atomic rename を no-op と誤認しない）

**B. 公開契約（committed `data/members.json`）**

9. 全員の `links` が非空配列。各要素の `label` / `url` が非空文字列
10. map key が `m_` の URL について、hostname が `wikipedia.org` で終わらない（サブドメイン込み）
11. `getMemberMinistry` 相当が非 null かつ slug が live の `m_` は、`/gov/{そのslug}` を持つ

9–10 だけでは生成器が死んで古いファイルのまま緑、になる。A が本体。B は「commit されたデータが契約を満たす」側。

**C. 配線**

12. `test_systemic_failure.py` に追加。`yaml.safe_load`。`id: enrich_members` の step が `node scripts/enrich-members.mjs` を含み、`|| true` なし、`continue-on-error` が true でない。**validate `--fix` より後、feeds/sitemap より前**（存在だけ見ると Claude の誤順が緑になる）

**D. atomic writer**

13. `test_the_js_half_of_the_rule_holds_too` を更新する。`validate-data.mjs` と `enrich-members.mjs` が `scripts/lib/jsonio.mjs` の `writeJsonAtomic` を import している。どちらも `writeFileSync(MEMBERS_PATH)` していない。helper 本体に temp（同ディレクトリ）、descriptor への writeFileSync、file fsync、rename、directory fsync、失敗時 unlink が残っている。

**E. CI**

14. `ci.yml` の `python-tests` が `actions/setup-node@v7` で node 22 を入れていることを、既存の workflow 契約テストと同じやり方で固定する（新テストでも、既存 `test_workflow_actions.py` 系への1本でも可）

### 実装順

1. テストを先に足して赤を確認
2. `jsonio.mjs` 抽出（手順変更なし）+ validate-data の import 差し替え
3. `enrich-members.mjs` + Python 削除
4. UI
5. daily-batch / package.json / ci.yml
6. 本番 `members.json` を1回再生成して commit
7. CLAUDE.md の「validate-data.mjs が atomic を内包」を「`scripts/lib/jsonio.mjs` を validate-data と enrich-members が import」へ更新。規則本文は写さない
8. 受け入れコマンドを実測

### やらないこと

- bio / CTR / タイトル語順
- Wikipedia API
- `m_` の政治家（`m_b0533ae2` 石破 茂 など）を rank/party で救い出す。それは members パイプラインの ID 品質の別仕事。非 `m_` 側に正規エントリがある（`ishibashigeru`）
- `ministry.mjs` 以外の省庁判定
- Node 内に atomic writer の写しを増やす
- enrich を date ループや news の `|| true` に乗せる

### 初回 diff の見方

約1,300件の `links` が付く。レビューは規則・件数・fixture。JSON 全行の目視に依存しない。

想定の内訳（現行データ、実装時に再集計）:

- 非 `m_` 652 → Wikipedia + 検索2本
- `m_` 省庁解決 395 → 現行は全員の slug が live → `/gov` + 検索
- `m_` 未解決 251 → 検索1本（委員50 / 議員43 / role空31 が上位）

---

## 4. 残存リスク

1. **非 `m_` の Wikipedia は 404 をゼロにしない。** サンプル40人中2人は平仮名通称で外れた。API 確認は non-goal のままなので、この残差は残す。消す選択（Codex 原案）よりコストは小さいが、「着地を保証した」とは言えない。

2. **`m_` 空間の政治家・重複。** `石破 茂` が `m_b0533ae2` と `ishibashigeru` の両方にいる。前者は検索1本。rank/party で救うと `気象庁長官` が政治家になる。ID 付与側の別 issue。

3. **検索1本だけのページは薄い。** 未解決251（委員・議員・空 role・記者・事務局）と stub 31（`q=m_hash`）は、経歴を求めて来た人には足りない。bio が non-goal である以上、この diff の効果を CTR で測ると失望する。成功指標は「全員が1本以上」「`m_` に Wikipedia が無い」「配線がテストされている」まで。

4. **enrich のコードバグはその朝の threads publish を止める。** commit 直前の必須ステップになる。`generate-feeds.js` と同じ形。annotation して通すと `--fix` の空リンクが commit されるので採らない。初回の朝は特に、ステップ失敗＝その日の threads が runner ごと消える。

5. **`getMemberMinistry` の前方一致は省庁再編で静かに減る。** テスト11は「解決できた人は `/gov` を持つ」しか見ない。解決人数の下限はピンしない（magic number が腐る）。減ったら検索フォールバックに落ち、契約テストは緑のまま。気づく手段はこの diff の外。

6. **`npm run lint` は `scripts/**` を見ていない**（`eslint.config.mjs:16-17`）。新ファイルは lint 受け入れの対象外。eslint を scripts へ広げるのは別仕事。

7. **stub 31件の検索は無意味。** `--fix` が `name: speaker || memberId` で作り、speaker が欠けるとハッシュになる。リンク契約は満たす。中身の修正は validator 側。

8. **共有 helper 抽出は validate-data の回帰窓。** 手順変更を混ぜなければ小さい。`npm run validate` と更新後の `test_jsonio.py` が番人。
