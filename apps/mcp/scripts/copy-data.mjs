#!/usr/bin/env node
/**
 * Copy the subset of repo-root data/ that the MCP server actually needs
 * into apps/mcp/data/ so it gets bundled into the Vercel serverless function.
 *
 * Why this exists:
 *   Vercel resolves outputFileTracingRoot incorrectly when pointing above
 *   the project root (it double-prefixes the project base, producing paths
 *   like /vercel/path0/vercel/path0/.next/...). A simple selective copy at
 *   build time sidesteps the issue entirely and also drops ~340MB of
 *   intermediate pipeline data (data/raw/, data/batch/) that the MCP
 *   server never reads.
 *
 * Run via the prebuild npm script. Idempotent, and never destructive before the
 * replacement exists: the copy is staged into a sibling directory and swapped in
 * (#73). It used to delete the destination first and then fill it, so an
 * interrupted run — SIGKILL, a build timeout, a full disk — left `threads/` half
 * copied or `members.json` truncated. Stale files still cannot survive a build:
 * the swap replaces the whole directory rather than merging into it.
 *
 * The bundle also records a manifest of what it contains, because the "already
 * bundled, skip the copy" path below is otherwise unable to tell a complete
 * bundle from a wrecked one — and being unable to tell used to mean accepting it.
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const PROJECT_ROOT = path.resolve(__dirname, "..");
const REPO_ROOT = path.resolve(PROJECT_ROOT, "..", "..");
const SOURCE = path.join(REPO_ROOT, "data");
const DEST = path.join(PROJECT_ROOT, "data");

// Files / directories that the MCP server reads at request time, and what shape
// each one has to be. Keep this list narrow: every entry here ships in the
// function bundle. The kind is declared rather than observed because it is part
// of what a bundle must be verified AGAINST — `threads` arriving as a regular
// file satisfies every check that reads the bundle's own description of itself,
// and then reads as zero threads at request time.
const ENTRY_KIND = { threads: "dir", "members.json": "file" };
const INCLUDE = Object.keys(ENTRY_KIND);

function bytes(n) {
  if (n < 1024) return `${n}B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)}KB`;
  return `${(n / 1024 / 1024).toFixed(1)}MB`;
}

function totalSize(p) {
  const stat = fs.statSync(p);
  if (stat.isFile()) return stat.size;
  let total = 0;
  for (const entry of fs.readdirSync(p)) {
    total += totalSize(path.join(p, entry));
  }
  return total;
}

// The bundle's own record of what it holds. Lives INSIDE the destination so it
// travels with it: a manifest kept anywhere else describes a bundle it cannot be
// sure it is looking at.
const MANIFEST_NAME = ".bundle-manifest.json";
// Bump only when a manifest written by the old code can no longer be checked by
// the new code — a changed key shape, a new required field. Bumping refuses every
// bundle already sitting in a CLI-deploy checkout, so the next deploy must run
// where the repo-root data/ is present. Adding a check that reads existing fields
// (as INCLUDE coverage and parseability do) is not a bump.
const MANIFEST_VERSION = 1;

// Manifest keys are always POSIX-separated, and resolved back through split("/").
// A bundle built on native Windows would otherwise record `threads\a.json` and
// fail verification on Vercel's Linux builder with every file reported missing.
function filesUnder(root, prefix = "") {
  const out = [];
  for (const entry of fs.readdirSync(root)) {
    const abs = path.join(root, entry);
    const rel = prefix ? `${prefix}/${entry}` : entry;
    // lstat, at every depth. A symlink anywhere in the bundle points at
    // something the upload does not carry, and following it makes the size and
    // parse below describe a file that is not in the bundle at all.
    if (fs.lstatSync(abs).isSymbolicLink()) {
      out.push({ rel, symlink: true });
    } else if (fs.statSync(abs).isDirectory()) {
      out.push(...filesUnder(abs, rel));
    } else if (rel !== MANIFEST_NAME) {
      out.push({ rel, symlink: false });
    }
  }
  return out;
}

const relsUnder = (root) => filesUnder(root).map((f) => f.rel);
const resolveRel = (root, rel) => path.join(root, ...rel.split("/"));

/** Is `rel` the INCLUDE entry `entry`, or a file inside it? */
const covers = (rel, entry) => rel === entry || rel.startsWith(`${entry}/`);

// The runtime's own selection, kept identical on purpose: `loadThreads` in
// apps/mcp/src/lib/data.ts reads exactly these under threads/. An unrecorded
// file only matters because the runtime will read it, so the two predicates
// have to agree — otherwise this rejects a stray `.DS_Store` that nothing would
// ever have opened.
const runtimeReads = (rel) =>
  rel.endsWith(".json") && !rel.endsWith(".progress.json");

function buildManifest(root) {
  const files = {};
  for (const rel of relsUnder(root).sort()) {
    files[rel] = fs.statSync(resolveRel(root, rel)).size;
  }
  return {
    version: MANIFEST_VERSION,
    // Sizes per file, not a count or a total: truncation is the failure this
    // exists to catch, and neither of those can see it. Not hashes — sizes catch
    // a copy cut short just as well, and the corruption hashes would add on top
    // (content that changed under us between two builds) has no source here.
    // What sizes cannot see at all is a file that was ALREADY bad when recorded;
    // that is why verifyBundle parses as well as compares.
    files,
    fileCount: Object.keys(files).length,
    totalBytes: Object.values(files).reduce((a, b) => a + b, 0),
  };
}

/** Problems with an already-present bundle, as human-readable lines. */
function verifyBundle(root) {
  const manifestPath = path.join(root, MANIFEST_NAME);
  if (!fs.existsSync(manifestPath)) {
    return [`no ${MANIFEST_NAME}: this bundle cannot be verified, and an ` +
            `unverifiable bundle is not a complete one (#73)`];
  }
  let manifest;
  try {
    manifest = JSON.parse(fs.readFileSync(manifestPath, "utf-8"));
  } catch (e) {
    return [`${MANIFEST_NAME} is unreadable (${e.message})`];
  }
  if (manifest.version !== MANIFEST_VERSION) {
    return [`${MANIFEST_NAME} has version ${manifest.version}, expected ` +
            `${MANIFEST_VERSION} — rebuild the bundle`];
  }
  const recorded = manifest.files;
  if (recorded === null || typeof recorded !== "object" ||
      Array.isArray(recorded) || Object.keys(recorded).length === 0) {
    return [`${MANIFEST_NAME} records no files — it cannot vouch for anything`];
  }

  const problems = [];

  // A key is a bundle-relative POSIX path and nothing else. This manifest is our
  // own artifact, so a `..` in it means the file was tampered with or mangled —
  // either way resolving it would take the checks below outside the bundle, and
  // report on a file the bundle does not contain.
  for (const rel of Object.keys(recorded)) {
    if (rel.includes("\\") || path.isAbsolute(rel) ||
        rel.split("/").some((seg) => seg === ".." || seg === "" || seg === ".")) {
      problems.push(`manifest key is not a bundle-relative path: ${rel}`);
    }
  }
  if (problems.length) return problems;

  // Against INCLUDE, not against the manifest's own contents. The manifest is
  // generated FROM the copy, so it agrees with itself about a bundle that never
  // held `threads/` at all — the case `INCLUDE.every(existsSync)` used to catch,
  // and the one a self-describing record is structurally blind to. The required
  // set has to come from outside the thing being verified.
  for (const entry of INCLUDE) {
    const wantDir = ENTRY_KIND[entry] === "dir";
    const covered = wantDir
      ? Object.keys(recorded).some((rel) => rel.startsWith(`${entry}/`))
      : entry in recorded;
    if (!covered) {
      problems.push(`required entry absent: ${entry} (nothing ` +
                    `${wantDir ? "under" : "at"} it was bundled)`);
      continue;
    }
    // Coverage above already rejects an entry of the wrong shape — `threads` as
    // a regular file has nothing recorded under `threads/`, and `members.json`
    // as a directory fails on size.
  }

  // What none of that sees is data that is not IN the bundle: a symlink
  // resolves, and every size and parse below is right, on the machine that
  // built it. Vercel uploads the link. At every depth, not just the entry
  // itself — `threads/2026-05-14.json` can be a link just as easily.
  for (const f of filesUnder(root)) {
    if (f.symlink) {
      problems.push(`symlink in bundle: ${f.rel} ` +
                    `(a bundle must contain its data, not point at it)`);
    }
  }

  for (const [rel, size] of Object.entries(recorded)) {
    const abs = resolveRel(root, rel);
    if (!fs.existsSync(abs)) {
      problems.push(`missing: ${rel}`);
      continue;
    }
    const actual = fs.statSync(abs).size;
    if (actual !== size) {
      problems.push(`size mismatch: ${rel} (${actual}B on disk, ${size}B when bundled)`);
      continue;
    }
    // Sizes catch a copy that was cut short; parsing catches a file that was
    // already malformed when it was bundled, which sizes never can — the record
    // is made from the copy, so a short source file is recorded as short and
    // verifies clean forever after. #73 asked for parseability by name. It is
    // affordable here for the same reason hashing is not interesting: ~60MB of
    // JSON is well under a second, and it is the only check that survives the
    // manifest being generated from the very files it describes.
    if (rel.endsWith(".json")) {
      let parsed;
      try {
        parsed = JSON.parse(fs.readFileSync(abs, "utf-8"));
      } catch (e) {
        problems.push(`not valid JSON: ${rel} (${e.message})`);
        continue;
      }
      // Shape, not just syntax, and for the same reason `_as_list_of_dicts`
      // exists on the Python side: the runtime does not throw on a thread file
      // that parses to `{}` — `loadThreads` skips a non-array and `loadMembers`
      // takes whatever it got. That is a green deploy serving zero threads,
      // which is the failure this file is named after, arriving quietly.
      const wantArray = covers(rel, "threads");
      const isArray = Array.isArray(parsed);
      if (wantArray !== isArray || parsed === null || typeof parsed !== "object") {
        problems.push(`wrong shape: ${rel} is ${isArray ? "an array" : typeof parsed}, ` +
                      `expected ${wantArray ? "an array of threads" : "an object"}`);
      }
    }
  }
  // An extra file is not merely wasted bundle size — but only when the runtime
  // would actually open it. One the manifest never described is then either
  // content nothing vouched for, or, being unparsed here, fatal at request
  // time: the exact outcome this verification exists to prevent. Everything
  // else — a `.DS_Store`, an editor temp file, a leftover `.progress.json`,
  // anything outside an INCLUDE entry — is weight, and failing over one would
  // turn a harmless leftover into a broken deploy. The predicate is the
  // runtime's own (`runtimeReads`), because "the runtime will read it" is the
  // entire reason this is an error rather than a warning.
  const extra = relsUnder(root).filter((rel) => !(rel in recorded));
  const willBeRead = extra.filter(
    (rel) => runtimeReads(rel) && INCLUDE.some((entry) => covers(rel, entry)));
  for (const rel of willBeRead) {
    problems.push(`unrecorded file inside ${INCLUDE.find((e) => covers(rel, e))}: ` +
                  `${rel} (the runtime will read it, and nothing verified it)`);
  }
  const harmless = extra.length - willBeRead.length;
  if (harmless > 0) {
    console.warn(`copy-data: bundle holds ${harmless} file(s) not in the manifest ` +
                 `that the runtime never opens (weight, not correctness)`);
  }
  return problems;
}

// Stage, then swap. Both live beside DEST so every rename stays on one
// filesystem — a staging directory under the system temp dir would make the
// swap a copy, which is exactly what must not be interruptible here.
const STAGING = path.join(PROJECT_ROOT, `.data-staging-${process.pid}`);
const RETIRED = path.join(PROJECT_ROOT, `.data-retired-${process.pid}`);

const isAlive = (pid) => {
  try {
    process.kill(pid, 0);
    return true;
  } catch (e) {
    return e.code === "EPERM";           // exists, we just may not signal it
  }
};

// Collect what an interrupted run left behind — BEFORE the source check below,
// because the source-less path exits early and would otherwise never sweep, and
// because the one leftover worth rescuing is only reachable on that same path.
//
// A run killed between the two renames runs none of its own cleanup, and nothing
// else ever collects what it left: a full 61MB copy sitting in the project,
// uploaded with it and charged against the function's size budget. (`.gitignore`
// does not prevent that — the CLI deploy uploads `apps/mcp/data/`, which is
// ignored too, and the "already bundled" path below exists precisely because it
// does. Ignoring only keeps them out of commits.)
//
// Only dead pids: another prebuild running concurrently in the same working
// directory owns its staging area, and one of the moments we could delete it is
// the moment after it moved the good bundle aside. This assumes the other run
// shares our pid namespace, which is true of the cases that motivate it (two
// npm scripts on one machine) and not of a shared bind mount written from two
// containers. Getting it wrong in that setup deletes a live staging area, so if
// this project ever builds that way, the ownership marker has to stop being a
// pid.
const orphans = [];
for (const name of fs.readdirSync(PROJECT_ROOT)) {
  const m = /^\.data-(staging|retired)-(\d+)$/.exec(name);
  if (!m) continue;
  const pid = Number(m[2]);
  if (pid !== process.pid && isAlive(pid)) {
    console.warn(`copy-data: leaving ${name}: pid ${pid} is still running`);
    continue;
  }
  orphans.push({ kind: m[1], abs: path.join(PROJECT_ROOT, name) });
}

// A kill in the one-rename-wide window between "old moved aside" and "new moved
// in" leaves the previous bundle whole, but not where anything looks for it.
// Put it back before deciding anything else — otherwise the source-less path
// below reports a missing bundle while a verified one sits next to it.
if (!fs.existsSync(DEST)) {
  const rescuable = orphans.filter(
    (o) => o.kind === "retired" && verifyBundle(o.abs).length === 0);
  if (rescuable.length > 1) {
    // Two complete bundles, no way to tell which is current: a manifest records
    // what a bundle holds, not when. Picking by readdir order would ship stale
    // data under a green build, which is worse than stopping — and stopping is
    // recoverable by hand in a way that a quietly old bundle is not.
    console.error(
      `copy-data: ${DEST} is missing and ${rescuable.length} retired bundles verify:\n` +
      rescuable.map((o) => `    - ${path.basename(o.abs)}`).join("\n") + `\n` +
      `  Nothing here records which is current. Move the one you want to ${DEST},\n` +
      `  or delete both and re-run where the repo-root data/ is present.`
    );
    process.exit(1);
  }
  if (rescuable.length === 1) {
    fs.renameSync(rescuable[0].abs, DEST);
    orphans.splice(orphans.indexOf(rescuable[0]), 1);
    console.warn(`copy-data: restored ${DEST} from ${path.basename(rescuable[0].abs)} ` +
                 `(a previous run was killed mid-swap)`);
  }
}
// Staging areas are never rescued, however complete they look: a staged copy
// was by definition not installed, so nothing ever decided it was good.
for (const o of orphans) fs.rmSync(o.abs, { recursive: true, force: true });

// On Vercel the project is uploaded with apps/mcp as the root, so the
// repo-level data/ directory two levels up does not exist on the build
// machine — but the local prebuild already ran on the developer's machine
// and the resulting apps/mcp/data/ was included in the upload. So if the
// source is missing AND the destination verifies against its own manifest,
// treat it as "Vercel rebuild, data already bundled" and exit successfully.
//
// Existence used to be the whole test, which a half-copied directory passes —
// and with no source to copy from, this is the only thing between a wrecked
// bundle and production. The MCP server's reader is deliberately fatal on a
// corrupt file (#57), so accepting one trades a loud build failure for failing
// requests.
if (!fs.existsSync(SOURCE)) {
  const problems = fs.existsSync(DEST) ? verifyBundle(DEST)
                                       : [`${DEST} does not exist`];
  if (problems.length === 0) {
    console.log(
      `copy-data: source ${SOURCE} not present (expected on Vercel); ` +
      `data/ already bundled and verified against ${MANIFEST_NAME}, skipping copy.`
    );
    process.exit(0);
  }
  console.error(
    `copy-data: source missing: ${SOURCE}\n` +
    `  and apps/mcp/data/ does not verify, so it cannot be reused:\n` +
    problems.map((p) => `    - ${p}`).join("\n") + `\n` +
    `  Re-run the prebuild where the repo-root data/ is present.`
  );
  process.exit(1);
}

fs.mkdirSync(STAGING, { recursive: true });

let copied = 0;
let installed = false;
try {
  for (const entry of INCLUDE) {
    const src = path.join(SOURCE, entry);
    const dst = path.join(STAGING, entry);
    if (!fs.existsSync(src)) {
      console.warn(`copy-data: skipping missing ${entry}`);
      continue;
    }
    fs.cpSync(src, dst, { recursive: true });
    const size = totalSize(dst);
    copied += size;
    console.log(`copy-data: + ${entry} (${bytes(size)})`);
  }

  fs.writeFileSync(
    path.join(STAGING, MANIFEST_NAME),
    JSON.stringify(buildManifest(STAGING), null, 2),
    "utf-8"
  );

  // Verify the staged copy before it becomes the bundle — #73 asked for
  // "stage → verify → swap", and without this the only thing standing between a
  // bad copy and the swap is `cpSync` happening to throw. The size half is
  // circular here (the record was just made from these files) and that is fine:
  // the two checks that are not are INCLUDE coverage and parseability, i.e. an
  // entry that never arrived, and a source file that was already malformed.
  const staged = verifyBundle(STAGING);
  if (staged.length) {
    throw new Error(
      `staged copy does not verify, refusing to replace ${DEST}:\n` +
      staged.map((p) => `    - ${p}`).join("\n")
    );
  }

  // Rename the old one aside first. Doing it the other way — remove DEST, then
  // rename STAGING in — reintroduces a window where DEST does not exist, which
  // is a smaller version of the bug this replaces.
  if (fs.existsSync(DEST)) {
    fs.renameSync(DEST, RETIRED);
  }
  try {
    fs.renameSync(STAGING, DEST);
    installed = true;
  } catch (e) {
    if (fs.existsSync(RETIRED) && !fs.existsSync(DEST)) {
      try {
        fs.renameSync(RETIRED, DEST);    // put it back rather than leave nothing
      } catch (rollbackFailed) {
        // Both renames failed. The old bundle is still whole, just not where
        // anything looks — say where, loudly, and let the `finally` below leave
        // it alone. Deleting it here (which an unconditional cleanup does) is
        // the destruction this whole change exists to make impossible.
        throw new Error(
          `${e.message}\n  and rolling back failed too (${rollbackFailed.message}).\n` +
          `  The previous bundle is INTACT at ${RETIRED} — move it to ${DEST} by hand,\n` +
          `  or re-run this prebuild, which restores it automatically.`
        );
      }
    }
    throw e;
  }
} catch (e) {
  // Tidying up must never replace the reason we are here — that message is what
  // tells the operator their previous bundle is intact and where it is.
  try {
    fs.rmSync(STAGING, { recursive: true, force: true });
  } catch (cleanupFailed) {
    e.message += `\n  (also failed to remove ${STAGING}: ${cleanupFailed.message})`;
  }
  throw e;
} finally {
  // Only once the replacement is actually in place. Before that, RETIRED may be
  // the only complete copy left.
  if (installed) fs.rmSync(RETIRED, { recursive: true, force: true });
}

console.log(`copy-data: done, ${bytes(copied)} total → ${DEST}`);
