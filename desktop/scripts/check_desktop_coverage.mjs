// What the desktop shell's tests actually reach, and it may only get better.
//
// ## Why this is a ratchet and not a 100% gate
//
// New Electron main-process code cannot be held at 100% from the start without tests that execute
// lines and assert nothing, which is worse than no test because it reads as covered. So this
// measures the direction of travel, and permits only one direction.
//
// ## Three numbers, and the first one is the point
//
// `untouched` is how many files no test reaches at all. A file with NO test reads exactly like a
// file whose tests all pass. It may only go DOWN.
//
// `lines` is the share of lines the tests execute, and `uncovered_lines` the count of lines they do
// not. A regression is the share falling AND the count rising together: code arriving with
// thinner tests than the rest; either moving alone is not one (deleting a well-covered file lowers
// the share and adds no untested line). The comparison is the client's, imported from
// `frontend/scripts/lib/coverage-ratchet.js` so the two ratchets cannot disagree about what a
// regression is. The pair is the weaker half of the gate (a file can be half-covered by a test
// that asserts nothing), so it is here to stop a silent slide rather than to be aimed at.
//
// Any move that is not a regression is recorded in the same commit: the next run is compared
// against the record, and a record one number out of date lets a real regression through.
//
// ## What a zero here means
//
// No test reaches the file. main.test.ts imports `main.ts` against doubles of everything it
// composes; what only a real Electron can show (the window's options taking effect, the process
// lifecycle) is walked by driving the installed application.

import { readFile, writeFile } from 'node:fs/promises';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
	compareCoverage,
	describeMove,
	measure
} from '../../frontend/scripts/lib/coverage-ratchet.js';

const HERE = dirname(fileURLToPath(import.meta.url));
const SUMMARY = join(HERE, '..', 'coverage', 'coverage-summary.json');
const BASELINE = join(HERE, 'desktop-coverage-baseline.json');
const ROOT = join(HERE, '..');

let summary;
try {
	summary = JSON.parse(await readFile(SUMMARY, 'utf8'));
} catch {
	console.error(
		`\ndesktop-coverage: no report at ${relative(ROOT, SUMMARY)}.\n` +
			`  Run it first (npm run test:coverage), which is what the gate script does.\n`
	);
	process.exit(1);
}

const files = Object.entries(summary).filter(([path]) => path !== 'total');

/* A report that measured nothing would pass forever. The floor is low but not zero: a count of
   one or two means `all: true` came out of vitest.config.mts and only imported files are in the
   report. */
if (files.length < 5) {
	console.error(
		`\ndesktop-coverage: the report holds only ${files.length} files.\n` +
			`  That is too few to be right. Check that coverage.all is still true in vitest.config.ts:\n` +
			`  without it a file no test imports is ABSENT from the report rather than at zero.\n`
	);
	process.exit(1);
}

const untouched = files.filter(([, one]) => one.lines.pct === 0).map(([path]) => path);
const measured = measure(summary);

const baseline = JSON.parse(await readFile(BASELINE, 'utf8'));

if (
	baseline.untouched === undefined ||
	baseline.lines === undefined ||
	baseline.uncovered_lines === undefined
) {
	console.error(
		`\ndesktop-coverage: nothing recorded. Add "untouched": ${measured.untouched}, ` +
			`"lines": ${measured.lines}, "uncovered_lines": ${measured.uncovered_lines}.\n`
	);
	process.exit(1);
}

const verdict = compareCoverage(baseline, measured);
const complaints = [];

if (verdict.regressed.includes('untouched')) {
	complaints.push(
		`  ${measured.untouched} files are reached by no test, and the recorded number is ` +
			`${baseline.untouched}.\n` +
			`    A file with no test looks exactly like a file whose tests all pass. Write one, or\n` +
			`    (if it is wiring that only exists inside a running Electron) drive the real window\n` +
			`    instead and say so here.\n` +
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
		`\nWhat the desktop shell's tests reach may only get better.\n\n${complaints.join('\n\n')}\n`
	);
	process.exit(1);
}

/* A move that is not a regression is recorded rather than passed on. Slack left in a ratchet is
   what the next regression spends, and a record one number out of date is measured against a pair
   that no longer exists (see `coverage-ratchet.js`). */
if (verdict.worse.length > 0 || verdict.better.length > 0) {
	if (process.env.SIFT_RECORD_COVERAGE === '1' || process.argv.includes('--record')) {
		const next = { ...baseline, ...measured };
		await writeFile(BASELINE, `${JSON.stringify(next, null, '\t')}\n`, 'utf8');
		console.log(
			`desktop-coverage: recorded ${measured.untouched} untouched / ${measured.lines}% lines / ` +
				`${measured.uncovered_lines} uncovered lines.`
		);
		process.exit(0);
	}
	console.error(
		`\ndesktop-coverage: moved from the recorded numbers. Record it in this commit.\n` +
			`${describeMove(baseline, measured, verdict)}\n` +
			`      SIFT_RECORD_COVERAGE=1 npm run gate:coverage\n` +
			`    or: node scripts/check_desktop_coverage.mjs --record\n`
	);
	process.exit(1);
}

console.log(
	`desktop-coverage: ${measured.untouched} files no test reaches, ${measured.lines}% of lines ` +
		`run, ${measured.uncovered_lines} uncovered (at the recorded numbers)`
);
