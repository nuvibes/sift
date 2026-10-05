// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * A FILTERED PEOPLE WALL FOLLOWS THE LIBRARY.
 *
 * The wall draws one of two answers: its page, or, while a name is typed in the box, the search's
 * answer. The library bell and the merge sheet must re-read whichever is drawn, or a wall filtered
 * by a search keeps drawing somebody who has been merged away until the page is reloaded.
 *
 * The stand-in server below holds a roster the test can change, as a merge on another screen would,
 * and the bell is rung the way the live connection rings it.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import Wall from './+page.svelte';
import { goto } from '$app/navigation';
import { libraryChanges } from '$lib/library/changes.svelte';
import { PeopleSearch, people, type Person } from '$lib/people/people.svelte';

const server = vi.hoisted(() => ({
	roster: [] as { id: string; name: string; gender?: string }[]
}));
const at = vi.hoisted(() => ({ url: new URL('http://localhost/people') }));

function row(one: { id: string; name: string }) {
	return {
		id: one.id,
		name: one.name,
		cover_asset_id: null,
		cover_at_ms: null,
		art: 'stamp',
		cover_upload_id: null,
		icon: null,
		site_url: null,
		notes: null,
		asset_count: 1,
		people_count: 0,
		o_count: 0,
		favorite: false,
		pinned: false,
		hidden: false,
		keep_local: false,
		rating: 0,
		record: null,
		shared: false,
		restricted: false
	};
}

vi.mock('$lib/api/client', () => ({
	api: {
		get: vi.fn(async (path: string, options?: { query?: Record<string, unknown> }) => {
			const query = options?.query ?? {};
			if (path !== '/people') return { items: [], total: 0, limit: 50, offset: 0 };
			const typed = String(query.prefix ?? '').toLowerCase();
			const refused = ([] as string[])
				.concat((query.gender as string[] | undefined) ?? [])
				.filter((one) => one.startsWith('-'))
				.map((one) => one.slice(1));
			const found = server.roster.filter(
				(one) => one.name.toLowerCase().includes(typed) && !refused.includes(one.gender ?? '')
			);
			return { items: found.map(row), total: found.length, limit: 50, offset: 0 };
		}),
		post: vi.fn(async () => undefined),
		put: vi.fn(async () => undefined),
		del: vi.fn(async () => undefined)
	},
	isMissing: () => false,
	ApiError: class extends Error {}
}));

vi.mock('$app/navigation', () => ({
	goto: vi.fn(),
	replaceState: vi.fn((url: string) => window.history.replaceState({}, '', url))
}));
vi.mock('$app/state', () => ({
	navigating: { to: null, from: null, type: null, complete: null, delta: null, willUnload: false },
	page: {
		state: {},
		get url() {
			return at.url;
		}
	}
}));

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

beforeEach(() => {
	server.roster = [
		{ id: 'jane', name: 'Jane' },
		{ id: 'janedoe', name: 'Jane Doe' },
		{ id: 'ada', name: 'Ada Lovelace' },
		{ id: 'janek', name: 'Jane Someone', gender: 'MALE' }
	];
	people.forget();
	people.loading = false;
});

afterEach(() => {
	if (mounted) void unmount(mounted);
	mounted = null;
	host?.remove();
	window.history.replaceState({}, '', '/');
});

async function settle() {
	for (let round = 0; round < 8; round += 1) {
		flushSync();
		await Promise.resolve();
	}
	flushSync();
}

const drawn = (id: string) => host.querySelector(`a[href="/people/${id}"]`) !== null;

/** The wall, arriving at this address. */
function wallAt(where: string) {
	window.history.replaceState({}, '', where);
	at.url = new URL(`${window.location.origin}${where}`);
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(Wall, { target: host, props: {} });
}

describe('the words a People wall is searched by', () => {
	it('arrives filtered by the words in its address, with them in the box', async () => {
		/* What the search band's See all opens: the wall, filtered by what was searched for. */
		wallAt('/people?q=jane');
		await settle();

		expect([drawn('jane'), drawn('janedoe'), drawn('ada')]).toEqual([true, true, false]);
		expect((host.querySelector('input') as HTMLInputElement).value).toBe('jane');
	});

	it('writes what is typed into the address, which is what Back and the chip read', async () => {
		wallAt('/people');
		await settle();
		vi.mocked(goto).mockClear();

		const box = host.querySelector('input') as HTMLInputElement;
		box.value = 'jane';
		box.dispatchEvent(new Event('input', { bubbles: true }));
		// The box settles its typing before it writes.
		await new Promise((resolve) => setTimeout(resolve, 250));
		await settle();

		expect(vi.mocked(goto)).toHaveBeenCalledWith('/people?q=jane', {
			replaceState: true,
			keepFocus: true,
			noScroll: true
		});
	});
});

describe('a People wall narrowed by a search', () => {
	it('lists only the people its filter chips allow', async () => {
		wallAt('/people?gender=-MALE&q=jane');
		await settle();

		expect([drawn('jane'), drawn('janedoe'), drawn('janek')]).toEqual([true, true, false]);
	});

	it('drops somebody merged away when the library bell rings', async () => {
		/* The search's words are in the address, which is where the box writes them. */
		wallAt('/people?q=jane');
		await settle();
		expect([drawn('jane'), drawn('janedoe'), drawn('ada')]).toEqual([true, true, false]);

		// Merged on another screen: the server no longer has them, and the bell rings.
		server.roster = server.roster.filter((one) => one.id !== 'jane');
		libraryChanges.changed();
		await settle();

		expect([drawn('jane'), drawn('janedoe')]).toEqual([false, true]);
	});
});

describe('the search a wall holds', () => {
	it('never lets a re-read put back the answer to a term that has since changed', async () => {
		let release: (found: Person[]) => void = () => {};
		const answers: Record<string, Person[]> = {
			jane: [row({ id: 'jane', name: 'Jane' }) as unknown as Person],
			ada: [row({ id: 'ada', name: 'Ada Lovelace' }) as unknown as Person]
		};
		let hold = false;
		const search = new PeopleSearch(async (term) => {
			if (hold) return await new Promise<Person[]>((resolve) => (release = resolve));
			return answers[term];
		});

		await search.run('jane');
		hold = true;
		const reread = search.again();
		hold = false;
		await search.run('ada');
		release(answers.jane);
		await reread;

		expect(search.searched).toBe('ada');
		expect(search.matches?.map((one) => one.id)).toEqual(['ada']);
	});

	it('asks nothing again when nothing is being searched for', async () => {
		const ask = vi.fn(async () => [] as Person[]);
		const search = new PeopleSearch(ask);

		await search.again();

		expect(ask).not.toHaveBeenCalled();
	});
});
