# OpenGIKAI - Project Instructions for Claude Code

## Project Overview

OpenGIKAI (議会) is a public media project that restructures Japanese parliamentary proceedings into a modern, social-media-like thread format. It supports multiple official sources — NDL (National Diet Library) Diet records, kantei.go.jp Prime Minister press conferences, and government council (審議会) meeting minutes — uses AI to summarize and structure them, and presents them on a static site.

## Tech Stack

- **Frontend**: Next.js 16 (App Router), TypeScript, Tailwind CSS. `output: "export"` — fully static, no server runtime at the root.
- **Deployment**: Two Vercel projects from one repo. Root → SSG frontend at `open-gikai.net`. `apps/mcp/` → dynamic MCP server.
- **Data Pipeline**: Python scripts + Claude API
  - Sliding 30-day lookback per run (NDL publishes transcripts with multi-day lag — see `scripts/fetch_ndl.py --lookback-days`).
  - `summarize.py --batch` uses Anthropic's Message Batches API for the summary phase (50% input/output discount).
  - System+user prompt instructions cached with `cache_control: ephemeral` (`scripts/pipeline/prompts.py` + `summarizer.py` / `grouper.py`).
  - `scripts/pipeline/news_ranker.py` (Haiku 4.5) filters Bing News candidates — auxiliary layer only.
  - SourceAdapter abstraction lives in `scripts/sources/`.
- **Data Sources**: NDL Diet Records API (`https://kokkai.ndl.go.jp/api/speech`), kantei.go.jp PM press conferences (`https://www.kantei.go.jp/`), cao.go.jp council meeting minutes (`https://www8.cao.go.jp/kisei-kaikaku/kisei/meeting/meeting.html`)

## Key Design Principles

1. **Political neutrality**: All speeches are processed with the same algorithm. No editorial selection. Prompts and logic are open-sourced for transparency.
2. **Source attribution**: Every summary links back to the original NDL transcript URL.
3. **AI transparency**: All AI-generated summaries are labeled as "Claude AI summary".
4. **Three reading levels**: `easy` (simple), `teen` (standard), `adult` (detailed) — each with specific tone and length rules.

## Summary Style Rules (Critical)

Summaries must express the **content of the speech itself**, not report on it.
- **Bad** (reporting style): "Pointed out that the AI definition is too vague and requested comparison with EU."
- **Good** (direct style): "The AI definition is too vague — recommendation engines could fall under regulation. Show the comparison with EU and impact estimates."

## Summary Layer Invariants (Critical for Neutrality)

The summary pipeline (`scripts/summarize.py` + `scripts/pipeline/grouper.py` + `scripts/pipeline/summarizer.py` + `scripts/pipeline/prompts.py`) is the core of OpenGIKAI's political-neutrality guarantee. The following are **non-negotiable**:

1. **Stateless**: No Memory tool, no carrying state across runs. A speech summarized today must produce the same *prompt* if re-summarized tomorrow on the same model — nothing about a previous run may reach it. (This used to read "the same output". It no longer can: see invariant 2 on why identical output is not something the API lets us pin. Do not resolve the wording back the other way — reading "same output" as a promise is what produced the #47 → #51 loop.)
2. **Deterministic**: no agent loops, no tool calls that branch on intermediate results, and **a fixed
   request shape** — the *set* of params a summary-layer request may carry is pinned, and adding one is a
   deliberate, tested act (`test_determinism.py`'s param allowlist; #58 tracks extending it to the
   synchronous call sites). Do not read anything below as loosening that.
   **Same input → same prompt.** That is the content claim, and it is deliberately narrower than it looks:
   the `system` block and the `messages` built from a given meeting are identical every time, because every
   request kind has exactly one message builder (below). It is *not* a claim that two runs put byte-identical
   HTTP bodies on the wire — `max_tokens` differs between the synchronous path and the batch path, and the
   synchronous paths issue a second request at a higher ceiling when the first is truncated. `max_tokens` is
   excluded from `compute_input_hash` for exactly that reason: it cannot change a response that already fit.
   The shape is fixed; one param's *value* varies, and only that one.
   **Send no sampling parameters.** The API rejects a *non-default* `temperature` / `top_p` / `top_k` on
   `claude-sonnet-5` with a 400 (omitting them, or passing the default, is accepted). This project forbids
   them outright anyway, because the failure is not a degraded day but a zero-thread run: #51 — the fix for
   #47 pinned `temperature=0` and took every request on 2026-08-05 to a 400. **Pinning sampling is therefore
   not a lever this project has.** What it does control, and must keep controlling, is: statelessness
   (1 above), one prompt builder per request kind (below), and **`thinking: {"type": "disabled"}` on every
   summary/grouping/outcome request** — Sonnet 5 turns adaptive thinking ON when the param is omitted, which
   both eats the `max_tokens` budget and reintroduces run-to-run variation. Be honest about the residual:
   given the same prompt, run-to-run identity of the *output* is the model's behavior, not something the API
   lets us pin any more.
   Every request goes through one of three builders — `grouper.build_grouping_request` /
   `build_outcome_request` and `summarizer.build_summary_request` — and the synchronous paths reuse the same
   message builders, so the daily run and a manual recovery run send byte-identical prompts. Build a request
   by hand and that guarantee is gone. `scripts/tests/test_determinism.py` guards both halves: it calls the
   builders for real values, and separately AST-sweeps every summary-layer module for a hand-rolled request
   that bypassed them. The recurring failure is a sibling script drifting from the rule, so it also *imports*
   `batch.py` / `bulk_batch.py` — they had been dead on import for months while a source-reading test
   reported them compliant.
3. **Prompt-only behavior**: All summarization rules live in `prompts.py` and are open-source. No retrieval-augmented prompts, no examples sourced from prior runs.
4. **No conversational UI inside OpenGIKAI's domain**: Chat answers are non-deterministic and break the "Claude AI summary" transparency label. External MCP server (separate Vercel project) is fine because clients bring their own LLM and we expose only deterministic JSON.

### What IS allowed
Auxiliary information layers (news enrichment, members extraction, sitemap generation, OGP image generation, ministry hub pages, lex-diff cross-links) may use LLM/agent patterns. They affect *which* context surfaces alongside a thread, not *what* a speech says. Example: `scripts/pipeline/news_ranker.py` uses Claude with temperature=0 and prompt caching to filter Bing News candidates — that is auxiliary, not summary. (It may keep `temperature=0` because it runs on Haiku 4.5, which still accepts sampling params; the summary layer may not, because it runs on Claude 5 — see invariant 2. Do not copy either setting across that line without checking the model.)

`src/lib/ministry.mjs` is a related auxiliary module: it deterministically maps a government-witness (政府参考人) member to a ministry from their `role` string (no LLM), powering the `/gov` hub pages, member-page breadcrumbs, sitemap-gov, and IndexNow. It is **plain ESM (+ `ministry.d.mts`)** rather than TS so the node-run build scripts (`scripts/generate-sitemap.mjs`, `scripts/notify-indexnow.mjs`) can import it — the repo has no tsx/ts-node. Politicians are excluded inside its API (m_-prefixed IDs only + political-title blocklist; `rank` is NOT used — it misclassifies bureaucrats). `data/lexdiff-mapping.json` (outbound law cross-links, consumed by `summarize.py`) is similarly auxiliary.

`scripts/pipeline/jsonio.py` holds `write_json_atomic`, and **every Python writer of a JSON file
this repo commits or re-reads goes through it** (#57/#72). `json.dump` serializes incrementally, so
a job killed mid-write or a full disk leaves a truncated document that IS the file from then on.
That one cause used to detonate in two unrelated places: a truncated `data/threads/{date}.json`
was named-and-skipped by the publish chain (so the run stayed green and committed it) and then
killed the Vercel build in `JSON.parse`; a truncated `data/raw/*.json` aborted `--collect-pending`
under `set -e` and stopped the morning's publish. Writes go to a temp file **in the same
directory** (`os.replace` is only atomic within one filesystem), fsync the file, rename, then fsync
the directory — nothing is replaced until serialization has fully succeeded, and the rename itself
is durable rather than just the bytes it points at. Its `indent=2` / `ensure_ascii=False` defaults
match the writers it replaced on purpose: changing either rewrites every committed data file on
the next run. Readers are guarded too, but that only decides how well the pipeline survives
corruption — this decides whether it creates any.

Read the scope of that rule literally, because two writers sit outside it and neither is an
oversight you should "fix" by widening the sentence. **`scripts/validate-data.mjs`** rewrites the
committed `data/members.json` under `--fix` — which `daily-batch.yml` runs immediately before
`git add data/members.json` — so it carries its own copy of the same temp→fsync→rename→fsync-dir
shape. It is a duplicate because the repo has no tsx/ts-node and node cannot import the Python one;
`test_jsonio.py::test_the_js_half_of_the_rule_holds_too` pins it, since the AST fence walks only
`scripts/**.py` and is structurally blind to it. **`apps/mcp/scripts/copy-data.mjs`** solves the same
problem with a different mechanism, because it copies a directory rather than writing a file (#73):
it stages into `apps/mcp/.data-staging-<pid>/`, verifies the staged copy, and only then swaps — old
aside into `.data-retired-<pid>/`, staged in, old removed. It also writes a `.bundle-manifest.json`
**inside** the bundle recording every file's size, and the "already bundled, skip the copy" path
(the CLI-deploy case, where there is no source to copy from) verifies against it instead of testing
that the entries exist. Existence was the whole check before, and a wrecked directory passes it.

The verification is four checks, and **which one catches what is the whole design** — none of them
subsumes another, so do not collapse them:

- **per-file sizes** see a copy that was cut short, which a count or a total cannot. Not hashes: the
  copy either arrives or does not, so hashing buys nothing here.
- **`JSON.parse` of every file, plus its top-level shape** sees a file that was *already* malformed
  when it was bundled. Sizes structurally cannot: the manifest is generated **from the copy**, so a
  short source file is recorded as short and matches itself forever after. Shape is checked for the
  same reason `_as_list_of_dicts` exists on the Python side — a thread file that parses to `{}` does
  not throw at request time, `loadThreads` just skips it, so it is zero threads under a green build
  rather than a loud failure. ~60MB parses in well under a second.
- **coverage against `INCLUDE`** sees an entry that never arrived at all — `threads/` missing
  outright. This is the one that must not be dropped when the manifest looks sufficient: a
  self-describing record agrees with itself about a bundle that never held `threads/`, so both the
  required set and each entry's expected shape (`ENTRY_KIND`) have to come from *outside* the thing
  being verified. The old `INCLUDE.every(existsSync)` had that property and a manifest alone does
  not; losing it silently is a green deploy serving zero threads.
- **unrecorded files the runtime would open** are a *failure*, not a warning: one the manifest never
  described is either content nothing vouched for or a fatal parse at request time. "Would open" is
  the load-bearing part, and the predicate is copied from `loadThreads` deliberately — `.json` and
  not `.progress.json`, inside an INCLUDE entry. Widen it and a stray `.DS_Store` breaks every CLI
  deploy; narrow it and the check stops covering what the runtime reads. Everything else is weight,
  and failing over it would turn a harmless leftover into a broken deploy. (A symlink **at any
  depth** is rejected for the neighbouring reason: it resolves, and sizes and parses fine, on the
  machine that built it — and contains nothing on Vercel.)

The same `verifyBundle` runs on both the staged copy (before the swap) and a reused bundle (the
CLI-deploy path), so the two cannot drift.

The swap's failure modes are handled in the order that keeps a complete copy existing at all times.
`.data-retired-<pid>/` is removed **only after** the replacement is actually in place — an
unconditional cleanup deletes the last good bundle in the one case that matters, where the install
rename fails *and* the rollback fails too, and that error names the directory the data is sitting
in. A SIGKILL in the one-rename-wide window between "old moved aside" and "new moved in" leaves the
previous bundle whole but not where anything looks for it, so the **next run restores it** from a
retired copy that verifies, before it decides anything else. That restore is why the sweep of
leftovers runs above the source check rather than at the top of the copy path — the source-less
path exits early, and it is the path where the rescue is the only thing standing between a mid-swap
kill and a failed deploy. Two retired copies that both verify is the one case it **refuses** rather
than guesses: a manifest records what a bundle holds, not when, so picking by directory order ships
the older one under a green build. A staging area is never rescued however complete it looks —
nothing ever decided it was good. Leftovers are swept **only for pids that are no longer running**:
a concurrent prebuild in the same working directory owns its staging area, and one of the moments
to delete it is right after it moved the good bundle aside. (That reads pids in our own namespace;
a shared bind mount written from two containers would need a different ownership marker.) `.gitignore` keeps these out of commits
but **not** out of a Vercel CLI upload, which carries the equally-ignored `apps/mcp/data/` — the
sweep, not the ignore rule, is what collects them.

`scripts/tests/test_mcp_bundle.py` drives the real script in a fake repo tree, including the
double-rename failure (injected by patching `fs.renameSync`).

On the read side the rule is that **unreadable is not absent**: `raw_unreadable` is its own HOLD
reason, because folding it into `raw_missing` would let a truncated file satisfy the abandon gate's
"nothing left to rebuild from" and delete a batch whose raw is sitting right there. The failure
carries the offending filename out with it (`RawUnreadable`) rather than being reconstructed by a
second pass — a re-read cannot see a file that is valid JSON of the wrong shape, and misreports a
transient error as a shape error. `src/lib/data.ts` and `apps/mcp/src/lib/data.ts` stay *fatal* on a
corrupt file (skipping would drop a whole date from the site under a green build) but now name the
file in the error.

Since #74 every reader **in `summarize.py`** is guarded, and **each one answers differently on
purpose** — the answer follows from what is lost, not from a house style, so do not "unify" them:

| reader | corrupt file costs | answer |
|---|---|---|
| cross-date links (`load_threads_from_other_dates`) | *this date's* links only — but the same file is fatal to the site build, and its own date is re-summarised only while inside the lookback window | skip + one aggregated warning that says so |
| first-run raw (`load_raw_meetings_for_date`) | one source's meetings, re-fetched next run (raw is gitignored, 30-day lookback) | skip + warning; **exit 3** only if the date then has no meetings at all |
| existing threads file, first-run resume | the only copy of that date's published work | refuse the date (**exit 3**) before any API call; file untouched |
| existing threads file, Collect's append (`ThreadsFileUnreadable`) | the same, plus the just-assembled threads | **hold** — the sidecar is kept, so a later run re-collects the same results |
| resume seed (`collect_processed_meeting_ids`) | nothing — it only *reseeds* what the row above then re-reads | warn + assume nothing is done; the refusal above is what decides |
| progress file (`load_progress`) | nothing durable: `*.progress.json` is gitignored and re-derived from the threads file | warn + treat as missing |
| lex-diff mapping (`_get_lexdiff_map`) | outbound law cross-links, an auxiliary layer | log + publish without the links |

The last three are the ones that make the sentence above true, and two of them were found only
because a test for the row above them failed: `collect_processed_meeting_ids` runs *before*
first-run resume reads the same file, so an unguarded shape there front-ran the refusal and turned
its exit 3 into an exit 1. **A guard that a second reader can reach the file ahead of is not a
guard** — when adding one, check what else opens the same path earlier in the call.

The claim stops at `summarize.py` on purpose, because it is checkable there and not elsewhere:
`scripts/validate-data.mjs` still reads `data/members.json` bare immediately before the commit step,
and `scripts/enrich-news.py` reads bare too (harmless only because `daily-batch.yml` runs it with
`|| true`). Both are outside this file and outside the Python AST fence.

"Guarded" here means shape as well as parse: `_as_list_of_dicts` runs *inside* each reader's
`try`, and raises `TypeError` precisely because that is already in `_RAW_READ_ERRORS`, so a
hand-edited file that parses into the wrong shape gets the same verdict as a truncated one. Without
it the failure escapes every guard and lands later — `[].extend("none")` appends four characters,
`list(some_dict)` appends its KEYS — as an `AttributeError` in `link_threads` or the grouper, i.e.
exit 1 and no publish, which is the outcome all of this exists to prevent.

That last row depends on an ordering that already existed: `_append_threads_to_date_file` runs
*before* `delete_sidecar`, which is why a failed append leaves the batch collectable. Do not move the
delete earlier. And note the deliberate asymmetry with `_load_meetings_for_date`, which raises
`RawUnreadable` for the same files the first-run path skips: on the resume path "no meetings" is
evidence the abandon gate deletes on, so a skip there would authorize a delete it never proved.

Still open: #57's option (b) — keeping a corrupt file out of the commit in the first place — is
**not** implemented (#75); (c), this writer, only stops the pipeline from *creating* one.

`scripts/pipeline/batch_state.py` is auxiliary persistence, not summary logic: it records an in-flight summary batch's id + grouping manifest (with per-thread `input_hash`) to a committed sidecar (`data/pending-batches/{date}.json`) so a timed-out batch resumes on a later run and assembles **without re-grouping** — same input → same request, so it upholds the invariants above rather than affecting them. The hash covers the content params only (`max_tokens` is excluded via `HASH_EXCLUDED_PARAMS`): the model is not told the ceiling, and excluding it is what lets a truncated request be re-issued at a higher one. Be precise about what that costs now that sampling cannot be pinned — a re-issue at `SUMMARY_RETRY_MAX_TOKENS` is the same *request* minus the ceiling, not a guaranteed-identical response. Anything that steers generation must stay hashed. **Bump `SCHEMA_VERSION` whenever `compute_input_hash` or the set of params fed to it changes** — pinning `temperature` took it to v3 and removing it again (#51) took it to v4, since each older version's hashes cover a param set ours no longer matches. `test_summary_request_param_set_is_pinned` keys the expected param set off `SCHEMA_VERSION` and keeps the per-version history, so the two are edited in the same place — but it cannot stop someone who edits both rows, so treat it as a prompt, not a fence. That matters because forgetting the bump does not surface as a version error: it surfaces as per-thread `input_hash mismatch — raw/prompt changed`, which sends the investigator to the raw data. Since #59/#61 that mismatch reds the run on the **first** morning (the reason is named in the annotation). Since #65 it is also *held*: no resubmit, no retry spent, and the sidecar is kept so the batch stays rescuable. It is a decision to make, not one to defer: the results expire ~29 days after submission, and since #69 the hold itself ends — past `ABANDON_AGE_DAYS`, **on a run where none of its manifest's meetings are in the raw on disk**, the sidecar is written off as a permanent loss (`abandoned_dates`) and deleted, because by then it is provably uncollectable. So deferring does not preserve the option; it spends it, loudly. (That absence is observed, never inferred from the age: widening `lookback_days` is how a human rescues a held sidecar, and that run re-fetches the raw, so a rescue must not be the thing that triggers the delete. `_reason_not_to_abandon` holds the whole rule and fails closed to "keep".) The annotation names the choices; **do not simply `git rm` the sidecar** — outside the lookback window the date is not re-summarized, which converts a recoverable batch into the permanent loss the hold was preventing.

`is_current_schema` compares for equality, so a version change is a refusal in **both** directions: merging one while a sidecar is in flight, or reverting one after a sidecar was written, refuses to collect that date's sidecar. Since #65 that refusal is a *hold*, not a hard fail — that date is skipped, its sidecar is left on disk, and every other date still publishes normally. **The landing condition is unchanged despite the softer failure mode**: a held sidecar's batch results still expire in ~29 days, so a version change landed on top of one still ends in permanent loss, just more quietly (no longer a broken `--collect-pending` run to notice it by — since #69 the loss is at least *recorded*, as an `abandoned_dates` error on the morning it becomes provable, but recording a loss is not preventing one). **Land a `SCHEMA_VERSION` change only when `git ls-files data/pending-batches/` is empty.**

When adding any new Claude-using script, ask yourself: **does this change what a speech is summarized to say?** If yes → must obey the invariants above. If no → reasonable freedom (still keep it deterministic where practical).

## Development Commands

```bash
# --- Frontend (repo root) ---
npm install
npm run dev      # localhost:3000
npm run build    # static export → out/
npm run lint

# --- MCP server (apps/mcp) ---
cd apps/mcp
npm install
npm run dev      # localhost:3100
npm run build    # prebuild script copies data/ from repo root

# --- Data pipeline (Python) ---
# Daily-batch.yml runs these in sequence each morning. To replay locally:
python scripts/fetch_ndl.py --lookback-days 30
python scripts/summarize.py --date YYYY-MM-DD --batch
python scripts/enrich-news.py --date YYYY-MM-DD --rank-with-claude
```

### `summarize.py` exit codes (a contract split across two files)

| code | meaning | what the daily workflow does |
|---|---|---|
| 0 | ran; may legitimately have produced nothing | continue |
| 1 | crash / usage error | abort the date loop (`set -e`) |
| 3 | **nothing reached the site for this date**, in any of three ways: (a) every meeting asked about produced nothing that became a thread; (b) summary requests went out and the date assembled nothing — (a) and (b) are not exclusive, a fully rejected batch reports both; (c) since #74, the date was **refused before anything was asked at all** — every raw file for it is unreadable, or its existing threads file is, so continuing would have published nothing or republished it short. | record the date, keep going, publish everything, fail the job in the last step |
| 4 | **suspect**: as (a)/(b) above, but only one meeting was asked about and the date already has threads | record separately; fail the job only if **2+ dates** in one run report it |

(c) is why the meaning is "nothing reached the site" and not "the API misbehaved". A refusal spends
no quota and touches no file, so the annotation says so in as many words — the cost of getting this
wrong is an operator hunting an API rejection that never happened, which is the same failure #59 was
about. Note the asymmetry with a *partly* unreadable date: that one publishes what it has and stays
**green**, carrying a warning naming what it went without. Data loss that repairs itself on the next
fetch is not worth a red morning; data that never reaches the site at all is.

`--collect-pending` is the exception: it speaks for many dates in one process, so a single exit
code cannot say which one failed. It reports through `systemic_dates` / `suspect_dates` /
`held_dates` / `abandoned_dates` step outputs plus annotations, and **exits 1 only on a crash**.
A sidecar that needs a human — an older schema, exhausted retries, a hash that no longer
verifies — is *held*: reported, red in the final step, and left on disk, without taking the
morning's publish down with it. That changed in #65: exit 1 used to be justified by "a sidecar
skips Summarize for every date anyway, so a green run would process nothing", and the per-date
gate removed that premise. A hold is bounded for a sidecar this code can still identify, on a
run using the default lookback — not unconditionally: since #69 one gate at the top
of `collect_pending_batches` writes off a sidecar — whatever regime it is held under —
whose current attempt is older than `ABANDON_AGE_DAYS` **and** none of whose manifest meetings
are in the raw on disk this run: the results have expired and there is nothing to rebuild from,
so it is provably uncollectable. (Raw for the *date* is not the test — a kantei file does not
rebuild an NDL batch — and everything `_reason_not_to_abandon` cannot establish means keep. A
sidecar carrying no `date` of its own is judged without one: its filename is a naming
convention, not evidence, so the question becomes whether its manifest's meetings are anywhere
in raw. Such a sidecar is then **held** rather than processed, since every path below the gate
indexes `sidecar["date"]`.) The corollary is the bound's exception, and it is deliberate: a
sidecar the gate cannot judge — unreadable raw or manifest, a date that contradicts its
filename, an uncomputable age — is held **indefinitely**, until a person fixes or removes it.
Fail-closed costs an unbounded red; the alternative costs data. It lands in `abandoned_dates` as a permanent loss. Two things keep that gate from
eating live data: the age is measured from the **latest** attempt, so a sidecar still being
resubmitted keeps resetting it; and raw's absence is **observed**, not inferred from the age —
`lookback_days` accepts up to 365 and widening it is exactly how a human rescues a held sidecar,
so inferring would delete the sidecar in the same job that restored what it needed. An over-age
sidecar whose raw came back is reported as held, not abandoned.

"Produced nothing that became a thread" covers both a rejected request *and* an answer that could not be
assembled — the outcome that matters is a speech that never reaches the site. It does **not** mean the date is
empty. Report it that way; an operator sent to hunt a 400 that never happened, or to look for threads that
were never lost, has had their morning taken.

**What counts as a meeting the API was asked about.** The pre-check both paths actually gate on is
`askable_request_kinds`, which answers with the *set* — `{"grouping"}`, `{"outcome"}`, both, or
neither — by delegating to the real request builders. (`has_question_for_the_api` is `bool()` of it
and survives as the readable predicate; edit the set-returning one.) A meeting counts as asked when
it sends at least one request — grouping *or* outcome. Since #60 that is
literal; before it, the pre-check looked at grouping only, as a workaround for
`extract_meeting_outcome` swallowing its own failures silently. The hole that closed: a date whose
only meeting is procedural-with-a-附帯決議 sends just an outcome request, so every request could be
rejected all morning and the run exited 0 with `attempted == 0`.
An outcome failure is still **not** raised — an outcome enriches a pattern-matched result, and a
meeting whose speeches summarised fine must not be failed over a 附帯決議 blurb no reader can see.
It is *counted* instead (`outcome_stats`), and folded into the per-meeting `api_stats` only when
`kinds == {"outcome"}`, i.e. when it is the whole of what that meeting asked. Fold it
unconditionally and a meeting that published threads reads as failed; two of those on one date read
as an outage. `api_stats` counts **meetings**, not requests — keep any new counter honest about that.
A meeting counted that way is also filed as **failed** rather than completed, on both the synchronous
and the batch path: filing it as completed would let the next `--resume` skip the one question it
ever asks, and the failure would drop out of the counters — the same invisibility this closed, one
layer further out.

**Why 4 exists.** A single meeting failing on an already-published date is ordinary breakage, and the 30-day
lookback re-visits published dates every morning — failing on one would fail most mornings, and an alarm that
cries daily gets switched off. But a *total* outage can present as nothing else: NDL adds one late meeting
each to three old dates, every request fails, and every date reports 1-of-1. So the evidence is kept (exit 4)
instead of discarded, and the **workflow** applies the threshold, because it is the only layer that sees every
date in the run. Change the threshold in `daily-batch.yml`'s `SUSPECT_N -ge 2`, not in Python.
That line now lives in the **last** step, not the Summarize step: the per-date gate means
Summarize now only skips the specific dates a sidecar already owns, but it still never sees the
dates Collect reported (only Collect owns those) — so a policy applied there would still be blind
to half its input.

3 exists only because 1 cannot carry this meaning: under the workflow's `set -e` loop a bare 1 is
indistinguishable from a crash, aborts the loop, and skips commit/push — the amplification #52 was about.
An outage must be loud without blocking the publish. **Both halves have to move together**: change
`EXIT_SYSTEMIC_FAILURE` / `EXIT_SUSPECT_FAILURE` in `scripts/summarize.py` and the matching `-eq` tests in
`.github/workflows/daily-batch.yml` in the same commit.
`test_systemic_failure.py::test_the_workflow_tolerates_exactly_these_exit_codes` parses the YAML and fails if
they drift.

## Project Structure

```
/                     Project root (frontend SSG project — `output: "export"`)
├── CLAUDE.md         This file
├── src/              Source code (Next.js App Router)
│   ├── app/          Pages and layouts (incl. /m member pages, /gov ministry hubs)
│   ├── components/   React components
│   ├── lib/          Utilities and data fetching (incl. ministry.mjs — see below)
│   └── types/        TypeScript type definitions
├── apps/
│   └── mcp/          MCP server (separate Vercel project, dynamic Node runtime)
├── scripts/          Python batch processing scripts
│   └── sources/      Source adapters (NDL, kantei, council, etc.)
├── data/             Generated JSON data (consumed by both frontend SSG and MCP server)
└── public/           Static assets
```

The repo hosts **two Vercel projects** pointing at the same GitHub repo:

- Root → frontend SSG (open-gikai.net). `next.config.ts` sets `output: "export"`.
- `apps/mcp` → dynamic MCP server. A `prebuild` script
  (`apps/mcp/scripts/copy-data.mjs`) copies `data/threads/` and
  `data/members.json` from the repo root into `apps/mcp/data/` so they ship
  with the serverless function bundle. The runtime reads from
  `process.cwd()/data` — see `apps/mcp/src/lib/data.ts`. We deliberately
  avoid `outputFileTracingRoot` pointing above the project root because
  Vercel double-prefixes such absolute paths during deploy.

Because the frontend uses static export, **server-side features (Route
Handlers, dynamic API routes, middleware) cannot be added under `src/app/`**.
Anything requiring a runtime belongs under `apps/`.

## Git Conventions

- **Conventional Commits**: All commit messages must follow the format:
  - `feat:` new feature
  - `fix:` bug fix
  - `ci:` CI/CD changes
  - `docs:` documentation
  - `refactor:` code restructuring
  - `test:` test additions/changes
  - `chore:` maintenance/deps
- **Issue titles**: English (body can be Japanese)
- **Branch strategy**: Direct to main for now; PRs for larger changes

## Coding Conventions

- Use TypeScript strict mode
- Components use functional style with hooks
- Code comments in English
- User-facing text in Japanese
- Use Tailwind CSS for styling (no CSS modules)
- Data types follow the schema defined in HANDOFF.md (see `Thread`, `Speech`, `Member` types)

## API Notes

- NDL API responses are in Japanese; field names are in camelCase English
- Diet records are public domain (Copyright Act Article 13)
- kantei.go.jp press conferences are scraped from the PM's official website
- Council adapter uses PyMuPDF for PDF text extraction and BeautifulSoup for HTML scraping
- Rate limiting: be respectful of NDL API, kantei.go.jp, and cao.go.jp usage

## 品質ゲート（Gate3 — 実装完了時の標準フロー）

- 非自明な変更（複数ファイル・設計判断・データ取得アダプタ / ビルドパイプラインに触れる変更）の
  完了時は `/code-gate` を実行し **critical 0 まで**ループする。標準起動は headless `/goal` 経由:
  ```bash
  timeout 3600 claude -p "/goal /code-gate を実行し critical 0 を達成する。5回で打ち切り" \
    --permission-mode acceptEdits
  ```
- 受け入れ基準（機械判定）: `npm run lint && npm run validate`。
  `scripts/` の Python を触ったら `python -m pytest scripts/tests`（resume/batch_state の
  ユニットテストがある — フロントには無い）。表示に影響する変更は `npm run test:e2e` を追加実行。
- 単発の小修正（数十行以下）は Gate 省略可。ただしスクレイパーのレート制御・公開データの
  生成ロジックに触れる diff はサイズによらず Gate 対象。
