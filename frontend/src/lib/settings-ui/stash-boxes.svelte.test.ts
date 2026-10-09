/* The stash-box store: what it sends, and what it refuses to keep hold of. */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '$lib/api/client';
import { KNOWN_BOXES, StashBoxes } from './stash-boxes.svelte';

const A_BOX = {
	id: 'b1',
	name: 'StashDB',
	endpoint: 'https://stashdb.org/graphql',
	enabled: true,
	has_key: true,
	route: null,
	requests_per_minute: 240
};

beforeEach(() => {
	vi.restoreAllMocks();
});

afterEach(() => {
	vi.restoreAllMocks();
});

it('holds whether a box has a key and never the key itself', async () => {
	vi.spyOn(api, 'get').mockResolvedValue({ boxes: [A_BOX] });
	const boxes = new StashBoxes();

	await boxes.load();

	expect(boxes.items[0].has_key).toBe(true);
	// Every field of everything the store holds, checked against a key that was never sent to it.
	expect(JSON.stringify(boxes.items)).not.toContain('api_key');
});

it('sends a new key once and does not keep it', async () => {
	const put = vi.spyOn(api, 'put').mockResolvedValue(undefined);
	vi.spyOn(api, 'get').mockResolvedValue({ boxes: [A_BOX] });
	const boxes = new StashBoxes();

	await boxes.replaceKey('b1', 'the-real-key');

	expect(put).toHaveBeenCalledWith('/stash-boxes/b1/key', { body: { api_key: 'the-real-key' } });
	expect(JSON.stringify(boxes.items)).not.toContain('the-real-key');
});

it('sends only the field being changed, so mentioning one does not blank the others', async () => {
	const put = vi.spyOn(api, 'put').mockResolvedValue(undefined);
	vi.spyOn(api, 'get').mockResolvedValue({ boxes: [A_BOX] });
	const boxes = new StashBoxes();

	await boxes.setPace('b1', 60);

	expect(put).toHaveBeenCalledWith('/stash-boxes/b1', { body: { requests_per_minute: 60 } });
});

it('reports a failure as a sentence rather than throwing at the screen', async () => {
	// This pane is where somebody comes to find out why nothing works.
	vi.spyOn(api, 'get').mockRejectedValue(new Error('nope'));
	const boxes = new StashBoxes();

	await boxes.load();

	expect(boxes.problem).toBeTruthy();
	expect(boxes.items).toEqual([]);
});

it('hands what was typed over as a parameter rather than pasting it into the address', async () => {
	// A name with an ampersand in it is ordinary, and pasted into an address it ends the parameter
	// and the rest of the name becomes something else.
	const get = vi.spyOn(api, 'get').mockResolvedValue({ answers: [] });
	const boxes = new StashBoxes();

	await boxes.lookUp('Bell & Sons');

	expect(get).toHaveBeenCalledWith('/stash-boxes/look-up', { query: { term: 'Bell & Sons' } });
});

it('offers the three boxes Sift has been checked against, as addresses and nothing more', () => {
	// A preset is a name and an address. There is no code path per box (all three run the same
	// software), so this list is a convenience, never a statement about what is allowed.
	expect(KNOWN_BOXES.map((one) => one.name)).toEqual(['StashDB', 'FansDB', 'PMVStash']);
	for (const one of KNOWN_BOXES) {
		expect(one.endpoint.endsWith('/graphql')).toBe(true);
	}
});
