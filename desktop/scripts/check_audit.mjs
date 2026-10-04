// Known-vulnerable dependencies in the shell, with the same threshold the client is held to (`npm
// audit --audit-level=high`).
//
// ## Why it is not the one-liner the client uses
//
// A bare `npm audit --audit-level=high` here cannot be made green by anything in this repository:
// an advisory can sit in a devDependency whose newest release still asks for the vulnerable line,
// and `npm audit fix` changes nothing. So this gate passes exactly the advisories written down
// below, each with the reason and what would retire it, and fails on anything else at or above the
// threshold. A new high in the shell's dependencies fails the run the day it lands.
//
// An entry here is not a dismissal: it records that the fix is a major version bump of a build
// tool.

import { spawnSync } from "node:child_process";

/** The threshold, the same word `job_web` passes to npm. */
const FAILS_AT = ["high", "critical"];

/**
 * Advisories this gate knows about, by the id npm reports.
 *
 * `until` is what makes the entry go away, written so nobody has to work it out again.
 */
const ACCOUNTED_FOR = {
  // An entry lives here only while its advisory is in the tree.
  "GHSA-ch52-4w7c-c8xp": {
    reason:
      "http-cache-semantics max-stale handling (a cross-user disclosure from a SHARED response " +
      "cache) reached only through got under @electron/get and electron-builder: build-time " +
      "tools that fetch the Electron binary on the build box and never ship in the installer. " +
      "No patched release exists yet (npm reports every version), and the fix npm " +
      "offers is a downgrade of electron-builder, which is no fix.",
    until:
      "http-cache-semantics publishes a version outside the advisory and the lockfile takes it " +
      "(npm audit then stops listing it), or electron-builder moves off got.",
  },
};

// One string with `shell: true`, because `npm` on Windows is `npm.cmd` and Node refuses to spawn a
// `.cmd` without a shell (CVE-2024-27980). An args array would answer EINVAL through `.error`, and
// the empty stdout that follows reads exactly like an audit that found nothing.
const run = spawnSync("npm audit --json", {
  shell: true,
  encoding: "utf8",
  cwd: process.cwd(),
});
if (!run.stdout) {
  console.error(
    `npm audit produced no output: ${run.error ?? run.stderr ?? "no reason given"}`,
  );
  process.exit(1);
}

const report = JSON.parse(run.stdout);
const found = Object.values(report.vulnerabilities ?? {});
const serious = found.filter((one) => FAILS_AT.includes(one.severity));

/**
 * The advisory ids behind one row. `via` holds either the advisory objects themselves or the names
 * of packages that carry them, so a transitive row points at the direct one rather than repeating
 * its id; the names are followed down to the advisories, each row read once.
 */
function idsBehind(row, seen = new Set()) {
  if (seen.has(row.name)) return [];
  seen.add(row.name);
  const ids = [];
  for (const via of row.via ?? []) {
    if (typeof via === "object" && via.url)
      ids.push(String(via.url).split("/").pop());
    else if (typeof via === "string" && report.vulnerabilities?.[via]) {
      ids.push(...idsBehind(report.vulnerabilities[via], seen));
    }
  }
  return [...new Set(ids)];
}

const unaccounted = [];
for (const one of serious) {
  const ids = idsBehind(one);
  const news = ids.filter((id) => !(id in ACCOUNTED_FOR));
  if (ids.length === 0 || news.length > 0) {
    unaccounted.push(
      `${one.name} (${one.severity}): ${news.join(", ") || "no advisory id"}`,
    );
  }
}

if (unaccounted.length > 0) {
  console.error("desktop: advisories this gate has never been told about:");
  for (const line of unaccounted) console.error(`  ${line}`);
  console.error(
    "\nFix it, or add the id to ACCOUNTED_FOR in desktop/scripts/check_audit.mjs with the\n" +
      'reason and what would retire it. An entry with no "until" is a dismissal.',
  );
  process.exit(1);
}

const accounted = Object.keys(ACCOUNTED_FOR).length;
console.log(
  `desktop: ${found.length} advisories, ${serious.length} at ${FAILS_AT[0]} or above, ` +
    `all ${serious.length ? `accounted for (${accounted} written down)` : "clear"}.`,
);
