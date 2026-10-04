#!/usr/bin/env node
/*
 * Every string a person reads in the client, held to the screen's words, as a ratchet, per file.
 *
 * ## What it reads
 *
 * `lib/copy.js`: every string the client puts on screen, found with the Svelte and TypeScript
 * parsers by WHERE it is written: text between tags, every copy attribute (a dialog's
 * `consequence`, `message`, `detail`, `lead` among them), lookup tables, toasts, the words a `.ts`
 * helper returns, the arms of a ternary.
 *
 * ## What it counts, per file
 *
 * The words come from `tests/gates/data/vocabulary.json`, the one list every copy gate reads
 * (`lib/vocabulary.js` loads it here, `tests/gates/vocabulary.py` in Python).
 *
 * - `wrong_words`: the one-word-per-thing map, with its per-screen scopes, counted over every
 *   string and held at zero (`HELD`). This is the one reader of the client's copy for it; the
 *   Python gate reads the server's.
 * - `retired_words`, `insider_phrases`: the words the agreed vocabulary replaced, and the words
 *   from inside the machinery.
 * - `contractions`: the uncontracted forms a person would not say ("cannot", "does not", "it
 *   is"),
 *   each naming its contraction. What remains is allowed on purpose: "cannot" in a legal or security
 *   statement of impossibility, and a warning that opens "It is".
 * - `phrasing`: the shapes of a sentence a person would not write ("take it out", "at once", a
 *   subject left out, a rule said as a slogan, one pronoun for two things, over 25 words).
 * - `spelling`: British spellings. Not a ratchet: held at zero everywhere (`heldAtZero` below).
 * - `lower_case_names`: a name written without its capital ("the site"). Held at zero too.
 * - `verbs`: a button's text, a confirm label or a menu row's label whose first word is not on
 *   the agreed verb list, or is a verb scoped to other screens (the swap's Start, Join, End).
 * - `title_case`: a LABEL (a heading, a badge, a button, a menu row: `labelShaped`) with a
 *   capital after its first word that is not a name (`notSentenceCase`, the Python settings gate's
 *   rule over the one list of names). One count per label, however many of its words are
 *   capitalised.
 * - `raw_count`: a number written before the word it counts without `counted()` grouping it
 *   (`rawCounts`): `${n} files`, `{row.count} files`, "4000 files". Read off the source rather than
 *   the copy, because the copy reader turns a substitution into a gap. Held at zero (`HELD`).
 *
 * ## Why a ratchet and not a ban
 *
 * Bringing each count to zero is a large rewrite, and words come back while it is in progress. So
 * each file's count is recorded in `scripts/gate-baselines.json` under `vocabulary`, a rise fails,
 * a file with no entry is held at zero, and a fall fails until it is recorded (`node
 * scripts/check_vocabulary.js --record`, which the pre-commit hook runs for you), so the recorded
 * number is always the true one: the same ratchet as `lib/tree.js`, one level deeper because it
 * is kept per file.
 *
 * A check whose rewrite has finished leaves the ratchet for `HELD_AT_ZERO` (`lib/vocabulary.js`):
 * zero everywhere, nothing recorded, and `--record` refused while one is found.
 */

import { readFile, writeFile } from 'node:fs/promises';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';

import { copyIn } from './lib/copy.js';
import {
	BASELINES,
	RATCHET_FELL,
	SOURCE,
	everyFile,
	fromSource,
	recording,
	withoutComments
} from './lib/tree.js';
import {
	CHECKS,
	HELD_AT_ZERO,
	isBusyLabel,
	labelShaped,
	notSentenceCase,
	offences,
	rawCounts,
	startsWithAVerb,
	verbInstead
} from './lib/vocabulary.js';

const GATE = 'vocabulary';

/**
 * One offence, as the gate lists it: which check, where, what was found and what to write.
 *
 * @typedef {{
 *   check: string,
 *   file: string,
 *   line: number,
 *   found: string,
 *   instead: string,
 *   text: string
 * }} Offence
 */

/** @typedef {Record<string, number>} PerFile A count per file under `src`. */
/** What `gate-baselines.json` records per file. */
/*
 * `wrong_words` is at zero and HELD below, so a regression is refused outright and `--record`
 * cannot write one down. It is still counted with the others (`COUNTED`). This is the one reader
 * of the client's copy for it; the Python gate reads the server's copy only.
 */
const ALL_CHECKS = [...CHECKS.filter((check) => check !== 'wrong_words'), 'verbs', 'title_case'];
/**
 * Every check held at ZERO: the word lists `lib/vocabulary.js` holds there, and `raw_count`, which
 * starts held rather than recorded: a ratchet would let `--record` write a regression down.
 */
const HELD = [...HELD_AT_ZERO, 'raw_count', 'wrong_words'];
/** Every check read off each string: the recorded ones, and the ones held at zero. */
const COUNTED = [...CHECKS, ...HELD_AT_ZERO];
/** Every check a count is kept for, for the totals line and `--survey`. */
const REPORTED = [...ALL_CHECKS, ...HELD];

/**
 * The component gallery. It is its own repository, nested here and absent from a clone, so a count
 * that read it would record one number in this tree and find another in a clone. The gates that
 * count per file leave it out by this same prefix.
 */
const GALLERY = 'routes/design/';

/**
 * Not counted: a test, a generated declaration, or the gallery.
 *
 * @param {string} where
 */
const skipped = (where) => /\.test\.|\.d\.ts$/.test(where) || where.startsWith(GALLERY);

/**
 * Files whose every value is a name, so sentence case is not a question they can be asked: the
 * country table ("United Arab Emirates", "Isle of Man") is the whole of it.
 *
 * @param {string} where
 */
const allNames = (where) => where === 'lib/people/countries.ts';

/**
 * `{ copy, counts, unreadable }` for the whole tree: counts[check][file] = occurrences.
 *
 * @returns {Promise<{
 *   counts: Record<string, PerFile>,
 *   where: Offence[],
 *   unreadable: string[],
 *   strings: number,
 *   files: number
 * }>}
 */
export async function readTree() {
	/** @type {Record<string, PerFile>} */
	const counts = Object.fromEntries(REPORTED.map((check) => [check, {}]));
	/** @type {Offence[]} */
	const where = [];
	/** @type {string[]} */
	const unreadable = [];
	let strings = 0;
	let files = 0;
	for (const path of await everyFile(SOURCE, ['.svelte', '.ts'])) {
		const file = fromSource(path);
		if (skipped(file)) continue;
		const source = await readFile(path, 'utf8');
		const plain = withoutComments(source, { markup: path.endsWith('.svelte') });
		for (const raw of rawCounts(plain)) {
			counts.raw_count[file] = (counts.raw_count[file] ?? 0) + 1;
			where.push({
				check: 'raw_count',
				file,
				line: plain.slice(0, raw.offset).split('\n').length,
				found: raw.found,
				instead:
					'counted(...) from $lib/entity/entity-counts, the one formatter that groups a count',
				text: raw.found
			});
		}
		const copy = copyIn(source, file, (specifier) => {
			// The pane's copy module sits beside the component; a specifier it cannot read is no module.
			try {
				return readFileSync(resolve(dirname(path), `${specifier}.ts`), 'utf8');
			} catch {
				return null;
			}
		});
		files += 1;
		for (const one of copy) {
			if (one.kind === 'unreadable') {
				unreadable.push(`${file}: ${one.text}`);
				continue;
			}
			strings += 1;
			for (const check of COUNTED) {
				for (const hit of offences(check, one.text, file)) {
					counts[check][file] = (counts[check][file] ?? 0) + 1;
					where.push({
						check,
						file,
						line: one.line,
						found: hit.found,
						instead: hit.instead,
						text: one.text
					});
				}
			}
			// A label that opens with a substitution (`${n} more`) starts with data, not a verb.
			// A busy state ("Saving\u2026") is the control mid-press, not what it does: `isBusyLabel`.
			if (
				one.control &&
				!/^\s/.test(one.text) &&
				!isBusyLabel(one.text) &&
				!startsWithAVerb(one.text, file)
			) {
				counts.verbs[file] = (counts.verbs[file] ?? 0) + 1;
				const found = one.text.trim().split(/\s+/)[0] ?? '';
				where.push({
					check: 'verbs',
					file,
					line: one.line,
					found,
					instead: verbInstead(one.text),
					text: one.text
				});
			}
			const shouted = allNames(file) || !labelShaped(one.text) ? [] : notSentenceCase(one.text);
			if (shouted.length > 0) {
				counts.title_case[file] = (counts.title_case[file] ?? 0) + 1;
				where.push({
					check: 'title_case',
					file,
					line: one.line,
					found: shouted.join(' '),
					instead: 'sentence case: a capital only at the start and on a name in vocabulary.json',
					text: one.text
				});
			}
		}
	}
	return { counts, where, unreadable, strings, files };
}

/**
 * `{ rose, fell }` for one check, as `file: now, recorded n` lines.
 *
 * @param {PerFile} now
 * @param {PerFile} recorded
 * @returns {{ rose: string[], fell: string[] }}
 */
export function compare(now, recorded) {
	const rose = [];
	const fell = [];
	for (const file of [...new Set([...Object.keys(now), ...Object.keys(recorded)])].sort()) {
		const today = now[file] ?? 0;
		const allowed = recorded[file] ?? 0;
		if (today > allowed) rose.push(`${file}: ${today}, recorded ${allowed}`);
		else if (today < allowed) fell.push(`${file}: ${today}, recorded ${allowed}`);
	}
	return { rose, fell };
}

/**
 * Every complaint for a check held at zero, whatever the baselines say.
 *
 * `where` is `readTree`'s list of offences. `recorded` is the gate's baselines, read for one thing
 * only: a number written beside a held check would read as an allowance, so an entry is refused
 * rather than ignored.
 *
 * @param {Offence[]} where
 * @param {Record<string, PerFile>} recorded
 * @returns {string[]}
 */
export function heldAtZero(where, recorded) {
	const complaints = [];
	for (const check of HELD) {
		if (recorded[check] !== undefined) {
			complaints.push(
				`${GATE}.${check}: is held at zero and is never recorded; delete its entry under "${GATE}" in scripts/gate-baselines.json.`
			);
		}
		const found = where.filter((one) => one.check === check);
		if (found.length > 0) {
			const lines = found.map(
				(one) =>
					`      ${one.file}:${one.line}  "${one.found}" -> ${one.instead}\n          ${one.text.trim().slice(0, 100)}`
			);
			complaints.push(
				`${GATE}.${check}: held at zero, and ${found.length} came back.\n${lines.slice(0, 40).join('\n')}`
			);
		}
	}
	return complaints;
}

async function main() {
	const { counts, where, unreadable, strings, files } = await readTree();
	if (unreadable.length > 0) {
		console.error(
			`vocabulary: a file the reader could not parse is a file nobody checked:\n  ${unreadable.join('\n  ')}`
		);
		process.exit(1);
	}
	/* A reader that found nothing would hold every count at zero and pass forever. */
	if (strings < 3000 || files < 300) {
		console.error(
			`vocabulary: read only ${strings} strings in ${files} files, which cannot be right.`
		);
		process.exit(1);
	}

	const all = JSON.parse(await readFile(BASELINES, 'utf8'));
	/* Before any recording, so `--record` cannot write a held check's regression down either. */
	const held = heldAtZero(where, all[GATE] ?? {});
	if (held.length > 0) {
		console.error(
			`${held.join('\n\n')}\n\nUse the agreed word: tests/gates/data/vocabulary.json says which.`
		);
		process.exit(1);
	}
	if (all[GATE] === undefined) {
		/* The first run records what is there; every run after it holds it. */
		if (!recording()) {
			console.error(
				`${GATE}: nothing recorded in scripts/gate-baselines.json. Run: node scripts/check_vocabulary.js --record`
			);
			process.exit(1);
		}
		all[GATE] = Object.fromEntries(ALL_CHECKS.map((check) => [check, sortedCounts(counts[check])]));
		await writeFile(BASELINES, `${JSON.stringify(all, null, '\t')}\n`, 'utf8');
		console.log(`${GATE}: first baseline recorded in scripts/gate-baselines.json.`);
		return;
	}
	const recorded = all[GATE];
	const complaints = [];
	/** @type {[string, string[]][]} */
	const fallen = [];
	for (const check of ALL_CHECKS) {
		const { rose, fell } = compare(counts[check], recorded[check] ?? {});
		if (rose.length > 0) {
			const risen = new Set(rose.map((line) => line.split(':')[0]));
			const lines = where
				.filter((one) => one.check === check && risen.has(one.file))
				.map(
					(one) =>
						`      ${one.file}:${one.line}  "${one.found}" -> ${one.instead}\n          ${one.text.trim().slice(0, 100)}`
				);
			complaints.push(
				`${GATE}.${check}: a file says more of it than was recorded.\n    ${rose.join('\n    ')}\n${lines.slice(0, 40).join('\n')}`
			);
		}
		if (fell.length > 0) fallen.push([check, fell]);
	}

	if (complaints.length > 0) {
		console.error(
			`${complaints.join('\n\n')}\n\nUse the agreed word: tests/gates/data/vocabulary.json says which.`
		);
		process.exit(1);
	}
	if (fallen.length > 0) {
		if (!recording()) {
			console.error(
				`${RATCHET_FELL} ${GATE}: ${fallen.map(([check, fell]) => `${check} (${fell.length} files)`).join(', ')} fell. ` +
					'Record it, so the progress cannot be given back: node scripts/check_vocabulary.js --record\n' +
					'    (the pre-commit hook does this for you; stage scripts/gate-baselines.json and commit again).'
			);
			process.exit(1);
		}
		const fresh = JSON.parse(await readFile(BASELINES, 'utf8'));
		fresh[GATE] = Object.fromEntries(
			ALL_CHECKS.map((check) => [check, sortedCounts(counts[check])])
		);
		await writeFile(BASELINES, `${JSON.stringify(fresh, null, '\t')}\n`, 'utf8');
		console.log(`${GATE}: recorded the fall in scripts/gate-baselines.json.`);
	}
	const totals = REPORTED.map(
		(check) => `${check} ${Object.values(counts[check]).reduce((a, b) => a + b, 0)}`
	);
	console.log(`vocabulary: ${strings} strings in ${files} files; ${totals.join(', ')}`);
}

/** @param {PerFile} per */
const sortedCounts = (per) =>
	Object.fromEntries(Object.entries(per).sort(([a], [b]) => a.localeCompare(b)));

/* `--survey` prints what would be recorded, for a first baseline or a reader change. */
if (process.argv.includes('--survey')) {
	const { counts, strings, files, unreadable } = await readTree();
	console.log(
		JSON.stringify(
			{
				strings,
				files,
				unreadable,
				vocabulary: Object.fromEntries(REPORTED.map((c) => [c, sortedCounts(counts[c])]))
			},
			null,
			'\t'
		)
	);
} else if (process.argv[1] && process.argv[1].endsWith('check_vocabulary.js')) {
	await main();
}
