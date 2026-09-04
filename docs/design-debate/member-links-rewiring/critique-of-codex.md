# design-codex.md への反証（fresh Claude subagent, 2026-09-04）

## critical

### C1. 朝の publish を殺す fail-closed ステップを、commit の直前に置いている

設計 §2.2 は「ファイル不存在 / parse 失敗 / トップレベルが非オブジェクト / メンバー値が非オブジェクト
/ `name` が空か非文字列」で**非ゼロ終了**すると宣言し、§2.5 で `Validate data` と `Generate feeds` の
間に置く。`.github/workflows/daily-batch.yml:285` の `Commit and push data` には `if:` が無いので、
その手前のステップが落ちれば **commit も push も走らない**。ephemeral runner なので、その朝に
組み上がった threads は消える。

この repo はまさにその失敗形を避けるために設計されている:

- `daily-batch.yml:262-268`（metrics ステップのコメント）: 「this step runs under `bash -e` …
  **and the very next step is the commit.** A single corrupt `data/threads/*.json` therefore skipped
  the whole morning's publish」
- `scripts/validate-data.mjs:84-90`: 「In `--fix` mode … **errors do not set a non-zero exit —
  deliberately**, so one bad file cannot block the whole publish the way #52 did」
- ルート CLAUDE.md「The final push is the publish (#82)」

つまり設計は、**「データ由来の異常で落ちないよう意図的に fail-open にしてある validate-data.mjs の
真下に、データ由来の異常で fail-close するステップを置く」**。両者の矛盾を設計は一度も突き合わせて
いない。

brief の「失敗は朝を止めてよい」は `|| true` の禁止を言っているが、この repo は「`|| true`」と
「ステップを落とす」の二択ではない。第三の型が既に3箇所ある — `::error::` annotation を出して処理は
続け、**最後のステップで job を赤にする**（`validate-data.mjs:86-89` / `check_committable_json` の
`broken_json_check_failed` / `Fail the run on a systemic summary failure` at `daily-batch.yml:626`）。
設計はこの repo 固有のパターンを検討した形跡がない。

**修正の向き**: enrich は annotation + `enrich_failed=true` output を出して exit 0 し、既存の最終
ステップで赤にする。あるいは commit ステップより**後**に置く（links は翌朝の commit に乗ればよい
派生データで、その日のうちに出る必要はない）。

### C2. §1 の中心的主張（キーを id に正規化して31件を救う）は実データで効果ゼロ

設計 §1 は「値だけを走査すると要件上の官僚を取りこぼす」ので `{...member, id: memberId}` にすると
述べ、§2.3 のコード例もこれを前提にしている。実測:

```
m_ keys 646
entries WITHOUT id field 31   (すべて m_ キー)
m_ resolved(by key): 395 unresolved: 251
m_ resolved(by member.id, current): 395
```

**395 と 395。正規化しても解決件数は1件も増えない。** 理由は `src/lib/ministry.mjs:88-90` —
`getMemberMinistry` は `id` の前に `const role = member.role || ""; if (!role) return null;` を通る。
31件は全員 `role: ""` なので、`id` を足しても null のままになる。

しかもその31件は `validate-data.mjs:170-183` が `name: info.speaker` で作った補完エントリで、実測で
**31件すべて `name === キー`**（`m_f9112eed` のようなハッシュ）。設計の規則を当てると生成される
唯一のリンクは `https://www.google.com/search?q=m_f9112eed`。`name` は空でも非文字列でもないので
§2.2 の fail-closed は素通りし、テスト1（links>=1）は**これを合格にする**。

## high

### H1. 「m_ = 官僚ではない」だけでなく「m_ に政治家はいない」も成り立たない

```
m_ with non-empty party: 32
  記者 / 城内 実(大臣,政府) / 事務局 / 平(大臣,政府) / 石破 茂(内閣総理大臣,政府)
unresolved m_ の role 上位:
  委員 50 / 議員 43 / (空) 31 / 関係者 6 / 日本放送協会理事 6 / 専門委員 5 /
  課長 4 / 大臣 3 / 審議官 3 / 内閣総理大臣 2 …
```

**石破茂（内閣総理大臣）が m_ 空間にいる。** ID プレフィックスのみの分類だと、彼と `議員` role の
43名・`大臣` 3名は「所属・経歴を検索」1本だけになり、公式サイト検索と X 検索を失う。設計は `party`
という**既に data にあるシグナル**を「分類正本を増やさない」という理由で捨て、その代償として
少なくとも48件を誤分類する。

### H2. 4本の契約テストのうち2本は、生成器が壊れても緑のまま

テスト1（全員 links>=1）とテスト2（m_ に Wikipedia なし）は**コミット済み `data/members.json` を
読む**。生成器が壊れて enrich ステップが落ちれば `members.json` は更新されないので、両テストは
前回の良いデータに対して合格し続ける。MEMORY の `feedback_fail_closed_guards`（「a passing test may
reach its assertion by another path — break it to check」）が指す型そのもの。**生成器の出力に対する**
テストが要る。

### H3. `/gov/{slug}` の 404 を防ぐ fence が無く、一回きりの手動確認に委ねている

`/gov/[slug]` は `dynamicParams = false`（`src/app/gov/[slug]/page.tsx:7`）。roster は
`src/lib/data.ts:350-356` で `getMemberMinistry` が解決し **かつ発言実績がある**メンバーのみ。
リンク生成側の条件（省庁解決のみ）とページ生成側の条件（省庁解決 かつ 発言実績あり）が違う。
今日は成り立つ（roster 37 / link slugs 37 / dangling 0 / 発言ゼロのメンバー9名）が、
それを固定するテストは4本のどれにもない。

### H4. `writeJsonAtomic` の抽出で、既存 fence が空振りするようになる

`scripts/tests/test_jsonio.py:170-178` は `validate-data.mjs` の**呼び出し行の文字列だけ**を見る:

```python
assert "writeJsonAtomic(MEMBERS_PATH" in src
assert "writeFileSync(MEMBERS_PATH" not in src
```

関数本体を `scripts/lib/jsonio.mjs` へ移しても呼び出し行は残るので、**このテストは通り続ける**。
一方 docstring が守ると宣言している「temp→fsync→rename→fsync-dir の実体」はもうそのファイルにない。
CLAUDE.md が「AST fence は `scripts/**.py` しか歩かないので、このテストが唯一の番人」と明記している
前提が静かに壊れる。加えて (A) 級 diff に #57/#72 機構のリファクタを同梱することになる。

### H5. 受け入れコマンド `npm run lint` は新規ファイルを一切 lint しない

`eslint.config.mjs:16`: `globalIgnores([ ..., "scripts/**", "apps/**" ])`。新設する
`scripts/enrich-members.mjs` と `scripts/lib/jsonio.mjs` は両方 `scripts/` 配下。
**受け入れ2本のうち1本は新コードに対して何も検査しない。**

## medium

### M1. `/gov` 内部リンクを `links` に入れると、外部リンクとして描画される

`src/components/member/member-profile-view.tsx:160-168` は全 links を
**新規タブ + `open_in_new` アイコン + 素の `<a>`（`next/link` ではない）**で描画する。
設計 §2.1 の変更ファイル一覧にこのコンポーネントは無い。

### M2. Wikipedia 全削除の前提が、実測と合わない

ja.wikipedia API で非 m_ 652名を照会（2026-09-04、API のタイトル正規化で152件が集計から落ちた
粗い1パス。**下限**として読むこと）:

```
non-m_ unique names: 652 | wikipedia EXISTS: 485 | MISSING: 15
```

少なくとも 485/652 = 74% は実在し、明確に missing なのは15名（2.3%）。「404 をばらまく」は
少なくとも非 m_ については実測で成り立たない。現在 links を持つのは57件で、設計はこの生きている
57本を全部消して Google 検索リンクに置き換える — その Google の可用性も保証できないことは設計自身が
認めている。「保証できないから消す」を Wikipedia にだけ適用するのは一貫しない。

### M3. 検索クエリの品質を測る手段がない
unresolved m_ 251件の role 上位は `委員 50 / 議員 43 / (空) 31`。生成されるのは `q=氏名+委員`。
テスト1 はこれを全部合格にする。

### M4. `ci.yml` の python-tests に `setup-node` が無い
`.github/workflows/ci.yml:36-58` は `setup-python` のみ。テスト3は node CLI を subprocess 起動する
統合テストなので、runner 既定の node に暗黙依存する（#80 と同じ形）。

### M5. 「パイプライン」の定義が2つに割れる
`package.json:build` は `validate-data.mjs --fix && generate-feeds && generate-sitemap && next build`。
設計はワークフロー側だけ3ステップに割るので、**Vercel の本番ビルドは enrich を通らない**。

## low

- **L1**: `--members-path` の既定が CWD 相対。`ci.yml:59` は `cd scripts && python -m pytest -q` で走る。
- **L2**: テスト2は `ja.wikipedia.org` のみ禁止。`ja.m.wikipedia.org` / `en.wikipedia.org` は通る。
- **L3**: この diff の**成功指標が何一つ定義されていない**（テスト1〜4はどれも「リンクが存在する」しか見ない）。
- **L4**: 設計 §4 は「646キー中251件を解決できない」と書くが、内訳（委員/議員/空 role）を出していない。

## この設計の最も危険な単一の欠陥

**C1 — `Commit and push data` の手前に、データ由来の異常で非ゼロ終了するステップを新設したこと。**

この repo の中心的な不変条件は「commit/push だけがその朝の仕事を存在させる」（CLAUDE.md #82）であり、
`validate-data.mjs:84-90` と `daily-batch.yml:262-268` は、**その直前で落ちうるものを一つずつ潰して
きた履歴そのもの**である。しかも落ちる条件は**コードのバグではなくデータの形**で、
`data/members.json` は `validate-data.mjs --fix` が毎朝書き換える生成物である。つまり
「上流が壊れた日は、その日の threads ごと publish が消える」— #52 と #74 が二度にわたって塞いだ穴を、
リンク生成という補助レイヤーのために開け直すことになる。
