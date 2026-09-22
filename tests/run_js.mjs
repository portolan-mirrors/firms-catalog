#!/usr/bin/env node
/**
 * Run every JavaScript gate. Exit non-zero if any fails.
 *
 * The explorer's modules are plain ESM with no build step and no test
 * framework, so each suite is a file node can run on its own. This runs them
 * the same way, one process apiece, and reports which ones failed.
 *
 * The list is explicit rather than globbed, for the same reason
 * tests/run_all.py keeps one: the gates are visible in one place and adding
 * one is a deliberate edit.
 *
 *     node tests/run_js.mjs
 */
import {spawnSync} from "node:child_process";
import {existsSync} from "node:fs";
import {dirname, resolve} from "node:path";
import {fileURLToPath} from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");

const TESTS = [
  "apps/firms-explorer/archives.test.mjs",
  "apps/firms-explorer/deck-cells.test.mjs",
  "apps/firms-explorer/mounted.test.mjs",
  "apps/firms-explorer/timeline.test.mjs",
];

const failed = [];
for (const name of TESTS) {
  const path = resolve(ROOT, name);
  if (!existsSync(path)) continue;
  console.log(`\n=== ${name} ` + "=".repeat(Math.max(1, 60 - name.length)));
  const run = spawnSync(process.execPath, [path], {stdio: "inherit"});
  if (run.status !== 0) failed.push(name);
}

console.log();
if (failed.length) {
  console.log("FAILED: " + failed.join(", "));
  process.exit(1);
}
console.log("all JavaScript gates passed");
