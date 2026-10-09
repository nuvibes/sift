/* The board's tiles from fixture answers of the contracts' shape: which tiles a period is drawn
 * as, in which order, and what card each one carries. */
import { describe, expect, it } from 'vitest';

import type { components } from '$lib/api/schema';
import { skeletonOf, tilesOf, type Tile } from '$lib/components/insights/board/tiles';
import type { InsightsBlock, InsightsPage } from '$lib/components/insights/period';

type Figure = components['schemas']['Figure'];
type NamedRow = components['schemas']['NamedRow'];

function plain(text: string) {
	return { text, kind: null, id: null, href: null, gone: false, rest: [], lead: '' };
}

function figure(label: string, value: number, unit: Figure['unit'] = 'count'): Figure {
	return {
		label,
		value,
		unit,
		said: '',
		hidden_said: '',
		hidden_part: 0,
		caption: [],
		defines: [],
		trend: []
	};
}

function row(name: string, cover: string | null, value = 60_000): NamedRow {
	return { piece: plain(name), value, unit: 'ms', said: '1 min', cover };
}

function block(id: string, over: Partial<InsightsBlock> = {}): InsightsBlock {
	return {
		id,
		title: id,
		floor_reached: true,
		statements: [[plain(`${id} says so.`)]],
		figures: [],
		chart: null,
		calendar: null,
		lists: [],
		notes: [],
		...over
	};
}

const bars = (count: number) => ({
	kind: 'bars' as const,
	unit: 'ms' as const,
	today: null,
	caption: [plain('The busiest day.')],
	bars: Array.from({ length: count }, (_, at) => ({
		label: String(at),
		said: '',
		parts: [{ kind: 'video', value: at * 1000, said: '' }]
	}))
});

function answer(
	period: InsightsPage['period'],
	blocks: InsightsBlock[],
	first = 'The cover line.'
): InsightsPage {
	return {
		period,
		from: '2026-09-07',
		to: '2026-09-13',
		today_is_live: false,
		first_sentences: first ? [[plain(first)]] : [],
		blocks
	};
}

const MOST = block('most_viewed', {
	title: 'Most viewed',
	lists: [
		{
			title: 'People',
			rows: [1, 2, 3, 4, 5, 6].map((n) => row(`Person ${n}`, `/api/people/p${n}/cover`))
		},
		{
			title: 'Sites',
			rows: [row('Site one', '/api/sites/s1/cover'), row('Site two', '/api/sites/s2/cover')]
		},
		{ title: 'Photo Sets', rows: [row('Set one', '/api/photo-sets/a/cover')] },
		{ title: 'Files', rows: [row('harbour.mp4', '/api/assets/f1/thumb', 4)] }
	]
});

const of = (tiles: Tile[], slot: string) => tiles.find((one) => one.slot === slot);

describe("the board's tiles", () => {
	it('stands the same first screen before and after the answer', () => {
		const before = skeletonOf('week').map((one) => [one.slot, one.shape]);
		const after = tilesOf(answer('week', [block('overview')])).slice(0, before.length);
		expect(after.map((one) => [one.slot, one.shape])).toEqual(before);
		expect(skeletonOf('week').every((one) => one.card === null)).toBe(true);
		expect(skeletonOf('day').map((one) => one.slot)).toContain('when');
		expect(skeletonOf('all').map((one) => one.slot)).not.toContain('days');
		const days = (period: 'week' | 'month') =>
			skeletonOf(period).find((one) => one.slot === 'days');
		expect([days('week')?.block, days('month')?.block]).toEqual(['overview-bars', 'overview-days']);
		expect(skeletonOf('day').find((one) => one.slot === 'people')?.block).toBe(
			'most_viewed-person'
		);
	});

	it('fills the first screen from the overview and the lists', () => {
		const overview = block('overview', {
			title: 'Overview',
			figures: [
				figure('Viewed', 3_600_000, 'ms'),
				figure('Sessions', 9),
				figure('Daily average', 60_000, 'ms')
			],
			chart: bars(7)
		});
		const tiles = tilesOf(
			answer('week', [
				overview,
				MOST,
				block('arrived', { figures: [figure('Imported', 142)] }),
				block('sittings', { figures: [figure('Times you opened Sift', 6)] })
			])
		);
		expect(of(tiles, 'headline')?.card?.statement[0].text).toBe('The cover line.');
		expect(of(tiles, 'headline')?.title).toBe('Your week');
		expect(of(tiles, 'viewed')?.title).toBe('Day by day');
		const viewed = of(tiles, 'viewed')?.card;
		expect(viewed?.figure?.label).toBe('Viewed');
		expect(viewed?.chart?.bars).toHaveLength(7);
		expect(viewed?.statement[0].text).toBe('overview says so.');
		expect(of(tiles, 'people')?.card?.rows).toHaveLength(5);
		expect(of(tiles, 'people')?.title).toBe('People');
		const top = of(tiles, 'top_file')?.card;
		expect([top?.cover, top?.figure?.value, top?.figure?.label]).toEqual([
			'/api/assets/f1/thumb',
			4,
			'Most viewed'
		]);
		expect(of(tiles, 'sites')?.card?.cover).toBe('/api/sites/s1/cover');
		const days = of(tiles, 'days')?.card?.calendar;
		expect(days?.days.map((day) => day.day)).toEqual([
			'2026-09-07',
			'2026-09-08',
			'2026-09-09',
			'2026-09-10',
			'2026-09-11',
			'2026-09-12',
			'2026-09-13'
		]);
		expect(days?.days[3].value).toBe(3000);
		expect(of(tiles, 'days')?.title).toBe('Calendar');
		expect(of(tiles, 'imported')?.card?.figure?.value).toBe(142);
		expect(of(tiles, 'visits')?.card?.figure?.value).toBe(6);
		expect(of(tiles, 'sessions')?.card?.figure?.label).toBe('Sessions');
		expect(of(tiles, 'third')?.card?.figure?.label).toBe('Daily average');
		expect(of(tiles, 'photo_sets')?.title).toBe('Photo Sets');
	});

	it("says the chart's caption on the Viewed tile where the headline already says its sentence", () => {
		const overview = block('overview', {
			chart: bars(7),
			statements: [[plain('The cover line.')]]
		});
		expect(of(tilesOf(answer('week', [overview])), 'viewed')?.card?.statement[0].text).toBe(
			'The busiest day.'
		);
		const bare = block('overview', { statements: [[plain('The cover line.')]] });
		expect(of(tilesOf(answer('week', [bare])), 'viewed')?.card?.statement).toEqual([]);
	});

	it("draws a year's days as sent, and a month with no bars without them", () => {
		const calendar = { unit: 'ms' as const, days: [{ day: '2026-01-01', said: '', value: 5 }] };
		const year = tilesOf(answer('year', [block('overview', { calendar })]));
		expect(of(year, 'days')?.card?.calendar).toEqual(calendar);
		expect(of(tilesOf(answer('year', [block('overview')])), 'days')?.card?.calendar).toBeNull();
		const month = tilesOf(answer('month', [block('overview')]));
		expect(of(month, 'days')?.card?.calendar).toBeNull();
		expect(of(month, 'days')?.card?.statement[0].text).toBe('overview says so.');
	});

	it('leads with the server sentence below the floor, and draws a slot with no block empty', () => {
		const tiles = tilesOf(
			answer(
				'day',
				[block('overview', { floor_reached: false }), block('when', { floor_reached: false })],
				''
			)
		);
		expect(of(tiles, 'headline')?.card?.statement[0].text).toBe('overview says so.');
		expect(of(tiles, 'viewed')?.card?.figure).toBeNull();
		expect(of(tiles, 'when')?.card?.statement[0].text).toBe('when says so.');
		expect(of(tiles, 'people')?.card?.statement).toEqual([]);
		expect(of(tiles, 'top_file')?.card?.rows).toEqual([]);
		expect(tilesOf(answer('day', [], ''))[0].card?.statement).toEqual([]);
	});

	it("gives a day's When the 2x1 beside the Sites, and a week's its own 4x2 below", () => {
		const when = block('when', { figures: [figure('Earliest start', 120, 'minute_of_day')] });
		const day = tilesOf(answer('day', [block('overview'), when]));
		expect(day.filter((one) => one.slot === 'when').map((one) => one.shape)).toEqual(['2x1']);
		expect(of(day, 'when')?.card?.figure?.label).toBe('Earliest start');
		expect(of(day, 'when')?.card?.figures).toEqual([]);
		const week = tilesOf(answer('week', [block('overview'), when]));
		expect(week.filter((one) => one.slot === 'when').map((one) => one.shape)).toEqual(['4x2']);
	});

	it("lays the families' tiles out in the board's order, each in its family", () => {
		const tiles = tilesOf(
			answer('week', [
				block('overview'),
				MOST,
				block('by_kind', { title: 'By kind', figures: [figure('Files opened', 3)] }),
				block('when'),
				block('sittings', {
					lists: [{ title: 'First opened', rows: [row('Person 1', '/api/people/p1/cover')] }]
				}),
				block('organizing', { lists: [{ title: 'By card', rows: [row('A card', null)] }] }),
				block('opinions', { statements: [], figures: [figure('Rated', 0)] }),
				block('theater', {
					figures: [figure('In Theater', 60_000, 'ms'), figure('Sessions', 2)],
					lists: [{ title: 'Saved Layouts', rows: [row('A layout', null)] }]
				}),
				block('arrived', {
					lists: [{ title: 'By Site', rows: [row('Site one', '/api/sites/s1/cover')] }]
				}),
				block('machine', { lists: [{ title: 'By task', rows: [row('A task', null)] }] })
			])
		);
		const families = tiles.slice(10).map((one) => [one.slot, one.shape, one.family, one.block]);
		expect(families).toEqual([
			['by_kind', '4x2', 'viewing', 'by_kind'],
			['when', '4x2', 'viewing', 'when'],
			['photo_sets', '2x2', 'viewing', 'most_viewed-photo_set'],
			['first_opened', '2x2', 'people', 'sittings-person'],
			['organizing', '4x2', 'organizing', 'organizing'],
			['theater', '4x2', 'theater', 'theater'],
			['theater_files', '2x2', 'theater', 'theater-wall'],
			['library', '4x2', 'downloads', 'arrived'],
			['machine', '4x2', 'downloads', 'machine']
		]);
		expect(of(tiles, 'first_opened')?.title).toBe('First opened');
		expect(of(tiles, 'theater')?.card?.figure?.label).toBe('In Theater');
		expect(of(tiles, 'theater')?.card?.figures.map((one) => one.label)).toEqual(['Sessions']);
		expect(of(tiles, 'theater_files')?.title).toBe('Saved Layouts');
		expect(of(tiles, 'organizing')?.card?.rows).toHaveLength(1);
		expect(of(tiles, 'library')?.card?.rows).toHaveLength(1);
	});

	it('draws no family tile for a block with nothing to draw, nor an empty list', () => {
		const tiles = tilesOf(
			answer('week', [
				block('overview'),
				block('by_kind', { floor_reached: false }),
				block('sittings', { lists: [{ title: 'First opened', rows: [] }] }),
				block('theater', { lists: [{ title: 'Saved Layouts', rows: [] }] }),
				block('organizing', { statements: [], figures: [figure('Questions answered', 0)] })
			])
		);
		expect(tiles.slice(10).map((one) => one.slot)).toEqual(['theater']);
		expect(of(tiles, 'theater')?.card?.figure).toBeNull();
	});

	it('draws Alongside as a tile a sentence, with its two figures where they were sent', () => {
		const pair = block('alongside', {
			statements: [[plain('One.')], [plain('Two.')]],
			figures: [
				figure('Viewed', 1),
				figure('Starred', 2),
				figure('Viewed', 3),
				figure('Starred', 4)
			]
		});
		const tiles = tilesOf(answer('month', [block('overview'), pair]));
		expect(tiles.slice(10).map((one) => [one.slot, one.shape])).toEqual([
			['alongside_0', '6x2'],
			['alongside_1', '6x2']
		]);
		expect(of(tiles, 'alongside_1')?.card?.figures.map((one) => one.value)).toEqual([3, 4]);
		const odd = tilesOf(
			answer('month', [block('overview'), { ...pair, figures: [figure('Viewed', 1)] }])
		);
		expect(of(odd, 'alongside_0')?.card?.figures).toEqual([]);
	});

	it('says Alongside is below its floor only while the viewing is past its own', () => {
		const below = block('alongside', {
			floor_reached: false,
			statements: [[plain('Not enough yet to say.')]]
		});
		const past = tilesOf(answer('month', [block('overview'), below]));
		expect(of(past, 'alongside')?.card?.statement[0].text).toBe('Not enough yet to say.');
		const quiet = tilesOf(answer('month', [block('overview', { floor_reached: false }), below]));
		expect(of(quiet, 'alongside')).toBeUndefined();
	});
});
