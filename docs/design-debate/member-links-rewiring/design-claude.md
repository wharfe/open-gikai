# design A (Claude): /m の外部リンクを全員に行き渡らせる

## 起草中に判明した、brief の2つの欠陥

**(1) `m_` プレフィックス ≠ 官僚。** 実測: `getMemberMinistry` が省庁を引けるのは
`m_` 615人中 **395人 (64%)**。残り220人の role には `中央大学文学部教授` / `記者` /
`公益社団法人経済同友会常務理事` / `国立社会保障・人口問題研究所...部長` / `衆議院事務総長`、
そして **`内閣総理大臣`** が含まれる。`m_` は「官僚」ではなく
**「議員名簿と突き合わなかった発言者」** を意味する。
→ brief の受け入れ基準2「`m_` に Wikipedia を付けない」は**そのままでは誤り**。差し替える（下記）。

**(2) atomic writer の3つ目の写しが生まれる。** `writeJsonAtomic` は
`scripts/validate-data.mjs:44` の **非 export のローカル関数**。新しい node スクリプトが
同じことをすると写しが3つになる。CLAUDE.md が2つ目（Python/JS）を許したのは
「node が Python を import できない」という不可避の事情による。JS 同士にその事情は無い。

## 設計

### 判別軸を「`m_` かどうか」から「Wikipedia が当たるか」へ

配線して困るのは 404 リンクをばらまくこと。ならば判別軸は所属ではなく
**「その人に Wikipedia 項目が期待できるか」**であるべき。3分類にする:

| 区分 | 判定 | 付けるリンク |
|---|---|---|
| **A. 選出職** | `id` が `m_` でない、または `rank ∈ {pm, minister, viceminister}`、または `party` あり、または role が政治的肩書で始まる | Wikipedia + 公式サイト検索 + X検索（現行のまま） |
| **B. 政府参考人** | `getMemberMinistry(member)` が省庁を返す（395人） | **`/gov/{slug}` 内部リンク** + 「氏名 省庁名」検索 |
| **C. その他の参考人** | 上のどちらでもない（220人。学者・記者・団体役員・議院事務局） | 「氏名 role先頭語」検索のみ |

**Wikipedia は A のみ。** B と C は存在確認ができず、確認する手段（Wikipedia API）は
毎朝1,241人分の外部呼び出しとレート制御を新規に発生させる（brief の non-goal）。
検索リンクは 404 にならず必ずどこかに着地するので、確認不能な相手にはこちらを当てる。

**現行 Python の `is_elected_member` はこの概念を既に持っている。** 欠陥は
`generate_links` が **elected 判定より前に無条件で Wikipedia を append している**
3行の順序だけ。つまり区分 A/非A の線は移植であって新規設計ではない。

### 言語: node へ移す

理由は `ministry.mjs`（省庁判定の正本）を import するため。前例は
`generate-sitemap.mjs` / `notify-indexnow.mjs` が既に同じことをしている。
LLM も外部 API も使わない純粋な文字列変換なので Python である必然性が無い。
**`scripts/enrich_members.py` は削除する**（2つの enricher を残さない）。

### atomic writer は抽出して共有する

`scripts/validate-data.mjs:44` の `writeJsonAtomic` を **`scripts/lib/json-atomic.mjs`** へ
切り出し、`validate-data.mjs` と新スクリプトの両方が import する。JS 側の写しは1つのまま。
`test_jsonio.py::test_the_js_half_of_the_rule_holds_too` が現在 `validate-data.mjs` を
名指しでピン留めしているので、**そのテストの参照先も一緒に動かす**。

### 冪等性

全員分を決定的に再生成 → 現行ファイルの内容と**シリアライズ結果を比較**し、同じなら書かない。
差分が無い日は `members.json` に触らないので、`git status` に出ず、余計な data commit も出ない
（CLAUDE.md の停滞検知が数える直近10件の窓を無関係なコミットで埋めない）。

### 配線

`daily-batch.yml` の **`validate-data.mjs --fix` の直前**に1ステップ追加。`|| true` は付けない。
その位置である理由: enrich → validate → `git add data/members.json` の順なら、
enrich が壊した JSON を validate が同じ朝に捕まえる。逆順だと検査済みの後に書き換わる。

## 受け入れコマンド

```
npm run lint && npm run validate
python -m pytest scripts/tests
```

新規テスト（**いずれも壊して赤くなることを確認してから緑にする**）:

1. `members.json` の全員が links を1本以上持つ
2. **Wikipedia リンクを持つのは区分 A のみ**（brief の「`m_` に付かない」から差し替え。
   `内閣総理大臣` の role を持つ `m_` メンバーが A に入り、Wikipedia を持つことを含める）
3. 区分 B の全員が `/gov/{slug}` 内部リンクを持ち、その slug が `MINISTRIES` に実在する
4. 同じ入力で2回走らせ、2回目がファイルに書き込まない（冪等）
5. `daily-batch.yml` に enrich ステップが存在し、`|| true` が付いていない
6. `json-atomic.mjs` の写しが JS 側に1つしか無い（`test_jsonio.py` の JS 半分を移設・拡張）

## 残るリスク

- **区分 C の220人は検索リンク1本だけになり、ページの薄さは解消しない。** bio が別立ての
  non-goal である以上ここは埋まらない。「404 を出さない」は達成するが「経歴を求めて来た人に
  経歴を出す」は達成しない。**この diff の効果を CTR で測ろうとすると失望する**ので、
  受け入れ基準に CTR を入れない（brief の non-goal と一致）。
- **`getMemberMinistry` の 64% という数字は role 文字列の前方一致に依存**しており、
  省庁再編や表記ゆれで静かに下がりうる。テスト3は「B の全員が持つ」ことしか見ないので、
  B が縮んでも緑になる。カバレッジの下限を別途ピン留めするか、下がったら気づく手段が要る。

## 起草中の検算で出た、既存コードのバグ（設計に織り込む）

3分類を実データで走らせた結果: **A 676 / B 382 / C 240**。C の中身を見て1件見つかった。

**`林芳正（内閣官房長官, rank=minister）` が C に落ちる。** 原因は2つの判定が両方すり抜けること:

1. `is_elected_member` の官僚キーワード判定が **部分一致** で、`内閣官房長官` の中に
   `官房長` が含まれるため「官僚」と誤判定される（`rank=minister` を見る前に false を返す）
2. `getMemberMinistry` は `内閣官房長官` を `POLITICAL_TITLE_PREFIXES` で弾いて null を返す

両方から漏れた結果、現職の官房長官が「検索リンク1本」の区分に入る。
`衆議院事務総長` / `参議院事務総長`（議院事務局）も同じく C。

**対処: 判定の順序を変える。** 政治的肩書と `rank` を**先に**見て A を確定させ、
そのあとで官僚キーワードの部分一致を評価する。順序の問題なので新しい判定は要らない。
`POLITICAL_TITLE_PREFIXES` は `ministry.mjs` が既に持っているので、node へ移すことで
そのまま使える（Python に残す限りこの共有はできない ＝ 言語移行の副次的な利得）。

**受け入れテストに追加**: 区分 A に `rank ∈ {pm, minister, viceminister}` の全員が入ること
（`内閣官房長官` を含む。部分一致バグの再発をこれで止める）。
