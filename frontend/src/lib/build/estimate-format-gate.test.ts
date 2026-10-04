/** What the one-estimate-format gate refuses, driven with lines written for the purpose.
 *
 * `scripts/check_one_estimate_format.js` holds every estimate on screen to `sayWindow`. Each shape
 * below is one a screen could write by hand.
 */

import { describe, expect, it } from 'vitest';

import { estimatesWrittenIn } from '../../../scripts/lib/estimate-format.js';

const lines = (code: string) => estimatesWrittenIn(code).map((one: { line: number }) => one.line);

describe('the one-estimate-format gate', () => {
	it.each([
		['seconds left', 'return `${Math.round(seconds)}s left`;'],
		['minutes left, abbreviated', 'return `${Math.round(seconds / 60)}m left`;'],
		['minutes left, spelled out', 'return `About ${minutes} minutes left`;'],
		['a literal nought', "return '0 minutes left';"],
		['hours left with a decimal', 'return `${(seconds / 3600).toFixed(1)}h left`;'],
		['a range worded by hand', 'return `about ${quick} to ${slow} ${unit}`;'],
		['a figure worded by hand', 'return `about ${minutes} min`;'],
		['in markup', '<span>{minutes} minutes left</span>']
	])('refuses %s', (_name, code) => {
		expect(lines(code).length).toBeGreaterThan(0);
	});

	it.each([
		['the formatter read', 'return `${sayWindow(seconds, seconds)} left`;'],
		['a count of files left', 'return `${left.toLocaleString()} files left to scan`;'],
		['a size', 'return `about ${roughly(bytes)}`;'],
		['a past duration', 'return `took ${sayDuration(seconds)}`;']
	])('lets %s through', (_name, code) => {
		expect(lines(code)).toEqual([]);
	});
});
