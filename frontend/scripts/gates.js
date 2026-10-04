// Every structural gate over the client, in one run, before a commit exists.
//
// ## Why this exists
//
// A gate that runs after the fact is a report; a gate that runs before the commit is a rule. This
// is the rule.
//
// ## What it runs, and what it leaves to the wide run
//
// Every `check_*.js` in this folder that reads the source tree and nothing else. Three are left out
// because they are not that: the dead-CSS gate needs `svelte-check` (a full type pass), the
// coverage gate needs the whole unit suite, and the offline gate reads the BUILT output. Those stay
// where they are, in `ci-local.sh` and the workflow, and the orphan guard
// (`tests/gates/test_ci_runs_where_sift_ships.py`) keeps them wired.
//
// ## In parallel, then the fallen ratchets again with --record
//
// Each gate is its own node process and they are independent, so they run at once: a few seconds of
// wall clock rather than twenty. The one thing that cannot run in parallel is RECORDING: several
// ratchets write the same baseline file, and two writers racing on one JSON file lose one of the
// writes. So the first pass never records. A gate that failed only because its number FELL (it says
// so: `RATCHET FELL`) is run again afterwards, one at a time, with `--record`. The baseline
// changes on disk, pre-commit reports that a hook modified a file, and the commit is refused once
// so the new number can be staged: the same shape the formatter hook already has.
//
// ## Why not one process with one tree walk
//
// It would be faster still, and every gate would have to become a function first. They are scripts
// with `process.exit` at the bottom, and converting all of them in one change is a rewrite nobody
// can review. Spawning them costs under a second each, in parallel. If that stops being cheap, the
// conversion is the answer.

import { spawn } from 'node:child_process';
import { readdir } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { RATCHET_FELL } from './lib/tree.js';

const HERE = dirname(fileURLToPath(import.meta.url));

/** Not structural: each needs something other than the source tree. Named, with the reason. */
const NOT_HERE = {
	'check_no_dead_css.js': 'runs svelte-check, a full type pass',
	'check_client_coverage.js': 'runs the whole unit suite',
	'check_no_external_refs.js': 'reads the built output, not the source'
};

function run(script, args = []) {
	return new Promise((resolve) => {
		const child = spawn(process.execPath, [join(HERE, script), ...args], { cwd: join(HERE, '..') });
		let out = '';
		child.stdout.on('data', (chunk) => (out += chunk));
		child.stderr.on('data', (chunk) => (out += chunk));
		child.on('close', (code) => resolve({ script, code: code ?? 1, out }));
	});
}

const gates = (await readdir(HERE))
	.filter((name) => /^check_.*\.js$/.test(name) && !(name in NOT_HERE))
	.sort();

/* A run that found nothing to run would pass, forever. */
if (gates.length < 20) {
	console.error(
		`gates: found only ${gates.length} check scripts beside this file. That is too few to be right.`
	);
	process.exit(1);
}

const started = Date.now();
const results = await Promise.all(gates.map((gate) => run(gate)));

const fell = results.filter((one) => one.code !== 0 && one.out.includes(RATCHET_FELL));
const failed = results.filter((one) => one.code !== 0 && !one.out.includes(RATCHET_FELL));

/* Recording, one at a time, and only the ones whose sole complaint was a fall. */
const recorded = [];
for (const one of fell) {
	const again = await run(one.script, ['--record']);
	if (again.code === 0) recorded.push(one.script);
	else failed.push(again);
}

const seconds = ((Date.now() - started) / 1000).toFixed(1);

for (const one of failed) {
	console.error(`\n--- ${one.script} ---\n${one.out.trim()}\n`);
}
if (recorded.length > 0) {
	console.error(
		`\nRecorded ${recorded.length} ratchet(s) that fell: ${recorded.join(', ')}.\n` +
			`scripts/gate-baselines.json changed. Stage it and commit again: the number can only go down, and now it has.\n`
	);
}

if (failed.length > 0 || recorded.length > 0) {
	console.error(
		`gates: ${failed.length} failed, ${recorded.length} recorded, of ${gates.length} in ${seconds}s`
	);
	process.exit(1);
}

console.log(`gates: ${gates.length} structural gates green in ${seconds}s`);
