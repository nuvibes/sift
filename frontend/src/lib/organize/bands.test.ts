/* How the board sorts what registered into bands, and into cards. */

import { describe, expect, it } from 'vitest';

import type { components } from '$lib/api/schema';

import { bandsOf, organizeCrumbs, tabsFor, titleOf } from './bands';

/* The seven fields of the server's own queue that these two rules read, taken FROM it rather
 * than written out again beside it. */
type Row = Pick<
	components['schemas']['QueueView'],
	'name' | 'title' | 'band' | 'pending' | 'group' | 'group_title' | 'count'
>;

function queue(over: Partial<Row> = {}): Row {
	return {
		name: 'one',
		title: 'One',
		band: 'decision',
		pending: true,
		group: null,
		group_title: null,
		count: 3,
		...over
	};
}

describe('what lands in which band', () => {
	it('keeps a record off the board entirely', () => {
		const bands = bandsOf([
			queue({ name: 'waiting' }),
			queue({ name: 'identified', band: 'record', pending: false })
		]);

		expect(bands.flatMap((one) => one.cards.map((card) => card.lead.name))).toEqual(['waiting']);
	});

	it('puts a band this build has never heard of under the last heading', () => {
		/* A newer server can send a band this build does not know. */
		const bands = bandsOf([queue({ name: 'newish', band: 'something-later' })]);

		expect(bands).toHaveLength(1);
		expect(bands[0].heading.name).toBe('log');
	});

	it('draws no heading for a band with nothing in it', () => {
		const bands = bandsOf([queue({ name: 'only', band: 'cleanup' })]);

		expect(bands.map((one) => one.heading.name)).toEqual(['cleanup']);
	});
});

describe('a group is one card', () => {
	it("joins the members, adds their counts, and takes the LEAD's band", () => {
		/* Near duplicates is a judgement and exact copies are housekeeping, so they declare
		   different bands. */
		const bands = bandsOf([
			queue({
				name: 'duplicates',
				title: 'Near Duplicates',
				band: 'decision',
				group: 'dup',
				group_title: 'Duplicates',
				count: 9597
			}),
			queue({
				name: 'copies',
				title: 'Exact Duplicates',
				band: 'cleanup',
				group: 'dup',
				count: 11327
			})
		]);

		expect(bands).toHaveLength(1);
		expect(bands[0].heading.name).toBe('decision');
		const [card] = bands[0].cards;
		expect(card.queues.map((one) => one.name)).toEqual(['duplicates', 'copies']);
		expect(card.count).toBe(20924);
		expect(titleOf(card)).toBe('Duplicates');
	});

	it('leaves a queue standing alone exactly as it was', () => {
		const [band] = bandsOf([queue({ name: 'usernames', title: 'Usernames Waiting', count: 4 })]);

		const [card] = band.cards;
		expect(card.queues).toHaveLength(1);
		expect(card.count).toBe(4);
		expect(titleOf(card)).toBe('Usernames Waiting');
	});

	it("calls a card of one by its queue's own name, even where the queue names a group", () => {
		/* A group whose other members are switched off on this install: the queue still carries
		   the group's name, and a card titled after a group it alone stands for names a page
		   that is not there. */
		const [band] = bandsOf([
			queue({
				name: 'faces',
				title: 'Needs Your Input',
				group: 'faces',
				group_title: 'Faces',
				count: 3
			})
		]);

		const [card] = band.cards;
		expect(card.queues).toHaveLength(1);
		expect(titleOf(card)).toBe('Needs Your Input');
	});

	it("puts a group's records on its card, without counting them", () => {
		/* A card says what the page it opens is made of, and the tab row is that list: records
		   included, or the faces card would name only some of its tabs. */
		const [band] = bandsOf([
			queue({
				name: 'faces',
				title: 'Needs Your Input',
				group: 'faces',
				group_title: 'Faces',
				count: 4568
			}),
			queue({
				name: 'known-people',
				title: 'People Sift can recognize',
				band: 'record',
				pending: false,
				group: 'faces',
				count: 18
			})
		]);

		const [card] = band.cards;
		expect(card.queues.map((one) => one.name)).toEqual(['faces', 'known-people']);
		expect(card.count).toBe(4568);
		expect(titleOf(card)).toBe('Faces');
	});

	it('leaves a record whose group has no card off the board entirely', () => {
		// The known positive beside the rule above: a record rides along on a card, it never makes
		// one.
		expect(
			bandsOf([queue({ name: 'filed', band: 'record', pending: false, group: 'folders' })])
		).toEqual([]);
	});

	it("falls back to the lead's own title when no group name was declared", () => {
		/* A group whose lead names nothing was never meant to be one card. */
		const [band] = bandsOf([
			queue({ name: 'a', title: 'First', group: 'pair', count: 1 }),
			queue({ name: 'b', title: 'Second', group: 'pair', count: 2 })
		]);

		expect(titleOf(band.cards[0])).toBe('First');
		expect(band.cards[0].count).toBe(3);
	});

	it("never joins a record onto its group's card", () => {
		/* The count would then include something that never goes down, on a card whose whole
		   promise is that it reaches zero. */
		const [band] = bandsOf([
			queue({ name: 'unidentified', group: 'faces', group_title: 'Faces', count: 10 }),
			queue({ name: 'identified', band: 'record', pending: false, group: 'faces', count: 900 })
		]);

		expect(band.cards[0].count).toBe(10);
	});
});

describe('the tabs on a queue page', () => {
	it("offers the queues sharing this one's group, records included", () => {
		const queues = [
			queue({ name: 'unidentified', group: 'faces' }),
			queue({ name: 'identified', band: 'record', pending: false, group: 'faces' })
		];

		expect(tabsFor(queues, 'unidentified').map((one) => one.name)).toEqual([
			'unidentified',
			'identified'
		]);
	});

	it('draws nothing for a group of one, which is a heading that looks like a control', () => {
		expect(tabsFor([queue({ name: 'alone', group: 'lonely' })], 'alone')).toEqual([]);
		expect(tabsFor([queue({ name: 'alone', group: null })], 'alone')).toEqual([]);
	});
});

describe('the trail under Organize', () => {
	const QUEUES = [
		{ name: 'unidentified', title: 'Faces Awaiting Review', group: 'faces' },
		{ name: 'duplicates', title: 'Duplicates', group: null }
	];

	it('is the board, then the queue, then the screen', () => {
		expect(organizeCrumbs(QUEUES, 'unidentified', 'A group of faces')).toEqual([
			{ label: 'Organize', href: '/organize' },
			{ label: 'Faces Awaiting Review', href: '/organize/unidentified' },
			{ label: 'A group of faces' }
		]);
	});

	it("does not say the queue's name twice when the screen IS the queue", () => {
		expect(organizeCrumbs(QUEUES, 'duplicates', undefined)).toEqual([
			{ label: 'Organize', href: '/organize' },
			{ label: 'Duplicates' }
		]);
	});

	it('names a fixed screen under the board when it belongs to no queue', () => {
		expect(organizeCrumbs(QUEUES, undefined, 'Recent decisions')).toEqual([
			{ label: 'Organize', href: '/organize' },
			{ label: 'Recent decisions' }
		]);
	});

	/* Until the board has been read the queue crumb is not drawn. */
	it('leaves the queue out of the trail until the board has named it', () => {
		expect(organizeCrumbs([], 'unidentified', 'A group of faces')).toEqual([
			{ label: 'Organize', href: '/organize' },
			{ label: 'A group of faces' }
		]);
	});

	it('draws no trail at all on a queue screen the board has not named yet', () => {
		// One crumb, which `Breadcrumbs` draws as nothing.
		expect(organizeCrumbs([], 'unidentified', undefined)).toEqual([
			{ label: 'Organize', href: '/organize' }
		]);
	});

	/* THE PAGE A TAB IS ON, AND THE TAB A DETAIL WAS OPENED FROM. */
	const TABS = [
		{ name: 'faces', title: 'Needs Your Input', group: 'faces', group_title: 'Faces' },
		{ name: 'discarded-faces', title: 'Discarded', group: 'faces', group_title: null },
		{ name: 'known-people', title: 'People Sift can recognize', group: 'faces', group_title: null }
	];

	it('names the page a tab is on, between the board and the tab', () => {
		expect(organizeCrumbs(TABS, 'discarded-faces', undefined)).toEqual([
			{ label: 'Organize', href: '/organize' },
			{ label: 'Faces', href: '/organize/faces' },
			{ label: 'Discarded' }
		]);
	});

	it('sends a detail back to the tab it was opened from, not to its own queue', () => {
		expect(organizeCrumbs(TABS, 'known-people', 'Marissa Vale', undefined, 'faces')).toEqual([
			{ label: 'Organize', href: '/organize' },
			{ label: 'Faces', href: '/organize/faces' },
			{ label: 'Needs Your Input', href: '/organize/faces' },
			{ label: 'Marissa Vale' }
		]);
	});

	it('refuses an origin that is not a tab of this page', () => {
		// An address is somebody else's to write. A `from` naming any queue at all would draw a
		// trail through a screen this detail has nothing to do with: read, followed, and only then
		// found to be a lie.
		expect(organizeCrumbs(TABS, 'known-people', 'Marissa Vale', undefined, 'duplicates')).toEqual([
			{ label: 'Organize', href: '/organize' },
			{ label: 'Faces', href: '/organize/faces' },
			{ label: 'People Sift can recognize', href: '/organize/known-people' },
			{ label: 'Marissa Vale' }
		]);
	});

	it('still takes a title from the caller where one is handed in', () => {
		// The way the unknown-queue branch of the pile route names itself: no board, no queue, a
		// word of its own.
		expect(organizeCrumbs([], undefined, 'Not found')).toEqual([
			{ label: 'Organize', href: '/organize' },
			{ label: 'Not found' }
		]);
	});
});
