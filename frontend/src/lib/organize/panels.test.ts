import { describe, expect, it } from 'vitest';

import { chainHref, pileHref } from './addresses';
import { canOpen, detailFor, itemMovedTo, movedTo, panelFor, wayIn } from './panels';

/*
 * Which component draws which queue, and where a card goes when it is pressed.
 *
 * The second of those is what this file is for. `canOpen` (can this build DRAW the queue's panel)
 * answers "where does this card go" only for a queue whose way in is its panel. A report whose
 * way in is somewhere else (the browse wall, say) and that has no panel would come out, decided by
 * `canOpen`, with a disabled heading and a dead body. So the server may declare the way in.
 */

describe('which component draws a queue', () => {
	it('knows the queues this build ships with', () => {
		expect(panelFor('folders')).toBeDefined();
		expect(canOpen('folders')).toBe(true);
	});

	it('draws every tab of the faces page', () => {
		// The faces are one page of five tabs. The first queue is named for the group, so the
		// board's Faces card opens `/organize/faces`.
		expect(panelFor('faces')).toBeDefined();
		expect(panelFor('disagreements')).toBeDefined();
		expect(panelFor('faces-to-name')).toBeDefined();
		expect(panelFor('discarded-faces')).toBeDefined();
		expect(panelFor('known-people')).toBeDefined();
	});

	it('has no drawing for a queue a newer server might send, and says so rather than throwing', () => {
		expect(panelFor('a-queue-from-next-year')).toBeUndefined();
		expect(canOpen('a-queue-from-next-year')).toBe(false);
	});

	it('draws one item only for the queues with something worth opening', () => {
		expect(detailFor('faces-to-name')).toBeDefined();
		expect(detailFor('discarded-faces')).toBeDefined();
		expect(detailFor('known-people')).toBeDefined();
		// And `to-check`, the older address of the one faces list, in the same position as the
		// three below: an address somebody may still be holding.
		expect(detailFor('to-check')).toBeDefined();
		// And the addresses those replaced still DRAW, so a link from a receipt written last month
		// opens rather than flickering through a redirect into an empty screen.
		expect(detailFor('unidentified')).toBeDefined();
		expect(detailFor('identified')).toBeDefined();
		// A folder is answered where it sits, so opening one would be the same question with more
		// space around it.
		expect(detailFor('folders')).toBeUndefined();
	});
});

describe('where a card goes', () => {
	it('opens a queue that has a panel at that panel', () => {
		expect(wayIn({ name: 'folders', opens: null })).toBe('/organize/folders');
	});

	it('opens the filename report at its own page', () => {
		// It has a panel (the filings grouped by the username each was filed under), so the
		// ordinary way in is the answer and the server declares nothing.
		expect(panelFor('filenames')).toBeDefined();
		expect(wayIn({ name: 'filenames', opens: null })).toBe('/organize/filenames');
	});

	it('opens a queue that declares an address THERE, panel or no panel', () => {
		// Nothing ships declaring one today. The capability stays, because a report whose way in is
		// genuinely a wall is a real shape. And a card with no way in at all is the fault this
		// replaced.
		expect(wayIn({ name: 'a-report', opens: '/browse?q=enriched%3Afilename' })).toBe(
			'/browse?q=enriched%3Afilename'
		);
	});

	it('prefers what the server declared over a panel this build happens to have', () => {
		// Nothing declares both today. The day something does, the server saying where its card
		// goes is the answer and an older client's panel is not.
		expect(wayIn({ name: 'folders', opens: '/browse' })).toBe('/browse');
	});

	it('goes nowhere for a queue with neither, which is an older client meeting a newer server', () => {
		expect(wayIn({ name: 'a-queue-from-next-year' })).toBeNull();
		expect(wayIn({ name: 'a-queue-from-next-year', opens: null })).toBeNull();
	});
});

describe('the addresses held here rather than assembled by their callers', () => {
	it('names one chain and one group of faces', () => {
		expect(chainHref('k1')).toBe('/organize/duplicates/k1');
		// One address whatever the group's status is: a group set aside and a group waiting are one
		// list, with set-aside as a filter on it, so there is no second screen to send it to.
		expect(pileHref({ id: 'p1' })).toBe('/organize/faces-to-name/p1');
		expect(pileHref({ id: 'p1', status: 'ignored' })).toBe('/organize/faces-to-name/p1');
	});

	it('carries the tab a group was opened from, and only when there is one', () => {
		/* One screen draws a group whichever tab it was reached through, so the origin has to
		   travel with it: otherwise a group pressed on Ignored opens a trail through Faces to
		   name and cannot get back to Ignored at all. */
		expect(pileHref({ id: 'p1' }, 'discarded-faces')).toBe(
			'/organize/faces-to-name/p1?via=discarded-faces'
		);
		expect(pileHref({ id: 'p1' }, undefined)).toBe('/organize/faces-to-name/p1');
	});
});

describe('a queue that has been renamed', () => {
	/* An address outlives the screen it opened. The four faces queues became two, and the three
	   addresses that went are in links, in bookmarks and in whatever somebody has open in another
	   tab, so they move rather than dying, and this is the one place that says where to. */
	it('sends each old address to the tab that holds what it held', () => {
		// Not all of them to the front of the group: a link into the ignored groups that landed on
		// the suggestions would be a redirect that works and is wrong.
		expect(movedTo('unidentified')).toBe('faces-to-name');
		expect(movedTo('handles')).toBe('usernames');
		expect(movedTo('ignored')).toBe('discarded-faces');
		// The tab's own older address, from before its word became Discard.
		expect(movedTo('ignored-faces')).toBe('discarded-faces');
		expect(movedTo('look-alikes')).toBe('faces');
	});

	it('sends the one list those three briefly became to the page they are tabs of', () => {
		// `to-check` WAS all three, so there is no one tab it meant, and the group's own page
		// is where those tabs are.
		expect(movedTo('to-check')).toBe('faces');
	});

	it('sends the record to the record', () => {
		expect(movedTo('identified')).toBe('known-people');
	});

	it('sends one item of that list to the tab that opens a pile', () => {
		// An item of `to-check` is a pile; the group's own page has no item screen.
		expect(itemMovedTo('to-check')).toBe('faces-to-name');
		// Every other old name: the item goes where its queue goes.
		expect(itemMovedTo('unidentified')).toBe('faces-to-name');
		expect(itemMovedTo('identified')).toBe('known-people');
		expect(itemMovedTo('folders')).toBeNull();
	});

	it('leaves every other queue exactly where it is', () => {
		// The known positive: a queue that never moved must not be redirected to itself or anywhere
		// else, and a name this build has never heard of is not a move either.
		expect(movedTo('folders')).toBeNull();
		expect(movedTo('faces')).toBeNull();
		expect(movedTo(undefined)).toBeNull();
	});
});
