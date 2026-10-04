/*
 * The band of things a word names, above the wall of files that mention it.
 *
 * What is asserted is the shape: a word matching a lot of things must not fill the window and push
 * every file off the bottom. A row is the same chip the player's popout draws, so it is asserted as
 * one: the cover it asks for and the page it leads to.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import EntityBand from './EntityBand.svelte';
import { libraryChanges } from '$lib/library/changes.svelte';

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

const originalFetch = globalThis.fetch;

function json(body: unknown): Response {
	return new Response(JSON.stringify(body), {
		status: 200,
		headers: { 'content-type': 'application/json' }
	});
}

/** `named` rows for one field. Names invented; nothing here is anybody's. */
function people(howMany: number) {
	return Array.from({ length: howMany }, (_, at) => ({
		field: 'people',
		value: `Juniper ${at}`,
		count: at,
		detail: null,
		opens: null,
		id: `p${at}`
	}));
}

function serving(items: unknown[]) {
	globalThis.fetch = vi.fn(async (url: URL | RequestInfo): Promise<Response> => {
		const path = new URL(String(url), 'http://sift.test').pathname;
		if (path.endsWith('/search/named')) return json({ items });
		if (path.endsWith('/assets')) return json({ items: [], total: 0 });
		// What a hovered chip's card asks for. Answered by ADDRESS rather than with one canned
		// object, because the card asks three different routes and a single reply makes two of them
		// look as though they answered when they were merely handed the first one's.
		if (path.includes('/related/')) return json({ files: 4, tags: 2 });
		if (path.includes('/stash-boxes/links/')) return json({ links: [] });
		if (path.startsWith('/api/people/')) return json({ pmv_creator: false });
		return json({});
	}) as unknown as typeof fetch;
}

async function settle(times = 4) {
	for (let round = 0; round < times; round += 1) {
		await new Promise((done) => setTimeout(done, 0));
		flushSync();
	}
}

beforeEach(() => {
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (drawn) void unmount(drawn);
	drawn = null;
	host?.remove();
	globalThis.fetch = originalFetch;
});

describe('the band of things called something', () => {
	it('shows four of a kind and no more, whatever the word matched', async () => {
		serving(people(9));
		drawn = mount(EntityBand, { target: host, props: { word: 'juniper' } });
		await settle();

		expect(host.querySelectorAll('li')).toHaveLength(4);
	});

	it('offers the way to the rest as the same quiet control the bar clears with', async () => {
		serving(people(9));
		drawn = mount(EntityBand, { target: host, props: { word: 'juniper' } });
		await settle();

		const more = [...host.querySelectorAll('button')].find(
			(one) => one.textContent?.trim() === 'See all'
		);
		// `tone="quiet"` is what the filter bar's "Clear all" wears. It is a word that acts, not a
		// thing to aim at beside four buttons.
		expect(more?.className).toContain('quiet');
	});

	it('and does not offer it when there is nothing behind it', async () => {
		serving(people(3));
		drawn = mount(EntityBand, { target: host, props: { word: 'juniper' } });
		await settle();

		const more = [...host.querySelectorAll('button')].find(
			(one) => one.textContent?.trim() === 'See all'
		);
		expect(more).toBeUndefined();
	});

	it('draws each thing as the chip the player draws it as, cover and all', async () => {
		/* A result IS a thing, so it is the same object here as it is under the player: a quiet chip
		   with the thing's own cover in it. The address is `entityPicture`'s rule and is proved where
		   that rule lives; what matters here is that a row asks for it at all. */
		serving(people(2));
		drawn = mount(EntityBand, { target: host, props: { word: 'juniper' } });
		await settle();

		const covers = [...host.querySelectorAll('li img')].map((one) => one.getAttribute('src'));
		expect(covers).toEqual(['/api/people/p0/cover', '/api/people/p1/cover']);
	});

	it("answers a word that is a song's name with the song, leading to its page", async () => {
		serving([
			{ field: 'songs', value: 'Lantern Hum', count: 3, detail: null, opens: null, id: 'g1' }
		]);
		drawn = mount(EntityBand, { target: host, props: { word: 'lantern' } });
		await settle();

		const chip = host.querySelector<HTMLAnchorElement>('li a[href="/songs/g1"]');
		expect(chip?.textContent).toContain('Lantern Hum');
		expect(host.textContent).toContain('Music');
	});

	it('draws a tag the same way', async () => {
		/*
		 * A tag with no cover wears the tinted letter, as the tag chip under the player does: one
		 * tag, drawn one way. The letter is not offered as a photograph.
		 */
		serving([{ field: 'tags', value: 'beach', count: 4, detail: null, opens: null, id: 't1' }]);
		drawn = mount(EntityBand, { target: host, props: { word: 'beach' } });
		await settle();

		expect(host.querySelector('li img')?.getAttribute('src')).toBe('/api/tags/t1/cover');
	});

	it('groups the count on a row the way every other count on screen is grouped', async () => {
		serving([{ field: 'tags', value: 'beach', count: 12345, detail: null, opens: null, id: 't1' }]);
		drawn = mount(EntityBand, { target: host, props: { word: 'beach' } });
		await settle();

		expect(host.querySelector('.count')?.textContent).toBe((12345).toLocaleString());
	});

	it('names the cover in the address, so the browser may keep the picture', async () => {
		/* The band's rows carry what the entity walls' rows carry (the account's token and which
		   cover the thing wears) and the chip's address has to name it. A bare address is the one
		   the server answers the careful way, so without these fields every chip in the band would
		   be re-checked on every search (`kernel/covers.py names_its_cover`). */
		serving([
			{
				field: 'people',
				value: 'Juniper 0',
				count: 1,
				detail: null,
				opens: null,
				id: 'p0',
				art: '7',
				cover_asset_id: 'a1',
				cover_upload_id: null,
				cover_at_ms: 1500,
				icon: null
			}
		]);
		drawn = mount(EntityBand, { target: host, props: { word: 'juniper' } });
		await settle();

		expect(host.querySelector('li img')?.getAttribute('src')).toBe(
			'/api/people/p0/cover?v=7.a1.1500'
		);
	});

	it("names a Site's shipped logo while nobody has chosen it a picture", async () => {
		/* A Site with no chosen cover is answered with the pack's logo, and the server keeps that
		   reply only under an address carrying the logo's own token (`names_the_shipped`). */
		serving([
			{
				field: 'sites',
				value: 'Northlight Raw',
				count: 3,
				detail: null,
				opens: null,
				id: 's1',
				art: '7',
				cover_asset_id: null,
				cover_upload_id: null,
				cover_at_ms: null,
				icon: 'dev-0123456789abcdef'
			}
		]);
		drawn = mount(EntityBand, { target: host, props: { word: 'north' } });
		await settle();

		expect(host.querySelector('li img')?.getAttribute('src')).toBe(
			'/api/sites/s1/cover?v=7.dev-0123456789abcdef'
		);
	});

	it('goes to the thing itself, not to the library narrowed to it', async () => {
		/* Not a button that filters the wall. A chip is a noun with a shape round it,
		   and a noun goes to its page, which is also what gives it middle-click, the address in
		   the status bar, and a back button that means something. */
		serving(people(1));
		drawn = mount(EntityBand, { target: host, props: { word: 'juniper' } });
		await settle();

		expect(host.querySelector('li a')?.getAttribute('href')).toBe('/people/p0');
	});

	it('opens the same card on a hovered chip that the popout player draws', async () => {
		/*
		 * A result is a thing, and under the player a thing's chip opens a `LinkPreview`. These are
		 * the same chips, so hovering one here mounts the same card. The card's contents are proved
		 * in its own suite; this proves the band opens it.
		 */
		serving(people(1));
		drawn = mount(EntityBand, { target: host, props: { word: 'juniper' } });
		await settle();

		const trigger = host.querySelector('li .hovered');
		expect(trigger).not.toBeNull();
		trigger?.dispatchEvent(new MouseEvent('pointerenter', { bubbles: false }));
		await vi.waitFor(() => {
			flushSync();
			if (!document.querySelector('.preview')) throw new Error('no card yet');
		});

		// The card's name is a real link to the same page the chip itself goes to, which is what
		// keeps it an extra rather than the only way through.
		const inside = [...document.querySelectorAll('.preview a')].map((one) =>
			one.getAttribute('href')
		);
		expect(inside).toContain('/people/p0');
	});

	it('opens no card on a folder, which has no page to preview', async () => {
		/* Not an omission. A folder is a filtered view of the library rather than a thing with a
		   record, so there is no cover to draw and no strip of tabs to count. */
		serving([{ field: 'in', value: 'beach trip', count: 4, detail: null, opens: null, id: null }]);
		drawn = mount(EntityBand, { target: host, props: { word: 'beach' } });
		await settle();

		expect(host.querySelector('li .hovered')).toBeNull();
	});

	it('sends a folder to the library narrowed to it, because a folder has no page', async () => {
		/* `in:` is a filter and a folder is what it selects, so the filtered view IS the thing:
		   this is the one section where that address is the right one rather than a fallback. */
		serving([{ field: 'in', value: 'beach trip', count: 4, detail: null, opens: null, id: null }]);
		drawn = mount(EntityBand, { target: host, props: { word: 'beach' } });
		await settle();

		const link = host.querySelector('li a');
		expect(link?.getAttribute('href')).toBe('/browse?q=in%3A%22beach%20trip%22');
		// Nothing to picture: there is no folder to fetch a cover from.
		expect(link?.querySelector('img')).toBeNull();
	});

	it('asks again when Hidden shuts, so the strip cannot draw what the wall withholds', async () => {
		let shut = false;
		const file = { id: 'f1', media_type: 'image', width: 4, height: 3, thumb: true, art: 'a1' };
		const asked: string[] = [];
		globalThis.fetch = vi.fn(async (url: URL | RequestInfo): Promise<Response> => {
			const where = new URL(String(url), 'http://sift.test');
			asked.push(where.pathname);
			if (where.pathname.endsWith('/search/named')) return json({ items: [] });
			const item = shut ? { id: 'f1', media_type: '', concealed: true } : file;
			return json({ items: [item], total: 1 });
		}) as unknown as typeof fetch;
		drawn = mount(EntityBand, { target: host, props: { word: 'harbour' } });
		await settle();
		expect(host.querySelector('.strip img')).not.toBeNull();

		shut = true;
		libraryChanges.changed();
		await settle();

		expect(asked.filter((path) => path.endsWith('/assets'))).toHaveLength(2);
		expect(asked.filter((path) => path.endsWith('/search/named'))).toHaveLength(2);
		expect(host.querySelector('.strip img')).toBeNull();
		expect(host.querySelector('.strip .concealed')).not.toBeNull();
	});
});
