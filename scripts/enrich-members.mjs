#!/usr/bin/env node
/**
 * Derive data/members.json's `links` deterministically.
 *
 * The discriminator is two facts and no more: whether the MAP KEY starts with
 * `m_`, and whether getMemberMinistry() resolves a ministry from the role. A
 * richer classifier was tried and rejected in Gate1 — reading `rank`/`party`
 * as evidence of elected office puts /wiki/記者, /wiki/事務局 and
 * /wiki/内閣総理大臣 on 34 members, and those resolve 200 to articles about the
 * occupation, the org-chart term and the office. src/lib/ministry.mjs says in
 * as many words not to use `rank` (気象庁長官 is ranked minister).
 *
 * The map key, not member.id, is the identifier of record: validate-data.mjs
 * --fix adds entries whose value carries no `id` at all (31 of them today).
 */
import { readFileSync, readdirSync, existsSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath, pathToFileURL } from "url";
import { getMemberMinistry } from "../src/lib/ministry.mjs";
import { writeJsonAtomic } from "./lib/jsonio.mjs";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));

function google(q) {
  return "https://www.google.com/search?" + new URLSearchParams({ q }).toString();
}

function text(value) {
  return typeof value === "string" ? value.trim() : "";
}

/**
 * The ministries whose /gov/{slug} page this build actually produces.
 *
 * /gov/[slug] sets dynamicParams = false and getMinistryRosters() keeps only
 * ministries with a member who has spoken, so resolving a ministry is NOT
 * enough to link to it — the page for a ministry whose only witness stopped
 * appearing is never built, and the link is a hard 404. data.ts is TypeScript
 * and this script cannot import it, so the rule is recomputed from the same
 * two sources it uses (members + threads), never from a copied slug list.
 */
export function computeLiveSlugs(members, threadsDir) {
  const spoken = new Set();
  if (existsSync(threadsDir)) {
    for (const name of readdirSync(threadsDir)) {
      // Same predicate as loadThreads: .json and not .progress.json.
      if (!name.endsWith(".json") || name.endsWith(".progress.json")) continue;
      let threads;
      try {
        threads = JSON.parse(readFileSync(join(threadsDir, name), "utf-8"));
      } catch {
        // threads/ is not this script's to own, and it is re-fetched on the
        // next run. Refusing here would take the morning's publish down for
        // data that repairs itself.
        console.warn(`enrich-members: skipping unreadable ${name}`);
        continue;
      }
      if (!Array.isArray(threads)) continue;
      for (const thread of threads) {
        // thread?.speeches can be truthy but not iterable (an object, a
        // number) — a shape neither the top-level Array.isArray(threads)
        // check nor a plain `|| []` fallback catches, since both only guard
        // absence, not the wrong type. threads/ is not this script's to own
        // (see the try/catch above); skip the malformed thread rather than
        // let a TypeError from a bad for-of take the run down.
        if (!Array.isArray(thread?.speeches)) continue;
        for (const speech of thread.speeches) {
          if (speech?.memberId) spoken.add(speech.memberId);
        }
      }
    }
  }
  const live = new Set();
  for (const [memberId, member] of Object.entries(members)) {
    // Speech lookup is by map key here; data.ts:352 uses member.id. They agree
    // on the committed data (0 mismatches, measured) — every entry either has
    // id === key or has no id at all, and the latter never resolves a ministry
    // anyway because its role is empty. Left asymmetric rather than "fixed" so
    // this reads the same key it iterates; the divergence is caught by
    // scripts/tests/test_member_links.py::test_the_generator_and_the_site_agree_on_which_gov_pages_exist.
    if (!spoken.has(memberId)) continue;
    // getMemberMinistry(member), NOT a key-normalised copy. data.ts resolves
    // from the stored object (Object.values + member.id), so normalising here
    // would let this script link to a ministry whose page data.ts never builds
    // — a hard 404, because /gov/[slug] is dynamicParams=false. Gate2 measured
    // that normalising resolves zero extra members anyway: all 31 id-less
    // entries also have an empty role, and getMemberMinistry returns null on
    // that before it ever looks at the id. The normalisation bought nothing
    // and cost a latent 404, so the two sides resolve identically instead.
    const ministry = getMemberMinistry(member);
    if (ministry) live.add(ministry.slug);
  }
  return live;
}

export function buildMemberLinks(memberId, member, { ministry, liveSlugs }) {
  const name = text(member?.name);
  const role = text(member?.role);
  // A stub whose name is missing still gets a link rather than an empty array:
  // zero links is the state this whole change exists to end.
  const term = name || memberId;

  if (!memberId.startsWith("m_")) {
    return [
      { label: "Wikipedia", url: "https://ja.wikipedia.org/wiki/" + encodeURIComponent(term) },
      { label: "公式サイト検索", url: google(`${term} 公式サイト`) },
      { label: "X (Twitter) 検索", url: google(`${term} site:x.com OR site:twitter.com`) },
    ];
  }
  if (ministry && liveSlugs.has(ministry.slug)) {
    return [
      { label: `${ministry.name}の発言者一覧`, url: `/gov/${ministry.slug}` },
      { label: "所属・経歴を検索", url: google(`${term} ${ministry.name}`) },
    ];
  }
  return [{ label: "所属・経歴を検索", url: google(role ? `${term} ${role}` : term) }];
}

export function enrichMembers(members, { liveSlugs }) {
  const next = {};
  for (const [memberId, member] of Object.entries(members)) {
    // A malformed row is copied through untouched rather than failing the run
    // — see the note in main(). It keeps whatever links it had, including none.
    if (member === null || typeof member !== "object" || Array.isArray(member)) {
      next[memberId] = member;
      continue;
    }
    // Same resolution as computeLiveSlugs and as src/lib/data.ts. The map key
    // still decides `m_`-ness below; only the ministry lookup follows data.ts.
    const ministry = getMemberMinistry(member);
    // Field order is preserved and only `links` is replaced: everything else
    // in this file is owned by other writers.
    next[memberId] = { ...member, links: buildMemberLinks(memberId, member, { ministry, liveSlugs }) };
  }
  return next;
}

function parseArgs(argv) {
  let membersPath = null;
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--members-path") {
      if (!argv[i + 1]) throw new Error("--members-path needs a value");
      membersPath = argv[i + 1];
      i++;
      continue;
    }
    if (arg.startsWith("--members-path=")) {
      const value = arg.slice("--members-path=".length);
      if (!value) throw new Error("--members-path needs a value");
      membersPath = value;
      continue;
    }
    // Reject anything else that looks like a flag rather than silently
    // falling through to the default (repo-committed) path — an
    // unrecognised `--members-path=<path>` used to do exactly that and
    // would have targeted data/members.json instead of the caller's fixture.
    if (arg.startsWith("--")) {
      throw new Error(`enrich-members: unknown argument ${arg}`);
    }
  }
  if (membersPath !== null) return membersPath;
  // Anchored to this file, not the CWD: ci.yml runs pytest from scripts/.
  return join(SCRIPT_DIR, "..", "data", "members.json");
}

function main() {
  let membersPath;
  try {
    membersPath = parseArgs(process.argv.slice(2));
  } catch (e) {
    console.error(e.message);
    console.error("usage: enrich-members.mjs [--members-path <path>|--members-path=<path>]");
    process.exit(1);
  }
  if (!existsSync(membersPath)) {
    console.error(`enrich-members: ${membersPath} does not exist`);
    process.exit(1);
  }
  const raw = readFileSync(membersPath, "utf-8");
  let members;
  try {
    members = JSON.parse(raw);
  } catch (e) {
    console.error(`enrich-members: ${membersPath} is not readable JSON: ${e.message}`);
    process.exit(1);
  }
  if (members === null || typeof members !== "object" || Array.isArray(members)) {
    console.error(`enrich-members: ${membersPath} is not a JSON object`);
    process.exit(1);
  }
  // A single member of the wrong shape is NOT fatal. This step sits under
  // `bash -e` with `git add data/members.json` a few steps later, so exiting
  // here throws away the morning's threads — and validate-data.mjs (:84-90)
  // and the metrics step (:262-268) both exist because that already happened
  // twice (#52, #74). The file as a whole being unreadable IS fatal, above:
  // that is not one bad row, it is nothing to work from. One bad row is left
  // exactly as it is, with its links untouched, and named in the log.
  const skipped = [];
  for (const [key, value] of Object.entries(members)) {
    if (value === null || typeof value !== "object" || Array.isArray(value)) {
      skipped.push(key);
    }
  }
  if (skipped.length) {
    console.warn(`enrich-members: leaving ${skipped.length} malformed ` +
                 `member(s) untouched: ${skipped.slice(0, 5).join(", ")}`);
  }

  const liveSlugs = computeLiveSlugs(members, join(dirname(membersPath), "threads"));
  const next = enrichMembers(members, { liveSlugs });

  // Compare the structures, not the raw text: a file that differs only in
  // trailing whitespace has not had its links change.
  if (JSON.stringify(next) === JSON.stringify(members)) {
    console.log(`Member links unchanged (${Object.keys(members).length} members)`);
    return;
  }
  writeJsonAtomic(membersPath, JSON.stringify(next, null, 2) + "\n");
  console.log(`Member links written for ${Object.keys(next).length} members ` +
              `(${liveSlugs.size} ministries with a built /gov page)`);
}

// Only when run as a program. The tests import computeLiveSlugs to check that
// this script and the site agree on which /gov pages exist, and an unguarded
// main() would run on that import — against the DEFAULT path, i.e. the repo's
// committed data/members.json, writing it from inside a test.
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main();
}
