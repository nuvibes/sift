/* Arriving at one row of a queue, named by a fragment in the address. */

import { afterEach, expect, it } from 'vitest';

import { revealAnchored } from './anchor';

afterEach(() => {
	document.body.replaceChildren();
});

function planted(id: string): HTMLElement {
	const row = document.createElement('li');
	row.id = id;
	document.body.append(row);
	return row;
}

it('finds a row whose name holds a colon, which is what a group is named with', () => {
	const row = planted('group-phash:01HZZ');
	const seen: unknown[] = [];
	row.scrollIntoView = (...args: unknown[]) => seen.push(args[0]);

	expect(revealAnchored('group-phash:01HZZ')).toBe(true);
	expect(seen).toEqual([{ block: 'center' }]);
});

it('says so when there is no such row, so the caller keeps asking', () => {
	/* A fragment naming a row that is not on this page is the ordinary case rather than a fault:
	   the group was settled, or the dial moved. */
	planted('copy-somebody-else');

	expect(revealAnchored('copy-asset-a')).toBe(false);
});

it('and does nothing at all with an empty name', () => {
	/* An address with no fragment is every other visit to these screens. */
	expect(revealAnchored('')).toBe(false);
});

it('finds the row even where scrolling is not a thing this environment does', () => {
	/* jsdom reports a missing method as undefined rather than throwing on the call, so a panel
	   test that happens to draw an anchored row must not fail on where it scrolls. */
	const row = planted('copy-asset-a');
	// @ts-expect-error: taking the method away is the whole of the case being covered.
	row.scrollIntoView = undefined;

	expect(revealAnchored('copy-asset-a')).toBe(true);
});
