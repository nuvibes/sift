/** What the empty-scope gate refuses, driven with lines written for the purpose.
 *
 * `scripts/check_empty_scope.js` holds every `Empty` to saying whether it is a page or a block.
 */

import { describe, expect, it } from 'vitest';

import { unscopedEmptiesIn } from '../../../scripts/lib/empty-scope.js';

const faults = (code: string) => unscopedEmptiesIn(code).map((one: { what: string }) => one.what);

describe('the empty-scope gate', () => {
	it.each([
		['no scope', '<Empty>Nothing here yet.</Empty>', 'no scope'],
		['the quiet flag', '<Empty quiet>No folders yet.</Empty>', 'the quiet flag'],
		[
			'the quiet flag given a value',
			'<Empty quiet={true} icon="inbox">None.</Empty>',
			'the quiet flag'
		],
		[
			'no scope over several lines',
			'<Empty\n\ticon="search"\n\ttitle="No results"\n>x</Empty>',
			'no scope'
		]
	])('refuses %s', (_name, code, what) => {
		expect(faults(code)).toEqual([expect.stringContaining(what)]);
	});

	it.each([
		['a page', '<Empty scope="page" title="No results">Try fewer words.</Empty>'],
		['a block', '<Empty scope="block">No presets saved yet.</Empty>'],
		['a scope from an expression', '<Empty scope={inside ? "block" : "page"}>None.</Empty>'],
		['an arrow in an attribute', '<Empty scope="page" action={() => (quiet = true)}>x</Empty>'],
		['a component whose name begins the same', '<EmptyWall quiet />']
	])('lets %s through', (_name, code) => {
		expect(faults(code)).toEqual([]);
	});
});
