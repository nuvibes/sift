/* A link dropped ON something: fetch it, and file what comes back under that thing. */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '$lib/api/client';
import { dropOffer, fetchOnto } from '$lib/library/aimed-drop.svelte';
import { toasts } from '$lib/shell/toasts.svelte';

vi.mock('$lib/api/client', () => ({
	api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() }
}));

const mocked = vi.mocked(api);

beforeEach(() => {
	vi.clearAllMocks();
	toasts.clear();
});

describe('a link dropped on something', () => {
	it('queues a download carrying what it was dropped on', async () => {
		mocked.post.mockResolvedValue({});

		await fetchOnto('https://example.test/clip', 'person', 'p1', 'Nadia Vance');

		expect(mocked.post).toHaveBeenCalledWith('/downloads', {
			body: { url: 'https://example.test/clip', aimed_kind: 'person', aimed_id: 'p1' }
		});
	});

	it('names the thing it is being filed under, because that is the only acknowledgement there is', async () => {
		/* The fetch takes as long as it takes and nothing on the card can show that. */
		mocked.post.mockResolvedValue({});

		await fetchOnto('https://example.test/clip', 'collection', 'c1', 'Best of');

		expect(toasts.items).toHaveLength(1);
		expect(toasts.items[0].message).toContain('Best of');
	});

	it('sends no id for Favorites, which is a place rather than a row', async () => {
		/* The server refuses an id with it, and refuses a missing one for every other kind, so
		   the two halves cannot be mixed up silently here or there. */
		mocked.post.mockResolvedValue({});

		await fetchOnto('https://example.test/clip', 'favorite', null, 'Favorites');

		expect(mocked.post).toHaveBeenCalledWith('/downloads', {
			body: { url: 'https://example.test/clip', aimed_kind: 'favorite', aimed_id: null }
		});
	});

	it('says a refused link could not be queued rather than saying it is downloading', async () => {
		mocked.post.mockRejectedValue(new Error('no such person'));

		await fetchOnto('https://example.test/clip', 'person', 'gone', 'Somebody');

		expect(toasts.items).toHaveLength(1);
		expect(toasts.items[0].tone).toBe('error');
	});
});

describe('what the offer promises while something is held over a target', () => {
	it('names what it is going to, where the surface has room for it', () => {
		expect(dropOffer('person', 'Nadia Vance')).toBe('Drop to add \u2014 it goes to Nadia Vance');
	});

	it('says nothing but the act on a card, which is already the name', () => {
		expect(dropOffer('collection')).toBe('Drop to add');
	});

	it('promises a Site drop keeps the one the link came from', () => {
		/* A drop ADDS on every kind, and a Site is the one aim that answers a question the link
		   also answers, so it is the one that reads as "instead". */
		expect(dropOffer('site')).toBe('Drop to add \u2014 its own Site is kept too');
		expect(dropOffer('site', 'QuillMoss')).toBe(
			'Drop to add \u2014 it goes to QuillMoss, and the Site the link came from is kept too'
		);
	});

	it('says it of no other kind, because no other kind is contested', () => {
		for (const kind of ['person', 'tag', 'collection', 'photo_set', 'favorite'] as const) {
			expect(dropOffer(kind, 'Anything'), kind).not.toContain('kept too');
		}
	});
});
