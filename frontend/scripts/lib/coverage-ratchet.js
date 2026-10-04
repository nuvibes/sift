// The comparison both coverage ratchets make (the client's and the desktop shell's), written
// once.
//
// ## Why the share alone is the wrong question
//
// A share of lines falls for two opposite reasons. Code arriving with thin tests pulls it down, and
// that is the regression the ratchet exists for. But deleting a file that was covered better than
// the average pulls it down too, with nothing having got worse.
//
// So the share is read beside the count of lines no test runs (`uncovered_lines`). A regression is
// the share falling AND that count rising: more untested code, and a bigger part of the whole. Each
// of the other three moves is not one:
//
//   - a well-covered file deleted: the share falls, the uncovered count stays put;
//   - code added with better coverage than the average: the count rises, the share rises too;
//   - a poorly covered file deleted: the share rises, the count falls.
//
// `untouched` (files no test reaches at all) keeps its own rule and may only fall.
//
// ## Why a move that is not a regression still has to be recorded
//
// Because the next comparison is made against whatever is recorded. Leave one number worse than the
// record and the gate is measuring against a pair that does not exist: after code arrives covered
// better than average (share 90 -> 91, uncovered 100 -> 105), a thin change that takes the share to
// 90.5 and the count to 110 has fallen against the truth and risen against it, and passes against
// the stale record because 90.5 is not below 90. That is the ratchet convention in `tree.js` (the
// recorded number must always be the real one) applied to a pair.
//
// Kept in `frontend/scripts/lib` and imported by the shell's script as well, so the two ratchets
// cannot come to disagree about what a regression is.

/**
 * @typedef {{ untouched: number, lines: number, uncovered_lines: number }} Coverage
 * @typedef {{ regressed: string[], worse: string[], better: string[] }} Verdict
 */

/**
 * Compare one run against the record.
 *
 * `regressed` is what fails the gate. `worse` names a number that moved the wrong way without
 * being a regression; `better`, one that moved the right way. Either of those means the record is
 * out of date and is to be written in the same commit.
 *
 * @param {Coverage} recorded
 * @param {Coverage} measured
 * @returns {Verdict}
 */
export function compareCoverage(recorded, measured) {
	const shareFell = measured.lines < recorded.lines;
	const uncoveredRose = measured.uncovered_lines > recorded.uncovered_lines;

	const regressed = [];
	if (measured.untouched > recorded.untouched) regressed.push('untouched');
	if (shareFell && uncoveredRose) regressed.push('lines');

	/* The share alone is not a move. It is the count of uncovered lines over a total the coverage
	 * tool counts, and that total differs by a line or two between Windows and Linux for the
	 * same tree (a share of 97.26 on one and 97.27 on the other, with the same count of uncovered
	 * lines). A record can hold one number, so a share that moved
	 * with the counts unchanged would fail on whichever side did not write it. The counts are what
	 * the tests changed; the share still says a regression together with them, above. */
	const worse = [];
	const better = [];
	if (!regressed.includes('lines') && uncoveredRose) worse.push('uncovered_lines');
	if (measured.untouched < recorded.untouched) better.push('untouched');
	if (measured.uncovered_lines < recorded.uncovered_lines) better.push('uncovered_lines');
	return { regressed, worse, better };
}

/**
 * The three numbers out of a v8 `coverage-summary.json`.
 *
 * @param {Record<string, { lines: { pct: number, total: number, covered: number } }>} summary
 * @returns {Coverage}
 */
export function measure(summary) {
	const files = Object.entries(summary).filter(([path]) => path !== 'total');
	return {
		untouched: files.filter(([, one]) => one.lines.pct === 0).length,
		lines: summary.total.lines.pct,
		uncovered_lines: summary.total.lines.total - summary.total.lines.covered
	};
}

/**
 * What a run that is not a regression but moved a number says, one line per number.
 *
 * @param {Coverage} recorded
 * @param {Coverage} measured
 * @param {Verdict} verdict
 * @returns {string}
 */
export function describeMove(recorded, measured, verdict) {
	/** @param {keyof Coverage} name */
	const was = (name) => `${name} ${measured[name]} (recorded ${recorded[name]})`;
	const lines = [
		...verdict.worse.map((name) => `    worse:  ${was(/** @type {keyof Coverage} */ (name))}`),
		...verdict.better.map((name) => `    better: ${was(/** @type {keyof Coverage} */ (name))}`)
	];
	if (verdict.worse.length > 0) {
		lines.push(
			'    Not a regression: the share of lines covered falls AND the count of uncovered lines',
			'    rises only when code arrives with thinner tests than the rest, and here only one moved.'
		);
	}
	return lines.join('\n');
}
