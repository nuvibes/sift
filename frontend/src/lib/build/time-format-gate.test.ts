/** What the one-time-format gate refuses, driven with lines written for the purpose.
 *
 * `scripts/check_one_time_format.js` holds every date on screen to `$lib/shell/when`. The reading of a
 * file is `scripts/lib/time-format.js`, so it can be handed a line and watched refusing it here:
 * a gate that has never failed is indistinguishable from one that cannot. Each shape below is one
 * a screen could write by hand.
 */

import { describe, expect, it } from 'vitest';

import { datesWrittenIn } from '../../../scripts/lib/time-format.js';

const lines = (code: string) => datesWrittenIn(code).map((one: { line: number }) => one.line);

describe('the one-time-format gate', () => {
	it.each([
		['a locale date method', 'const x = new Date(at * 1000).toLocaleDateString();'],
		['a locale time method', "const x = at.toLocaleTimeString([], { hour: 'numeric' });"],
		['a bare toLocaleString off a new Date', 'return new Date(seconds * 1000).toLocaleString();'],
		[
			'toLocaleString with a date option',
			"return when.toLocaleString(undefined, { month: 'short' });"
		],
		['a Date named in the file', 'const when = new Date(at);\nreturn when.toLocaleString();'],
		['a parameter typed as a Date', 'function f(when: Date) {\n\treturn when.toLocaleString();\n}'],
		['an Intl date formatter', 'const f = new Intl.DateTimeFormat(undefined, {});'],
		['an Intl relative formatter', 'const f = new Intl.RelativeTimeFormat();'],
		["a Date's fixed-English string", 'return first.toDateString() === last.toDateString();'],
		['a date built by hand', 'const s = `${when.getFullYear()}-${when.getMonth() + 1}`;'],
		['a second ladder, past', 'if (minutes < 60) return `${minutes}m ago`;'],
		['a second ladder, spelled out', 'return `${hours} hours ago`;'],
		['a second ladder, forward', 'return `starts in ${Math.floor(seconds / 60)}m`;']
	])('refuses %s', (_name, code) => {
		expect(lines(code).length).toBeGreaterThan(0);
	});

	it.each([
		['a count written for people', 'return `${count.toLocaleString()} files`;'],
		['a number with an argument', "return total.toLocaleString('en-US');"],
		['the name of a filter bucket', "older: 'Asked longer ago'"],
		['a count in a sentence', 'return `in ${count} files`;'],
		['the clock read for arithmetic', 'const now = Math.floor(Date.now() / 1000);']
	])('lets %s through', (_name, code) => {
		expect(lines(code)).toEqual([]);
	});

	it('reports the line each one is on, once however many shapes it holds', () => {
		const code =
			'const a = 1;\nreturn new Date(at).toLocaleString(undefined, { dateStyle: "medium" });';
		expect(datesWrittenIn(code)).toEqual([
			{ line: 2, what: 'toLocaleString with a date or time option' }
		]);
	});
});
