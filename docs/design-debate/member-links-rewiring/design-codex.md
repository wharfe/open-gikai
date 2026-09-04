# 1. 前提と解釈

本変更は、`data/members.json` の `links` を日次で決定的に派生させる補助データ処理である。発言要約の内容には影響せず、LLM・外部API・過去実行状態を使わない。

「全メンバーが、404 に着地しないリンクを持つ」は、次のように解釈する。

- 検索結果ページと、ビルドされることを確認できる内部ページだけを自動生成する。
- Wikipediaの人物ページは、URLを構成できてもページの存在を保証できない。Wikipedia APIによる確認もnon-goalなので、自動生成対象から全員分除外する。
- 既存のWikipediaリンクも、全件再生成時に削除する。既存値を保存すると、新規メンバーと既存メンバーで結果が変わり、「同じ入力から決定的に再生成」が成立しない。
- 将来、確認済みWikipediaリンクを残したい場合は、自動生成される `links` とは別の手編集・出典付きフィールドとして設計する。今回は扱わない。

官僚判定には `member.id` ではなく、`members.json` の外側のキーを使う。現行データでは `m_` キーが646件ある一方、うち31件は `validate-data.mjs --fix` が追加した補完エントリで、値の中に `id` がない。このため、値だけを走査すると要件上の官僚を取りこぼす。

```js
for (const [memberId, member] of Object.entries(members)) {
  const normalizedMember = { ...member, id: memberId };
}
```

`m_` は厳密には省庁官僚だけでなく、審議会委員、参考人、記者、事務局なども含む。現時点ではこれを新しい人物分類で細分化しない。`getMemberMinistry()` が省庁を返した場合だけ省庁固有リンクを生成し、`null` の場合は役職を含む検索リンクへフォールバックする。

`bio`、プロフィール本文、タイトル、CTR改善、リンク先の内容評価は対象外とする。

# 2. 設計案

## 2.1 構成

変更対象は次のとおり。

- `scripts/enrich_members.py`
  - 削除する。
- `scripts/enrich-members.mjs`
  - Node版のリンク生成・CLIエントリポイントを追加する。
- `scripts/lib/jsonio.mjs`
  - Node用のatomic JSON writerを置く。
- `scripts/validate-data.mjs`
  - 現在内包しているatomic writerを上記共通モジュールへ移し、importして使う。
- `.github/workflows/daily-batch.yml`
  - validation後、feeds/sitemap生成前にenrichステップを追加する。
- `scripts/tests/test_member_links.py`
  - データ契約と冪等性のテストを追加する。
- `scripts/tests/test_systemic_failure.py`
  - ワークフロー配線のfenceを追加する。
- `scripts/tests/test_jsonio.py`
  - 移動後も両Node writerが共通atomic helperを使うことを検査する。
- `data/members.json`
  - 新ルールで全件再生成した結果をコミットする。
- `CLAUDE.md`
  - `validate-data.mjs` がatomic処理を内包するという記述を、共有Node helperを使用する記述へ更新する。

PythonとNodeの間でatomic writerを共有できないことは不可避だが、Node同士でコピーを増やす理由はない。既存実装を `scripts/lib/jsonio.mjs` へそのまま移し、Node側の正本を一つにする。

## 2.2 リンク生成API

`scripts/enrich-members.mjs` は次の純粋関数を公開する。

```js
export function buildMemberLinks(memberId, member) {}
export function enrichMembers(members) {}
```

CLIは次の形式とする。

```text
node scripts/enrich-members.mjs
node scripts/enrich-members.mjs --members-path /tmp/members.json
```

`--members-path` の既定値は `data/members.json`。テストが実データを書き換えずに実行できるよう、隠れた環境変数ではなく明示的な引数にする。

入力についてはfail closedとする。

- ファイル不存在
- JSONのparse失敗
- トップレベルが配列または非オブジェクト
- メンバー値が非オブジェクト
- `name` が空または文字列でない

これらは非ゼロ終了とし、既存ファイルを書き換えない。空のリンクを静かに生成することは今回防止したい失敗形と一致するため、許容しない。

## 2.3 生成規則

### `m_` メンバーで省庁が解決できる場合

```js
const ministry = getMemberMinistry({ ...member, id: memberId });
```

次の2本を固定順序で生成する。

```json
[
  {
    "label": "財務省の発言者一覧",
    "url": "/gov/mof"
  },
  {
    "label": "所属・経歴を検索",
    "url": "https://www.google.com/search?q=氏名+財務省"
  }
]
```

- 省庁判定は必ず `src/lib/ministry.mjs` をimportする。
- URLのslugと表示名も返された `{ slug, name }` から作り、対応表を複製しない。
- 現行の `/gov/[slug]` は、発言実績から `generateStaticParams()` を生成する。新規メンバーも発言から作られるため、その省庁のrosterが生成される。
- 現行データでも、解決済みメンバーの省庁slugはすべて生成対象rosterに含まれることを移行時に確認する。

### `m_` メンバーで省庁が解決できない場合

省庁を推測せず、検索リンクを1本生成する。

```json
[
  {
    "label": "所属・経歴を検索",
    "url": "https://www.google.com/search?q=氏名+役職"
  }
]
```

`role` が空なら氏名だけを検索語にする。`課長`、`参事官`などの一般役職から省庁を推測するロジックは追加しない。これは `ministry.mjs` と競合する第二の省庁判定になるためである。

汎用の `/gov` リンクを全員へ付ける案も採用しない。審議会委員や記者へ「省庁別発言者一覧」を所属先として見せるのは誤解を招く。

### 非`m_`メンバー

新たな「議員らしさ」判定は作らず、すべて同じ2本を生成する。

```json
[
  {
    "label": "公式サイトを検索",
    "url": "https://www.google.com/search?q=氏名+公式サイト"
  },
  {
    "label": "Xを検索",
    "url": "https://www.google.com/search?q=氏名+site%3Ax.com"
  }
]
```

旧スクリプトの `is_elected_member()`、役職キーワード、party/rank分岐は廃止する。IDプレフィックス以外の人物分類を増やさず、非`m_`メンバーへ一律適用する。

検索URLは `URLSearchParams` で組み立てる。氏名や役職を独自に正規化せず、保存された文字列をそのまま使う。

## 2.4 全件再生成とno-op

入力オブジェクトのキー順と、各メンバーの既存フィールド順を維持したコピーを作り、`links` だけを生成結果で置換する。既存リンクとのmergeや重複除去は行わない。

処理手順は次のとおり。

1. ファイルを読み、parse・shape検証する。
2. 全エントリについて新しい `links` を計算する。
3. parse済みの旧オブジェクトと新オブジェクトを比較する。
4. 意味上同じなら書き込まず、`Member links unchanged` を出力して終了0。
5. 異なる場合だけ `JSON.stringify(next, null, 2) + "\n"` を作り、atomic writerへ渡す。

比較は生テキストではなく構造化データ同士で行う。空白や末尾改行だけが違うファイルを「リンク内容が変わった」とは扱わない。

atomic writerは現在の `validate-data.mjs` と同じ手順を維持する。

```text
同一ディレクトリの一時ファイルをopen
→ 全テキストを書き込み
→ ファイルfsync
→ close
→ rename
→ ディレクトリfsync（best effort）
```

途中失敗時は一時ファイルだけを削除し、元ファイルを残す。

## 2.5 日次データフロー

ワークフローでは順序が重要である。

```text
summarize
→ news enrichment
→ validate-data.mjs --fix
→ enrich-members.mjs
→ feeds / sitemap生成
→ metrics
→ commit
```

`validate-data.mjs --fix` はthreadから欠落メンバーを追加できるため、enrichはその後でなければならない。先にenrichすると、validationが直後にリンクなしメンバーを追加できてしまう。

`.github/workflows/daily-batch.yml` の現在の `Validate and generate` を次の3ステップへ分割する。

```yaml
- name: Validate data
  run: node scripts/validate-data.mjs --fix

- name: Enrich member links
  id: enrich_members
  run: node scripts/enrich-members.mjs

- name: Generate feeds and sitemaps
  run: |
    node scripts/generate-feeds.js
    node scripts/generate-sitemap.mjs
```

enrichステップには次を付けない。

- `|| true`
- `continue-on-error: true`
- 成否を無視するshell分岐
- 日付リストによる条件実行

全日程が空の日でも、既存メンバーのリンク契約を検査・修復するため毎回実行する。

## 2.6 テスト

要求された4契約を次のテストで固定する。

1. `test_every_member_has_at_least_one_link`
   - コミット済み `data/members.json` を読む。
   - 全エントリの `links` が非空配列であることを検査する。
   - 各要素の `label` と `url` が非空文字列であることも検査する。

2. `test_m_prefixed_members_never_link_to_wikipedia`
   - 外側のキーが `m_` で始まる全エントリを対象にする。
   - labelだけでなく、URLのhostnameが `ja.wikipedia.org` でないことを検査する。
   - ラベル変更で禁止を回避できない形にする。

3. `test_second_run_does_not_rewrite_members_file`
   - 一時ディレクトリへ、省庁解決可能な `m_`、解決不能な `m_`、非`m_`を含むfixtureを書く。
   - CLIを1回実行して生成結果を確認する。
   - inode、`mtime_ns`、ファイル内容を記録する。
   - 同じ入力で2回目を実行し、3値がすべて不変であることを確認する。
   - これにより「同じテキストをatomic renameしただけ」をno-opとして誤認しない。

4. `test_daily_batch_runs_member_enrichment_as_a_required_step`
   - `yaml.safe_load()` でワークフローをparseする。
   - `fetch-and-summarize` の `id: enrich_members` を特定する。
   - `run` が `node scripts/enrich-members.mjs` を含むことを確認する。
   - `|| true` がないこと、`continue-on-error` がtrueでないことを確認する。
   - validateより後、feeds/sitemapより前にあることも検査する。

加えて、既存 `test_the_js_half_of_the_rule_holds_too` は次を検査する形へ更新する。

- `validate-data.mjs` と `enrich-members.mjs` が共通の `writeJsonAtomic` をimportしている。
- どちらも `members.json` へ直接 `writeFileSync()` していない。
- 共有helperに temp、file fsync、rename、directory fsync、失敗時cleanupが残っている。

実装時の検証順序は、要求どおり各新規テストを対象コードより先に追加して赤を確認し、その後に実装・データ再生成して緑にする。最終受け入れは次の2本である。

```bash
npm run lint && npm run validate
python -m pytest scripts/tests
```

PyYAML未導入によるskipを合格扱いしない。pytest結果ではskip数も確認する。

# 3. 代替案と却下理由

## Pythonのまま省庁判定を移植する

却下する。`MINISTRIES`、最長一致、`m_`制約、政治役職blocklistが二重化し、政府組織変更時に片方だけ更新される。今回の主目的である「未配線を検知できる仕組み」に対して、新しいドリフト箇所を増やしてしまう。

## PythonからNode subprocessを呼んで省庁だけ解決する

却下する。言語間プロトコル、エラー伝播、シリアライズが増える一方、処理全体が文字列変換だけなのでPythonを残す利益がない。

## 既存リンクがあるメンバーをスキップする

却下する。古いWikipediaリンクが残り、規則変更が既存メンバーへ行き渡らない。追加時期によってリンク内容が変わるため、中立性と決定性にも反する。

## 既存リンクへ生成リンクを追加する

却下する。実行ごとの重複除去が必要になり、削除された規則のリンクが残る。`links` はこの処理の派生データとして完全置換する方が契約が明確である。

## 非`m_`メンバーにはWikipediaを残す

却下する。ページ存在を確認しない以上、「404に着地しない」というゴールを保証できない。特に新任議員はWikipediaページがない可能性があり、毎朝の決定的生成と相性が悪い。

## `m_` 全員へ省庁slugを推測する

却下する。現行データには審議会委員、民間参考人、記者、役職だけのエントリが含まれる。roleキーワードで推測すると誤所属を作り、`ministry.mjs` 以外の判定正本が生まれる。

## すべての`m_`へ `/gov` を付ける

リンク自体は404にならないが却下する。所属不明者や民間参考人に省庁所属を示唆する。内部リンク数を満たすことより、リンクの意味が正しいことを優先する。

## atomic writerを新スクリプトへコピーする

却下する。PythonとNodeの二重実装は言語境界上やむを得ないが、Node内の二重実装は避けられる。共有モジュール化し、既存テストも共有実装を監視する。

## enrichをvalidationより前に置く

却下する。`validate-data.mjs --fix` が後からリンクなしメンバーを追加でき、同じ実行のcommitに空リンクが混入する。配線テストが存在しても順序不良を防げない。

# 4. リスクと未解決点

- **Wikipediaの扱いはプロダクト判断を伴う。** 本案は404回避を優先して全自動Wikipediaリンクを削除する。議員の既存Wikipediaリンクを維持したい場合は、「404を保証しない」ことを受け入れるか、別途確認済みリンクのデータモデルが必要になる。

- **`m_` は官僚分類そのものではない。** 現行データでは646キー中251件を `getMemberMinistry()` が解決できない。これは省庁対応表の不足だけでなく、民間参考人などが同じID空間にいるためでもある。今回のdiffで分類体系を拡張しないことを明示する。

- **省庁改組でslug解決が変わる。** `ministry.mjs` の変更後、次の日次実行で全リンクが一括更新される。これは意図した動作だが、大きなデータdiffになり得る。

- **検索サービス自体の障害やbot対策までは保証できない。** 本案が避けるのは存在しない人物ページへの直接リンクであり、Google側の一時障害、地域制限、CAPTCHAまでは管理できない。

- **検索語が弱いメンバーがいる。** `name` がID同然の補完エントリや、roleが空の31件は、検索結果の有用性が低い可能性がある。ただし、推測で人物情報を足すより安全であり、元データ品質の改善は別タスクとする。

- **初回は大きな生成データdiffになる。** 約1,300件の `links` が置換される。レビューではスクリプトの規則、件数集計、代表fixtureを主に確認し、JSON全行の目視確認に依存しない。

- **共有atomic helper抽出の回帰リスクがある。** `validate-data.mjs` の既存処理を機能変更せず移動し、`npm run validate` と `test_jsonio.py` で利用経路を固定する。atomic手順の簡略化はこの変更へ混ぜない。

- **人手確認事項:** 「議員のWikipediaリンクも削除する」という本案の解釈だけは、実装開始前にプロダクトオーナーが明示的に承認するのが望ましい。承認されない場合、ゴール文を「官僚について404を避ける」へ狭める必要がある。

**完了**: 現行コードとデータ形状を踏まえ、全件再生成、atomic書き込み、日次配線、4契約テストを含む設計を提示した。

**次の一手**:

1. Wikipediaを全員から削除する解釈を確定する。
2. Gate1の合意後、テストを先に赤くしてから実装し、Gate3へ進む。

**検証**: リポジトリはread-onlyで調査し、実装・テスト実行・ファイル変更は行っていない。
