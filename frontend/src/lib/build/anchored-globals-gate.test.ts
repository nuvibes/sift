/** What the anchored-globals gate refuses.
 *
 * `scripts/check_anchored_globals.js` holds every component's `:global` rules to
 * `scripts/lib/anchored-globals.js`; each case here is two or three components written for the
 * purpose.
 */

import { describe, expect, it } from 'vitest';

import { againstKnown, looseGlobals } from '../../../scripts/lib/anchored-globals.js';
import dataRow from '$lib/components/common/DataRow.svelte?raw';
import dateRange from '$lib/components/common/DateRange.svelte?raw';
import applicationLog from '$lib/settings-ui/ApplicationLog.svelte?raw';

const CALENDAR = (rule: string) => ({
	file: 'lib/Calendar.svelte',
	text: `<div class="date-content"><span class="day">1</span></div>\n<style>\n\t${rule} {\n\t\tpadding: 0;\n\t}\n</style>\n`
});

const LOG = {
	file: 'lib/Log.svelte',
	text: '<div class="day">Today</div>\n<style>\n\t.day {\n\t\tposition: sticky;\n\t}\n</style>\n'
};

describe('a global rule', () => {
	it('is refused when another file writes its class for an element of its own', () => {
		const loose = looseGlobals([CALENDAR(':global(.day)'), LOG]);

		expect(loose).toEqual([
			{
				file: 'lib/Calendar.svelte',
				selector: ':global(.day)',
				first: 'day',
				others: ['lib/Log.svelte']
			}
		]);
	});

	it('is bounded when it starts at a class only this file writes', () => {
		expect(looseGlobals([CALENDAR(':global(.date-content .day)'), LOG])).toEqual([]);
	});

	it('is bounded when it starts at an element of this file and reaches in', () => {
		expect(looseGlobals([CALENDAR('.date-content :global(.day)'), LOG])).toEqual([]);
	});

	it('reads its own closing bracket past a nested one, so a bare rule is still refused', () => {
		const loose = looseGlobals([CALENDAR(':global(.day:not(:disabled))'), LOG]);

		expect(loose.map((one) => [one.first, one.others])).toEqual([['day', ['lib/Log.svelte']]]);
	});

	it('is refused when this file does not write its class at all', () => {
		const loose = looseGlobals([CALENDAR(':global(.row)'), LOG]);

		expect(loose.map((one) => [one.first, one.others])).toEqual([['row', []]]);
	});

	it('may start at a class the design shares on purpose', () => {
		const menu = (file: string) => ({
			file,
			text: `<div class="ui-menu"></div>\n<style>\n\t:global(.ui-menu) {\n\t\tpadding: 0;\n\t}\n</style>\n`
		});

		expect(looseGlobals([menu('lib/One.svelte'), menu('lib/Two.svelte')])).toEqual([]);
	});
});

describe('the list of reaches already standing', () => {
	it('may start at a design-shared class this file never writes: it dresses a page others lay out', () => {
		const heading = {
			file: 'lib/SectionHeading.svelte',
			text: `<h2 class="section-heading"></h2>\n<style>\n\t:global(.section-stack :is(.section-heading)) {\n\t\tborder-top: 1px solid;\n\t}\n</style>\n`
		};
		const pane = {
			file: 'lib/SettingsPane.svelte',
			text: `<div class="section-stack"></div>\n<style>\n\t.section-stack {\n\t\tdisplay: grid;\n\t}\n</style>\n`
		};

		expect(looseGlobals([heading, pane])).toEqual([]);
	});

	it('lets a listed reach stand and refuses one not on it', () => {
		const loose = looseGlobals([CALENDAR(':global(.day)'), LOG]);

		expect(againstKnown(loose, new Map([['lib/Calendar.svelte|day', 'listed']]))).toEqual({
			fresh: [],
			stale: []
		});
		expect(againstKnown(loose, new Map()).fresh).toHaveLength(1);
	});

	it('names a listed reach that no longer occurs, so the list can only shrink', () => {
		const loose = looseGlobals([CALENDAR(':global(.date-content .day)'), LOG]);

		expect(againstKnown(loose, new Map([['lib/Calendar.svelte|day', 'listed']])).stale).toEqual([
			'lib/Calendar.svelte|day'
		]);
	});
});

it("the date picker's calendar dresses no other screen's day or cell", () => {
	const loose = looseGlobals([
		{ file: 'DateRange', text: dateRange },
		{ file: 'ApplicationLog', text: applicationLog },
		{ file: 'DataRow', text: dataRow }
	]);

	expect(loose.filter((one) => one.file === 'DateRange')).toEqual([]);
});
