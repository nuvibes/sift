/* The Stats view's panels: a table each, in the board's families, with the address a tile opens. */

import { describe, expect, it } from 'vitest';

import type { InsightsBlock } from '$lib/components/insights/period';

import { answers, familiesOf } from './panels';

function plain(text: string, kind: string | null = null) {
	return { text, kind, id: null, href: null, gone: false, rest: [], lead: '' };
}

type Figure = InsightsBlock['figures'][number];
type List = InsightsBlock['lists'][number];

function figure(label: string, unit: Figure['unit'], over: Partial<Figure> = {}): Figure {
	return {
		label,
		value: 1,
		unit,
		said: '1',
		hidden_part: 0,
		hidden_said: '',
		caption: [],
		defines: [],
		trend: [],
		...over
	};
}

function list(title: string, kind: string | null, count = 1): List {
	return {
		title,
		rows: Array.from({ length: count }, (_, at) => ({
			piece: plain(`${title} ${at + 1}`, kind),
			value: count - at,
			unit: 'ms',
			cover: null,
			said: ''
		}))
	};
}

function bars(count: number, kind = 'bars') {
	return {
		kind,
		unit: 'ms',
		today: null,
		caption: [],
		bars: Array.from({ length: count }, (_, at) => ({
			label: `B${at + 1}`,
			said: '',
			parts: [{ kind: 'video', value: at, said: '' }]
		}))
	} as InsightsBlock['chart'];
}

function block(id: string, fields: Partial<InsightsBlock> = {}): InsightsBlock {
	return {
		id,
		title: id,
		floor_reached: true,
		statements: [],
		figures: [],
		chart: null,
		calendar: null,
		lists: [],
		notes: [],
		...fields
	};
}

const ids = (blocks: InsightsBlock[]) =>
	familiesOf(blocks).map(({ family, panels }) => [family, panels.map((one) => one.id)]);

describe('the Stats panels', () => {
	it("puts each table in its board family, a list of People or Sites in that family's own", () => {
		const blocks = [
			block('arrived', {
				figures: [figure('Imported', 'files')],
				lists: [list('By Site', 'site')]
			}),
			block('most_viewed', {
				lists: [
					list('People', 'person'),
					list('Sites', 'site'),
					list('Files', 'asset'),
					list('Empty', null, 0)
				]
			}),
			block('sittings', { lists: [list('First opened', 'person')] }),
			block('opinions', { figures: [figure('Rated', 'count')] }),
			block('theater', { figures: [figure('In Theater', 'ms')] }),
			block('something_new', { figures: [figure('New', 'count')] }),
			block('recaps', { figures: [figure('Kept', 'count')] }),
			block('alongside', { floor_reached: false, statements: [[plain('Not enough yet to say.')]] })
		];
		expect(ids(blocks)).toEqual([
			['viewing', ['most_viewed-asset', 'most_viewed-list-4', 'something_new-figures']],
			['people', ['most_viewed-person', 'sittings-person']],
			['sites', ['most_viewed-site']],
			['organizing', ['opinions-figures']],
			['theater', ['theater-figures']],
			['downloads', ['arrived-figures', 'arrived-site']],
			['alongside', ['alongside-words']]
		]);
	});

	it('sets a long chart and a calendar across the row, and a chart over its table two rows tall', () => {
		const shapes = (chart: InsightsBlock['chart'], calendar: InsightsBlock['calendar'] = null) =>
			familiesOf([block('overview', { chart, calendar })])[0].panels.map((one) => [
				one.id,
				one.wide,
				one.tall
			]);
		expect(shapes(bars(12))).toEqual([['overview-bars', false, true]]);
		expect(shapes(bars(24))).toEqual([['overview-bars', true, true]]);
		expect(shapes(bars(1, 'share'))).toEqual([['overview-bars', false, false]]);
		const [share] = familiesOf([block('by_kind', { chart: bars(1, 'share') })])[0].panels;
		expect([share.table?.caption, share.table?.head]).toEqual(['Each kind', 'What']);
		expect(
			shapes(null, { unit: 'ms', days: [{ day: '2026-10-01', value: 1, said: '' }] } as never)
		).toEqual([['overview-days', true, true]]);
	});

	it("puts the block's first sentence on its first panel, and what counts on the figures it defines", () => {
		const viewed = figure('Viewed', 'ms', {
			defines: [plain('Time in front of you.')],
			trend: [1, 2]
		});
		const visits = figure('Visits', 'count', { defines: [plain('Each time Sift opened.')] });
		const [overview] = familiesOf([
			block('overview', {
				statements: [[plain('You viewed 3 hours.')], [plain('A second line.')]],
				figures: [viewed, visits],
				chart: bars(2)
			})
		])[0].panels.map((one) => one);
		const [, chart] = familiesOf([
			block('overview', { figures: [viewed, visits], chart: bars(2) })
		])[0].panels;
		expect(overview.sentence?.[0].text).toBe('You viewed 3 hours.');
		expect(overview.defines.map((one) => one.label)).toEqual(['Viewed', 'Visits']);
		expect(chart.sentence).toBeNull();
		expect(chart.defines.map((one) => one.label)).toEqual(['Viewed']);
		/* A trend's bars are named by Overview's own. */
		expect(overview.table?.labels).toEqual(['B1', 'B2']);
	});

	it('keeps the sentences of a block past its floor with no table, and drops one with neither', () => {
		const said = block('when', { statements: [[plain('Mornings, mostly.')]] });
		expect(ids([said, block('machine')])).toEqual([['viewing', ['when-words']]]);
	});

	it('answers an address naming its block or itself, and nothing else', () => {
		const [people] = familiesOf([block('most_viewed', { lists: [list('People', 'person')] })])[0]
			.panels;
		expect(answers(people, 'most_viewed')).toBe(true);
		expect(answers(people, 'most_viewed-person')).toBe(true);
		expect(answers(people, 'most_viewed-site')).toBe(false);
		expect(answers(people, '')).toBe(false);
	});
});
