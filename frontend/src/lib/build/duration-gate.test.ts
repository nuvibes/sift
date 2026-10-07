/** Every clock and every word for a length of time is written by one file, `src/lib/shell/duration.ts`.
 *
 * ## Why
 *
 * A screen writing its own m:ss clock tends to forget hours, so a ninety-minute film reads "90:00"
 * in one place and "1:30:00" in another, and hand-written words drift the same way ("45 min" beside
 * "3.0 h", "hr"). `duration.ts` explains the one shape and the one set of words; this refuses a
 * second copy of either anywhere in `src/`.
 *
 * ## What it reads, in code with its comments taken out
 *
 * - A clock written by hand: an interpolation that is not padded, a colon, then one that is padded
 *   or taken `% 60`. That is the m:ss shape. A time of DAY ("07:10") pads both sides and is
 *   `$lib/shell/when`'s business, so it is not this.
 * - A duration in its own short words: an interpolation followed by min, hr, sec, or by h or d
 *   after a space.
 * - A duration in its own spelled-out words: "under a minute", "less than a minute", an
 *   interpolation followed by "minutes" or "hours", and a quoted "hr".
 *
 * ## Where it does not look, and why
 *
 * `lib/shell/when.ts` words ESTIMATES ("about 30 to 45 minutes"), a window on purpose, and has its own
 * gate (`scripts/check_one_estimate_format.js`). Tests state what the formatter should say. The
 * design gallery under `routes/design/` is a separate repository that is not always present, and a
 * gate must read the same tree on every machine.
 *
 * A bare `s` after an interpolation is not read: `${noun}s` is how every plural in the client is
 * made, and the two cannot be told apart from the text.
 */

import { readFile } from 'node:fs/promises';

import { describe, expect, it } from 'vitest';

import { everyFile, fromSource, SOURCE, withoutComments } from '../../../scripts/lib/tree.js';

/** The shapes, each with what it is called when it is found. */
const SHAPES: readonly [string, RegExp][] = [
	[
		'an m:ss clock written by hand',
		/\$\{(?![^}]*(?:padStart|\bpad\(|\btwo\())[^}]*\}:\$\{[^}]*(?:padStart\(\s*2|\bpad\(|%\s*60)/
	],
	[
		'an m:ss clock written by hand in markup',
		/\{(?![^}]*padStart)[^{}$]*\}:\{[^}]*(?:padStart\(\s*2|%\s*60)/
	],
	[
		'a duration in its own short words',
		/\$\{[^}]*\}\s?(?:min|mins|hr|hrs|sec|secs)\b|\$\{[^}]*\}\s(?:h|d)\b/
	],
	[
		'a duration in its own short words in markup',
		/\{[^{}$]*\}\s?(?:min|mins|hr|hrs|sec|secs)\b(?!\s*=)/
	],
	[
		'a duration in its own spelled-out words',
		/\b(?:under|less than) a minute\b|\$\{[^}]*\}\s(?:minutes|hours)\b|['"`]hrs?['"`]/
	]
];

/** Every line of `code` that writes a clock or a duration, once each, with the first shape on it. */
function durationsWrittenIn(code: string): { line: number; what: string }[] {
	const found: { line: number; what: string }[] = [];
	code.split('\n').forEach((text, at) => {
		const hit = SHAPES.find(([, shape]) => shape.test(text));
		if (hit) found.push({ line: at + 1, what: hit[0] });
	});
	return found;
}

/** The one file allowed to write a clock or a duration, and the one that words estimates. */
const THE_FORMATTERS = new Set(['lib/shell/duration.ts', 'lib/shell/when.ts']);

/**
 * Copies still standing in files this change did not own, each with how many lines it holds. The
 * list only shrinks: a file that no longer has its copy fails below until it is taken off, and a
 * file not on it fails at its first copy. Each is a one-line change to `$lib/shell/duration`.
 */
const NOT_YET_FOLDED: Record<string, number> = {};

async function everyCopy(): Promise<Record<string, string[]>> {
	const copies: Record<string, string[]> = {};
	for (const path of await everyFile(SOURCE, ['.ts', '.svelte', '.js'])) {
		const where = fromSource(path);
		if (THE_FORMATTERS.has(where) || where.startsWith('routes/design/')) continue;
		if (/\.(test|spec)\.ts$/.test(where) || where.endsWith('.d.ts')) continue;
		const found = durationsWrittenIn(withoutComments(await readFile(path, 'utf8')));
		if (found.length > 0)
			copies[where] = found.map(({ line, what }) => `${where}:${line}  ${what}`);
	}
	return copies;
}

describe('the one-duration gate, handed lines written for the purpose', () => {
	it.each([
		[
			'the player bar clock',
			'return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;'
		],
		['a clock with a pad helper', 'return `${hours}:${pad(minutes)}:${pad(seconds)}`;'],
		['a clock taken modulo sixty', 'return `${m}:${s % 60}`;'],
		['a clock in markup', '<span>{minutes}:{String(seconds).padStart(2, "0")}</span>'],
		['minutes, short', 'return `${Math.round(minutes)} min`;'],
		['hours with hr', 'return `${hours} hr`;'],
		['hours, one letter', 'return `${hours.toFixed(1)} h`;'],
		['days, one letter', 'return `${Math.round(hours / 24)} d`;'],
		['minutes in markup', '<p>{left} min</p>'],
		['a ladder head', "if (minutes < 1) return 'less than a minute';"],
		['another ladder head', "return 'under a minute';"],
		['minutes, spelled out', 'return `${n} minutes`;'],
		['a quoted hr', "const unit = 'hr';"]
	])('refuses %s', (_name, code) => {
		expect(durationsWrittenIn(code).length).toBeGreaterThan(0);
	});

	it.each([
		['the formatter read', 'return `${clock(position)} / ${clock(duration)}`;'],
		[
			'a time of day',
			'return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;'
		],
		['a time of day with a helper', 'return `${two(hours)}:${two(minutes)}`;'],
		['a date built for a file name', 'return `${two(month)}-${two(day)}`;'],
		['a plural', 'return `${noun}s`;'],
		['a count of seconds on a button', 'return `Back ${SKIP_SECONDS} seconds`;'],
		['an attribute named min', '<Meter.Root {value} min={0} {max}>'],
		['a size', 'return `${size} MB`;']
	])('lets %s through', (_name, code) => {
		expect(durationsWrittenIn(code)).toEqual([]);
	});

	it('reports the line each one is on', () => {
		expect(durationsWrittenIn('const a = 1;\nreturn `${n} min`;')).toEqual([
			{ line: 2, what: 'a duration in its own short words' }
		]);
	});
});

describe('the one-duration gate, over the client', () => {
	it('finds no second clock and no second set of duration words', { timeout: 60_000 }, async () => {
		const copies = await everyCopy();
		const strays = Object.entries(copies)
			.filter(([where]) => !(where in NOT_YET_FOLDED))
			.flatMap(([, lines]) => lines);
		expect(
			strays,
			'import `clock`, `lengthClock`, `sayDuration` or `sayLength` from $lib/shell/duration'
		).toEqual([]);
	});

	it('holds the copies not yet folded to their count, and to none once they are folded', async () => {
		const copies = await everyCopy();
		const standing = Object.fromEntries(
			Object.keys(NOT_YET_FOLDED).map((where) => [where, copies[where]?.length ?? 0])
		);
		expect(
			standing,
			'a folded copy comes off NOT_YET_FOLDED; a new one goes to $lib/shell/duration'
		).toEqual(NOT_YET_FOLDED);
	});
});
