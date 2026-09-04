/**
 * The JS half of scripts/pipeline/jsonio.py's rule (#57/#72): a writer of a
 * file this repo commits must never leave a half-written one behind.
 * daily-batch.yml runs validate-data.mjs --fix and enrich-members.mjs
 * immediately before `git add data/members.json`, so a job killed mid-write
 * commits a truncated members.json — and src/lib/data.ts is deliberately fatal
 * on that, which is a red Vercel build.
 *
 * Same shape as the Python side, and that has to mean the same steps, not just
 * the same outline: temp file in the SAME directory (rename is only atomic
 * within one filesystem), fsync the file, rename, then fsync the directory so
 * the rename is durable and not merely the bytes it points at. The directory
 * fsync is best-effort — some filesystems refuse to open a directory, and
 * failing the whole write over a durability upgrade would be worse than the
 * crash window it closes.
 *
 * This module exists because two JS writers need it and node cannot import the
 * Python one. It takes TEXT, not an object — the Python `write_json_atomic`
 * takes an object and serialises it. Do not unify the signatures; the callers
 * control their own `JSON.stringify` spacing and a change there rewrites every
 * committed data file on the next run.
 */
import {
  writeFileSync, renameSync, unlinkSync, openSync, fsyncSync, closeSync,
} from "fs";
import { join, dirname, basename } from "path";

function fsyncDir(dir) {
  let fd;
  try {
    fd = openSync(dir, "r");
    fsyncSync(fd);
  } catch { /* best-effort */ } finally {
    if (fd !== undefined) { try { closeSync(fd); } catch { /* ignore */ } }
  }
}

export function writeJsonAtomic(path, text) {
  const dir = dirname(path);
  const tmp = join(dir, `.${basename(path)}.${process.pid}.tmp`);
  try {
    const fd = openSync(tmp, "w");
    try {
      // writeFileSync on the descriptor, not writeSync: writeSync returns a
      // byte count and can short-write, which would silently truncate exactly
      // the way this function exists to prevent. writeFileSync loops until the
      // whole buffer is out.
      writeFileSync(fd, text, "utf-8");
      fsyncSync(fd);
    } finally {
      closeSync(fd);
    }
    renameSync(tmp, path);
    fsyncDir(dir);
  } catch (e) {
    try { unlinkSync(tmp); } catch { /* already gone */ }
    throw e;
  }
}
