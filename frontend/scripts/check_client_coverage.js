// What the browser client's tests actually reach, and it may only get better.
//
// ## Why this exists
//
// A file with NO test reads exactly like a file whose tests all pass, and no other gate in this
// directory can tell the two apart.
//
// ## Three numbers, and the first one is the point
//
// `untouched` is how many files no unit test reaches at all. That is the fault above, counted. It
// may only go DOWN.
//
// `lines` is the share of lines the unit tests execute, and `uncovered_lines` the count of lines
// they do not. A regression is the share falling AND the count rising together: code arriving
// with thinner tests than the rest. Either moving alone is not one: deleting a well-covered file
// lowers the share and adds no untested line. `scripts/lib/coverage-ratchet.js` holds that
// comparison, once, for this gate and the desktop shell's. The pair is the weaker half of the gate
// (a file can be half-covered by a test that asserts nothing), so it is here to stop a silent
// slide rather than to be aimed at.
//
// ## What a zero here does and does not mean
//
// It means no unit test reaches the file. Several of these are walked in a real browser by the
// end-to-end suite instead, which is the right place for a screen assembled out of other screens,
// and their zero is not a defect. What the number is for is the direction: nothing may add to it
// without saying so.
//
// Routes are out of the measurement entirely. See the note in `vite.config.ts`.
//
// ## The recorded numbers
//
// They live under the `client-coverage` key of `scripts/gate-baselines.json`, with every other
// gate's. Record a move with `SIFT_RECORD_COVERAGE=1 npm run gate:client-coverage`, or with
// `--record`, which is the spelling the other ratchets use.
//
// Any move that is not a regression still has to be recorded, in the same commit: the next run is
// compared against the record, and a record one number out of date lets a real regression through
// (the example is in `coverage-ratchet.js`).
//
// ## Why this one does not go through `ratchet()`
//
// The shared ratchet compares one number that may only fall. `lines` runs the other way, and it is
// judged beside `uncovered_lines` rather than alone; the three numbers here are recorded together,
// in one write, because a move in one beside no change in another is still one event. The walk, the
// paths and the file it writes are shared; the comparison is its own.

import { readFile, writeFile } from 'node:fs/promises';
import { join, relative } from 'node:path';

import { compareCoverage, describeMove, measure } from './lib/coverage-ratchet.js';
import { BASELINES, FRONTEND, recorded, recording } from './lib/tree.js';

const SUMMARY = join(FRONTEND, 'coverage', 'coverage-summary.json');
const ROOT = FRONTEND;
const GATE = 'client-coverage';

let summary;
try {
	summary = JSON.parse(await readFile(SUMMARY, 'utf8'));
} catch {
	console.error(
		`\nclient-coverage: no report at ${relative(ROOT, SUMMARY)}.\n` +
			`  Run it first (npm run test:unit:coverage), which is what the gate script does.\n`
	);
	process.exit(1);
}

const files = Object.entries(summary).filter(([path]) => path !== 'total');

/* A report that measured nothing would pass forever. There are hundreds of files under src/lib; if
   this reads a handful then `all: true` came out of the config and only the imported ones are in
   the report, which is the same blindness the gate exists to remove, wearing a green tick. */
if (files.length < 200) {
	console.error(
		`\nclient-coverage: the report holds only ${files.length} files.\n` +
			`  That is too few to be right. Check that coverage.all is still true in vite.config.ts:\n` +
			`  without it a file no test imports is ABSENT from the report rather than at zero.\n`
	);
	process.exit(1);
}

const untouched = files.filter(([, one]) => one.lines.pct === 0).map(([path]) => path);
const measured = measure(summary);

const baseline = await recorded(GATE);

if (
	baseline.untouched === undefined ||
	baseline.lines === undefined ||
	baseline.uncovered_lines === undefined
) {
	console.error(
		`\nclient-coverage: nothing recorded under "${GATE}" in scripts/gate-baselines.json.\n` +
			`  Add "untouched": ${measured.untouched}, "lines": ${measured.lines}, ` +
			`"uncovered_lines": ${measured.uncovered_lines}.\n`
	);
	process.exit(1);
}

const verdict = compareCoverage(baseline, measured);
const complaints = [];

if (verdict.regressed.includes('untouched')) {
	complaints.push(
		`  ${measured.untouched} files are reached by no unit test, and the recorded number is ` +
			`${baseline.untouched}.\n` +
			`    A file with no test looks exactly like a file whose tests all pass. Write one, or,\n` +
			`    if it is a screen that only makes sense assembled, put it in the end-to-end suite\n` +
			`    and say so here.\n` +
			`    The new ones are among:\n` +
			untouched.map((one) => `      ${relative(ROOT, one)}`).join('\n')
	);
}

if (verdict.regressed.includes('lines')) {
	complaints.push(
		`  Line coverage is ${measured.lines}% (recorded ${baseline.lines}%) and ` +
			`${measured.uncovered_lines} lines run under no test (recorded ${baseline.uncovered_lines}).\n` +
			`    Both moved the wrong way together, which is code arriving with thinner tests than the\n` +
			`    rest of it. Test what was added.`
	);
}

if (complaints.length > 0) {
	console.error(
		`\nWhat the client's tests reach may only get better.\n\n${complaints.join('\n\n')}\n`
	);
	process.exit(1);
}

/* A move that is not a regression is recorded rather than passed on. Slack left in a ratchet is
   what the next regression spends, and a record one number out of date is measured against a pair
   that no longer exists. See `coverage-ratchet.js`. */
if (verdict.worse.length > 0 || verdict.better.length > 0) {
	/* Two ways to say "record this": the environment variable the npm script has always used, and
	   `--record`, which is what the other nine ratchets answer to. */
	if (process.env.SIFT_RECORD_COVERAGE === '1' || recording()) {
		const all = JSON.parse(await readFile(BASELINES, 'utf8'));
		all[GATE] = { ...baseline, ...measured };
		await writeFile(BASELINES, `${JSON.stringify(all, null, '\t')}\n`, 'utf8');
		console.log(
			`client-coverage: recorded ${measured.untouched} untouched / ${measured.lines}% lines / ` +
				`${measured.uncovered_lines} uncovered lines.`
		);
		process.exit(0);
	}
	console.error(
		`\nclient-coverage: moved from the recorded numbers: record it in this commit.\n` +
			`${describeMove(baseline, measured, verdict)}\n` +
			`      SIFT_RECORD_COVERAGE=1 npm run gate:client-coverage\n` +
			`    or: node scripts/check_client_coverage.js --record\n`
	);
	process.exit(1);
}

console.log(
	`client-coverage: ${measured.untouched} files no unit test reaches, ${measured.lines}% of ` +
		`lines run, ${measured.uncovered_lines} uncovered (at the recorded numbers)`
);
