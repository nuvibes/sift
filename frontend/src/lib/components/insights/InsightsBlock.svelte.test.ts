/* One block of Insights, drawn from a fixture answer of the contracts' shape: its figures as cards
 * with their captions, the bars split by kind on one baseline, By kind as one bar of shares, a
 * month's heat-map, its lists ranked with covers, and below its floor, the one line and nothing
 * else. */

import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import { motion } from '$lib/shell/motion.svelte';
import { words } from '$lib/design/testing.svelte';
import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import statementsSource from './Statements.svelte?raw';
import InsightsBlock from './InsightsBlock.svelte';
import type { InsightsBlock as Block } from './period';

type Piece = Block['statements'][number][number];

const HOUR = 3_600_000;

function plain(text: string): Piece {
	return { text, kind: null, id: null, href: null, gone: false, rest: [], lead: '' };
}

function thing(kind: string, id: string, text: string): Piece {
	return { text, kind, id, href: null, gone: false, rest: [], lead: '' };
}

type Figure = Block['figures'][number];
type Chart = NonNullable<Block['chart']>;

/** A figure with no caption under it. */
function figure(label: string, value: number, unit: Figure['unit']): Figure {
	return { label, value, unit, hidden_part: 0, caption: [], said: '', hidden_said: '', trend: [] };
}

/** A chart with no bar still counting and no caption under it. */
type SentBar = { label: string; parts: { kind: string; value: number }[] };

/** A chart as the server sends one. Its words are left empty here, so the screen's own fallback
 *  words them (`wordsOf`): what the server says is `test_arithmetic`'s to pin. */
function chart(kind: Chart['kind'], bars: SentBar[]): Chart {
	return {
		kind,
		unit: 'ms',
		bars: bars.map((bar) => ({
			...bar,
			said: '',
			parts: bar.parts.map((part) => ({ ...part, said: '' }))
		})),
		today: null,
		caption: []
	};
}

/** A block as the server sends one: every field present, the ones a test does not name empty. */
function sentBlock(
	fields: Pick<Block, 'id' | 'title' | 'floor_reached' | 'statements'> & Partial<Block>
): Block {
	return { figures: [], chart: null, calendar: null, lists: [], notes: [], ...fields };
}

/** The Overview of a month, as `GET /api/insights` sends it past its floor. */
function overview(): Block {
	return sentBlock({
		id: 'overview',
		title: 'Overview',
		floor_reached: true,
		statements: [
			[plain('You viewed 41 hours in August: 29 of videos, 9 of pictures, 3 of GIFs.')],
			[plain("That's 12 hours fewer than July.")]
		],
		figures: [
			figure('Viewed', 41 * HOUR, 'ms'),
			figure('Sessions', 212, 'count'),
			figure('Daily average', 80 * 60_000, 'ms')
		],
		chart: chart('bars', [
			{
				label: '1',
				parts: [
					{ kind: 'video', value: 2 * HOUR },
					{ kind: 'image', value: HOUR },
					{ kind: 'gif', value: 0 },
					{ kind: 'theater', value: 0 }
				]
			},
			{
				label: '2',
				parts: [
					{ kind: 'video', value: 0 },
					{ kind: 'image', value: 0 },
					{ kind: 'gif', value: 0 },
					{ kind: 'theater', value: 0 }
				]
			},
			{
				label: '3',
				parts: [
					{ kind: 'video', value: HOUR },
					{ kind: 'image', value: 0 },
					{ kind: 'gif', value: HOUR / 2 },
					{ kind: 'theater', value: 3 * HOUR }
				]
			}
		])
	});
}

function mostViewed(): Block {
	return sentBlock({
		id: 'most_viewed',
		title: 'Most viewed',
		floor_reached: true,
		statements: [
			[
				plain('Your most-viewed person this month was '),
				thing('person', 'p1', 'Neve Alder'),
				plain(': 6 hours, 38 videos and 140 pictures.')
			]
		],
		lists: [
			{
				title: 'People',
				rows: [
					{
						piece: thing('person', 'p1', 'Neve Alder'),
						value: 6 * HOUR,
						unit: 'ms',
						cover: '/api/people/p1/cover',
						said: ''
					}
				]
			},
			{
				title: 'Walls',
				rows: [
					{
						piece: { ...thing('wall', 'w1', 'Nine up'), href: '/theater?wall=w1' },
						value: 2 * HOUR,
						unit: 'ms',
						cover: null,
						said: ''
					}
				]
			}
		]
	});
}

let host: HTMLElement;
let drawn: Record<string, unknown> | undefined;

function draw(block: Block): HTMLElement {
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(InsightsBlock, { target: host, props: { block } });
	flushSync();
	return host;
}

/* The figures are asserted at their values here; how they count up to them is `Figures`' own test. */
beforeEach(() => {
	motion.preference = 'reduce';
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = undefined;
	host?.remove();
	motion.preference = 'full';
});

describe('a block past its floor', () => {
	it('draws its title and its figures as cards, and not the statements that say them again', () => {
		const block = draw(overview());
		expect(words(block.querySelector('h2'))).toBe('Overview');
		expect(block.querySelector('.statements')).toBeNull();
		const figures = [...block.querySelectorAll('.figure')].map((one) => words(one));
		expect(figures).toEqual(['Viewed 1 d 17 h', 'Sessions 212', 'Daily average 1 h 20 min']);
	});

	it("puts the server's one sentence under a figure, and leaves a figure of nothing out", () => {
		const fixture = overview();
		fixture.figures[0] = {
			...fixture.figures[0],
			caption: [plain("That's 12 hours fewer than July.")]
		};
		fixture.figures[1] = { ...fixture.figures[1], value: 0 };
		const block = draw(fixture);
		const cards = [...block.querySelectorAll('.figure')];
		expect(cards.map((one) => words(one.querySelector('.label')))).toEqual([
			'Viewed',
			'Daily average'
		]);
		expect(words(cards[0].querySelector('.caption'))).toBe("That's 12 hours fewer than July.");
		expect(cards[1].querySelector('.caption')).toBeNull();
	});

	it('draws a bar per day on one baseline, split by kind in the fixed order', () => {
		const block = draw(overview());
		const columns = block.querySelectorAll('.plot .column');
		expect(columns).toHaveLength(3);
		const kindsOf = (column: Element) =>
			[...column.querySelectorAll<HTMLElement>('.part')].map((part) => part.dataset.series);
		expect(kindsOf(columns[0])).toEqual(['video', 'image']);
		expect(kindsOf(columns[1])).toEqual([]);
		expect(kindsOf(columns[2])).toEqual(['video', 'gif', 'theater']);
		/* The tallest bar fills the plot; the others are its share. */
		expect((columns[2].querySelector('.stack') as HTMLElement).style.blockSize).toBe('100%');
		expect(
			parseFloat((columns[0].querySelector('.stack') as HTMLElement).style.blockSize)
		).toBeCloseTo(66.67, 1);
		/* The key names each kind drawn, and nothing that was not. */
		expect([...block.querySelectorAll('.key li')].map((li) => words(li))).toEqual([
			'Videos',
			'Pictures',
			'GIFs',
			'Theater'
		]);
	});

	it('says every bar in words in a table, for anybody who cannot see the drawing', () => {
		const block = draw(overview());
		expect(block.querySelector('.plot')?.getAttribute('aria-hidden')).toBe('true');
		const rows = [...block.querySelectorAll('table tbody tr')].map((row) =>
			[...row.children].map((cell) => words(cell))
		);
		expect(rows[0]).toEqual(['1', '2 h', '1 h', '0 min', '0 min']);
		expect(rows[2]).toEqual(['3', '1 h', '0 min', '30 min', '3 h']);
	});

	it('draws By kind as one bar of shares, with each kind in words', () => {
		const block = draw({
			...overview(),
			id: 'by_kind',
			title: 'By kind',
			figures: [],
			chart: chart('share', [
				{
					label: 'Viewed',
					parts: [
						{ kind: 'video', value: 3 * HOUR },
						{ kind: 'image', value: HOUR },
						{ kind: 'gif', value: 0 },
						{ kind: 'theater', value: 0 }
					]
				}
			])
		});
		expect([...block.querySelectorAll('.share-bar .key li')].map((li) => words(li))).toEqual([
			'Videos 75% 3 h',
			'Pictures 25% 1 h'
		]);
	});

	it('draws a month as a heat-map of its days', () => {
		const block = draw({
			...overview(),
			calendar: {
				unit: 'ms',
				days: [
					{ day: '2026-08-01', value: HOUR, said: '1 h' },
					{ day: '2026-08-02', value: 0, said: '0 min' }
				]
			}
		});
		expect(block.querySelectorAll('.heat-map .grid .cell')).toHaveLength(2);
	});

	it('draws each list as a ranked list, the name a link to the thing, its cover where it has one', () => {
		const block = draw(mostViewed());
		const lists = block.querySelectorAll('.ranked-list');
		expect(lists).toHaveLength(2);
		expect(words(lists[0].querySelector('h3'))).toBe('People');
		expect(lists[0].querySelector('a.named')?.getAttribute('href')).toBe('/people/p1');
		expect(lists[0].querySelector('.cover')).not.toBeNull();
		expect(words(lists[0].querySelector('.figure'))).toBe('6 h');
		/* A saved wall has no picture, and draws none. */
		expect(lists[1].querySelector('.cover')).toBeNull();
		expect(lists[1].querySelector('a.named')?.getAttribute('href')).toBe('/theater?wall=w1');
	});

	it("draws a cover's letter, not a broken picture, when the picture fails to load", () => {
		const block = draw(mostViewed());
		const cover = block.querySelector('.ranked-list .cover') as HTMLElement;
		const picture = cover.querySelector('img');
		expect(picture).not.toBeNull();

		picture?.dispatchEvent(new Event('error'));
		flushSync();

		expect(cover.querySelector('img')).toBeNull();
		expect(cover.querySelector('.monogram')).not.toBeNull();
	});

	it('says how much of a figure is hidden only when the server sends a hidden part', () => {
		const block = overview();
		block.figures[0] = { ...block.figures[0], hidden_part: 6 * HOUR };
		const drawnBlock = draw(block);
		const hidden = drawnBlock.querySelectorAll('.figure .aside');
		expect(hidden).toHaveLength(1);
		expect(words(hidden[0])).toBe('6 h');
		expect(hidden[0].querySelector('[role="img"]')?.getAttribute('aria-label')).toBe('Hidden');
	});

	it('draws its notes under it, and its statements where every figure is zero', () => {
		const block = draw({
			...overview(),
			id: 'theater',
			title: 'Theater',
			statements: [[plain('You spent no time in Theater this month.')]],
			figures: [figure('In Theater', 0, 'ms')],
			chart: null,
			notes: [[plain('Sift is still counting some earlier days.')]]
		});
		const said = [...block.querySelectorAll('.statements p')].map((p) => words(p));
		expect(said).toEqual([
			'You spent no time in Theater this month.',
			'Sift is still counting some earlier days.'
		]);
		/* The notes are set quiet, the statements above them in full ink. */
		const [statements, notes] = [...block.querySelectorAll<HTMLElement>('.statements')];
		applyStyles(statementsSource, notes);
		expect(getComputedStyle(notes.querySelector('p') as HTMLElement).color).toBe(
			'var(--sift-ink-2)'
		);
		expect(getComputedStyle(statements.querySelector('p') as HTMLElement).color).toBe(
			'var(--sift-ink)'
		);
		removeStyles();
	});
});

describe('a block below its floor', () => {
	it('draws its heading and the one line, and no figures, chart or lists', () => {
		const block = draw(
			sentBlock({
				id: 'when',
				title: 'When',
				floor_reached: false,
				statements: [[plain('Not enough yet to say.')]]
			})
		);
		expect(words(block.querySelector('h2'))).toBe('When');
		expect(words(block.querySelector('.statements'))).toBe('Not enough yet to say.');
		expect(block.querySelector('.figures, .bar-chart, .ranked-list')).toBeNull();
	});
});
