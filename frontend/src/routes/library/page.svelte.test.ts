/*
 * Library, the phone's list of every kind of thing.
 *
 * What would be silent if it broke: Recently viewed losing the first row, a row that is not a link
 * to its screen, and a guest offered an admin's row.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

const who = vi.hoisted(() => ({ admin: true }));

vi.mock('$lib/shell/session.svelte', () => ({
	session: {
		get isAdmin() {
			return who.admin;
		}
	}
}));

const Page = (await import('./+page.svelte')).default;

let host: HTMLElement | undefined;
let drawn: ReturnType<typeof mount> | undefined;

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = undefined;
	host?.remove();
});

function open(admin: boolean): HTMLAnchorElement[] {
	who.admin = admin;
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(Page, { target: host });
	flushSync();
	return [...host.querySelectorAll<HTMLAnchorElement>('a.go')];
}

describe('the Library list', () => {
	it('leads with Recently viewed, then every kind of thing, each a link to its screen', () => {
		const rows = open(true);

		expect(
			rows.map((row) => [row.getAttribute('href'), row.querySelector('.name')?.textContent])
		).toEqual([
			['/recent', 'Recently viewed'],
			['/people', 'People'],
			['/sites', 'Sites'],
			['/collections', 'Collections'],
			['/photo-sets', 'Photo Sets'],
			['/tags', 'Tags'],
			['/songs', 'Music'],
			['/loops', 'Loops'],
			['/favorites', 'Favorites'],
			['/organize', 'Organize'],
			['/hidden', 'Hidden'],
			['/insights', 'Insights']
		]);
	});

	it("offers a guest no row whose screen is an admin's", () => {
		const hrefs = open(false).map((row) => row.getAttribute('href'));

		expect(hrefs).not.toContain('/organize');
		expect(hrefs).toContain('/hidden');
	});

	it('is titled with its tab', () => {
		open(true);

		expect(host?.querySelector('h1')?.textContent).toContain('Library');
	});
});
