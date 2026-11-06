# 本体サイトの配信を Vercel → Cloudflare Workers へ移す手順（#102）

要は: 本体サイト（Vercel プロジェクト `open-gikai`）の配信を、Cloudflare Workers の静的アセット（Workers Builds でビルド）へ移す。
open-gikai-mcp（`apps/mcp`）は Vercel に残す。各手順の**前に人の許可を取る**（本番に副作用がある）。

前提: Cloudflare アカウントは Workers Paid 契約済み（2026-11-06）。ビルド出力が約 28,600 ファイルで、無料の上限 20,000 を超えるため。

## 1. ダッシュボードの設定（repo に置けない値）

Workers & Pages → Create → Import a repository → `wharfe/open-gikai`。**作成画面の時点で下の値にする**（既定の deploy コマンドのまま作らない）。
Git 連携は **PR1 をマージする直前**に作る。早く作ると、main への push ごとに本番ビルドが走り、PR1 の前は失敗し続ける。

| 項目 | 値 |
|---|---|
| Worker 名 | `open-gikai`（wrangler.jsonc の `name` と一致させる） |
| リポジトリルート | `/` |
| 本番ブランチ | `main` |
| ビルドコマンド | `npm run build && (python3 scripts/write_deploy_manifest.py \|\| true)`（マニフェストは公開を止めない。python3 が無い日でも） |
| デプロイコマンド | `npx --yes wrangler@4.158.0 deploy` |
| 非本番ブランチのデプロイコマンド | `npx --yes wrangler@4.158.0 preview`（既定の `wrangler preview` を版固定）。wrangler.jsonc の `"previews": {}` が必要 |
| Build watch paths: include | `*` |
| Build watch paths: exclude | `data/pending-batches/*` |
| ビルド変数 | `NEXT_PUBLIC_GA_ID`（Vercel の open-gikai の環境変数と同じ値）、`NODE_VERSION` = `22`（ci.yml / daily-batch.yml と同じ） |

設定したら、画面の値がこの表と一致することをもう一度読み合わせる。

wrangler は repo の依存に入れない（daily-batch / CI の `npm ci` にネイティブバイナリを乗せないため）。版はデプロイコマンドで固定する。上げるときはここと手元の確認コマンドを同時に変える。

watch paths の意味: Batches の sidecar だけを触る `chore(pipeline): persist pending batch` の push では、**通常は**ビルドしない。
公式の例外（2026-11-06 に文書で確認）: 変更 0 ファイルの push、3,000 ファイル以上か 20 commit 以上の push は、watch paths によらずビルドされる。
判定は push ごと（前回のビルドからの差分ではない）。`data/pending-batches/*` が配下の階層まで含むかは文書に明記が無い → 最初の実際の sidecar push で「ビルドが始まらない」ことを見る。
理由は保存量ではなく順序（sidecar の push のビルドが、後の daily-batch の push のビルドより後に終わると、新しい本番を古い内容で上書きする）。
既知の穴: サイトのビルドが失敗した直後に sidecar だけの push が来ても再ビルドされない。→ 鮮度確認（`scripts/check_site_freshness.py`）が検知する。

## 2. 記録（切り替え前に埋める）

| 項目 | 値 |
|---|---|
| apex `open-gikai.net` | A `216.198.79.1`、プロキシ: ＿、TTL: ＿ |
| `www.open-gikai.net` | CNAME `c429f94839b542c3.vercel-dns-017.com`、プロキシ: ＿、TTL: ＿ |
| Search Console 確認用 TXT | ＿（触らない） |
| 既存の Redirect / Cache / Transform Rules | ＿ |
| Vercel open-gikai の最後の READY | デプロイ ID ＿、commit ＿ |
| Workers Builds の初回ビルド | 所要 ＿ 分（ビルド ＿ / アップロード ＿）、python3 有無 ＿、メモリ/ディスクのエラー有無 ＿ |
| workers.dev の URL | ＿ |

## 3. 手順

0. **マージ前**: PR1 の CI が緑、PR1 ブランチの Vercel Preview が READY（並行期間の Vercel 本番を壊さない証拠）。
1. **非本番ビルドで環境を確かめる**: Worker 作成 + Git 連携の直後、移行ブランチの非本番ビルドが通ること。python3（validate-data が `gen_status.py` を呼ぶ）、所要時間（上限 20 分）、ディスク。駄目なら DNS に進まない。
2. **PR1 をマージ** → workers.dev の本番に出る（利用者はまだ Vercel を見ている）。
3. **HTTP 契約と遷移を workers.dev で確かめる**:
   - `python3 scripts/check_http_contract.py https://open-gikai.net --compare https://<workers.dev の URL>`
   - `E2E_BASE_URL=https://<workers.dev の URL> npx playwright test e2e/client-navigation.spec.ts`（全 spec は流さない: member-profile などは手元の `data/` と件数を比べるので、デプロイ済み commit とずれると誤って赤になる）
   - 404 は **`wrangler deploy` した本番の workers.dev** で見る（Preview では 404-page が効かない）。
   - 合格の表（これ以外の差が出たら止める）:

| 対象 | Vercel（現行） | Cloudflare（期待） |
|---|---|---|
| `/`、`/about`、スレッド | 200 HTML、`public, max-age=0, must-revalidate` | 同じ |
| `/about/` | 308 → `/about` | **307** → `/about` |
| `/index.html` | 404 | 404 か、307 で `/`（200 で二重配信なら止める） |
| `/about.html`、`/about/index.html` | 404 | 404 か、307 でスラッシュなしへ（200 なら止める） |
| 存在しないパス | 404 サイトの 404 ページ | 同じ（素の "Not found" は不合格） |
| `*.xml` | 200 application/xml | 同じ（charset の有無は可） |
| IndexNow の鍵 `.txt` | 200 text/plain、sha256 が一致 | 同じ |
| `/feed.xml` | 整形式、item の links_sha | 同じ links_sha（同じ commit を比べたとき） |
| sitemap のサンプル 30 ページ | 200×30 | 同じ |
| RSC `.txt` | 200 | 同じ。遷移テストが緑 |
| OGP 画像 | 200 **application/octet-stream** | 200 **image/png**（`_headers` で直る） |
| `/_next/static/*.js` | immutable 1 年 | 同じ（`_headers`）。JS の `text/javascript` は可 |
| `/_headers` | PR1 以後は 200（`public/_headers` が out/ にコピーされ、Vercel はそのまま配信する。中身は秘密でない） | 404（本文を返したら止める） |
| `/__deployment.json` | 404（Workers のビルドコマンドでしか書かれない） | 200 JSON、noindex |
| workers.dev | — | `X-Robots-Tag: noindex` |
| apex `open-gikai.net`（切り替え後） | noindex なし | noindex なし |
| `www`（切り替え後） | 307 → apex（同じパス・クエリ） | 307 → apex（同じパス・クエリ。301 にしない） |
| トップの HTML の GA ID | `G-…` が入っている | 同じ値（workers.dev の段階で確認） |

4. **鮮度確認**: `python3 scripts/check_site_freshness.py --url https://<workers.dev の URL>/__deployment.json --attempts 1` が 0。
5. **DNS 切り替え**（1 つの許可で続けて行い、その場を離れない）:
   - TTL が長ければ、別の許可で先に apex と www の TTL を 120 秒に下げ、元の TTL が過ぎてから行う。
   - apex: Worker → Settings → Domains & Routes → Custom Domain に `open-gikai.net` を追加。既存 A と衝突したら、記録済みの値を手元に置いて A を消し、すぐ追加する。
   - www（この順で。同じ名前の CNAME と AAAA は共存できない）:
     1. Redirect Rule を作る: ホスト `www.open-gikai.net` → `https://open-gikai.net` + 元のパス、**307**、クエリ保持。www がまだ DNS only で Vercel を向いている間は効かないので、先に作ってよい。
     2. vercel-dns の CNAME を消す。
     3. すぐ、プロキシした `AAAA www 100::` を作る。作れなければ、記録した CNAME を戻して止まる。
     4. `curl -sI https://www.open-gikai.net/about?x=1` が 307 で `https://open-gikai.net/about?x=1` を返すこと。
6. **切り替え後の確認**: 手順 3 の契約を `https://open-gikai.net` と `https://www.open-gikai.net` で。`uptime.yml` と `deploy-mcp` が緑。トップの HTML に GA ID がある。
7. **Vercel の Git 連携だけ外す**（open-gikai のみ。open-gikai-mcp には触らない）。ドメイン設定と最後の READY デプロイは残す（切り戻し用）。以後 Vercel の保存量は増えない。既存分はプロジェクト削除まで残る。
8. **48 時間 かつ 日次の push で鮮度確認が一致**したら、許可を得て Vercel プロジェクト `open-gikai` を削除。削除前に、プロジェクト名・環境変数・最後の READY の commit をここに控える。**削除後は DNS を戻すだけでは切り戻せない**（再作成が要る）。削除後、Vercel の使用量画面で open-gikai の Deployment Storage が減ったことを見る。
9. **PR2**: daily-batch に鮮度確認のジョブ（ビルド＋アップロードの実測に 10 分足した予算、下限 30 分、60 秒間隔。失敗は notify-on-failure へ）、uptime の sitemap を非 200 で失敗に、`vercel.json`・`scripts/vercel-ignore-build.sh`・そのテストの削除、CLAUDE.md / README の配信の記述。

## 4. 切り戻し（手順 8 の削除より前だけ）

1. Worker の Custom Domain `open-gikai.net` を外す。
2. 続けて A `open-gikai.net` `216.198.79.1` を、記録したプロキシ状態・TTL で作る。
3. www の Redirect Rule を無効化し、`AAAA www 100::` を消し、記録した CNAME を戻す。
4. HTTPS、`/about`、`/about/`、feed、sitemap、トップ → スレッドの遷移を確認。

作業 5〜10 分。利用者に見える時間は TTL とリゾルバのキャッシュ次第で、保証値ではない。
戻った Vercel は Git 連携を外した時点の内容のまま。新しい内容が要れば、別の許可で Git 再接続か再デプロイ。
