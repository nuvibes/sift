/**
 * What the two coverage ratchets call a regression, driven with numbers made for the purpose.
 *
 * A regression is the share of covered lines falling AND the count of uncovered lines rising
 * together: deleting a file that was covered better than the average lowers the share and adds no
 * untested line, and is not one. `scripts/lib/coverage-ratchet.js` holds the comparison for both
 * the client's gate and the desktop shell's; these are its four cases, and the ones around them.
 */

import { describe, expect, it } from 'vitest';

import { compareCoverage, describeMove, measure } from '../../../scripts/lib/coverage-ratchet.js';

/** A library of 1,000 lines, 800 of them covered, every file reached. */
const RECORDED = { untouched: 0, lines: 80, uncovered_lines: 200 };

/** The share after a move: covered lines over all lines, to two places as v8 reports it. */
const share = (covered: number, total: number) => Math.round((covered / total) * 10000) / 100;

describe('a coverage move', () => {
	it('is not a regression when a well-covered file is deleted', () => {
		// 100 lines at 100% gone: 700 of 900 covered. The share falls; nothing untested was added.
		const verdict = compareCoverage(RECORDED, {
			untouched: 0,
			lines: share(700, 900),
			uncovered_lines: 200
		});
		expect(verdict.regressed).toEqual([]);
		// The share fell with the counts unchanged, which is no move: the share is a count over a
		// total the tool counts, and the total moves with the operating system.
		expect(verdict.worse).toEqual([]);
	});

	it('is not a regression when code arrives covered better than the average', () => {
		// 100 lines at 95%: 895 of 1,100. The count rises by five, and the share rises with it.
		const verdict = compareCoverage(RECORDED, {
			untouched: 0,
			lines: share(895, 1100),
			uncovered_lines: 205
		});
		expect(verdict.regressed).toEqual([]);
		expect(verdict.worse).toEqual(['uncovered_lines']);
		expect(verdict.better).toEqual([]);
	});

	it('is a regression when code arrives with thin coverage', () => {
		// 100 lines at 20%: 820 of 1,100. The share falls AND the count rises.
		const verdict = compareCoverage(RECORDED, {
			untouched: 0,
			lines: share(820, 1100),
			uncovered_lines: 280
		});
		expect(verdict.regressed).toEqual(['lines']);
		expect(verdict.worse).toEqual([]);
	});

	it('is not a regression when a poorly covered file is deleted', () => {
		// 100 lines at 10% gone: 790 of 900. Both numbers improve.
		const verdict = compareCoverage(RECORDED, {
			untouched: 0,
			lines: share(790, 900),
			uncovered_lines: 110
		});
		expect(verdict.regressed).toEqual([]);
		expect(verdict.better).toEqual(['uncovered_lines']);
	});

	it('still refuses a file no test reaches, whatever the lines do', () => {
		const verdict = compareCoverage(RECORDED, { untouched: 1, lines: 90, uncovered_lines: 100 });
		expect(verdict.regressed).toEqual(['untouched']);
	});

	it('names nothing when every number is where it was recorded', () => {
		expect(compareCoverage(RECORDED, { ...RECORDED })).toEqual({
			regressed: [],
			worse: [],
			better: []
		});
	});

	it('says why a move with one number worse is not a regression', () => {
		// One more uncovered line inside code that arrived covered better than the average.
		const measured = { untouched: 0, lines: 80.2, uncovered_lines: 201 };
		const said = describeMove(RECORDED, measured, compareCoverage(RECORDED, measured));
		expect(said).toContain('worse:  uncovered_lines 201 (recorded 200)');
		expect(said).toContain('Not a regression');
	});
});

describe('the numbers read from a coverage report', () => {
	it('counts the uncovered lines from the totals and the untouched files by name', () => {
		const summary = {
			total: { lines: { pct: 75, total: 8, covered: 6 } },
			'a.ts': { lines: { pct: 100, total: 4, covered: 4 } },
			'b.ts': { lines: { pct: 50, total: 4, covered: 2 } },
			'c.ts': { lines: { pct: 0, total: 0, covered: 0 } }
		};
		expect(measure(summary)).toEqual({ untouched: 1, lines: 75, uncovered_lines: 2 });
	});
});
