/*
 * The phone tab bar, which the design gallery deliberately cannot show.
 *
 * It is a singleton the layout renders, fixed to the bottom of the window and hidden above 767px,
 * so drawing a second one on the gallery would put a duplicate bar over the page at exactly the
 * width the gallery is hardest to read at. That is a good reason to keep it off the gallery and no
 * reason at all to leave it unchecked.
 *
 * Four things here would be silent if they broke: which tab is lit (including on a screen reached
 * from the Library list), that every tab is a plain link to a screen of its own, that a guest is
 * not offered an admin's tab, and that the bar announces itself: it is one of two navigations in
 * the app carrying the same name, and whichever is showing is the only one a locator may resolve.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount } from 'svelte';

const mocks = vi.hoisted(() => ({
	pathname: '/browse',
	admin: true
}));

vi.mock('$app/state', () => ({
	page: {
		get url() {
			return new URL(`http://localhost${mocks.pathname}`);
		}
	}
}));

vi.mock('$lib/shell/session.svelte', () => ({
	session: {
		get isAdmin() {
			return mocks.admin;
		}
	}
}));

import MobileTabs from './MobileTabs.svelte';
import { MOBILE_TABS } from './nav';

let host: HTMLElement;

beforeEach(() => {
	vi.clearAllMocks();
	mocks.pathname = '/browse';
	mocks.admin = true;
});

afterEach(() => {
	host?.remove();
	document.body.innerHTML = '';
});

function draw() {
	host = document.createElement('div');
	document.body.append(host);
	mount(MobileTabs, { target: host, props: {} });
	flushSync();
}

function tabs(): HTMLAnchorElement[] {
	return [...host.querySelectorAll<HTMLAnchorElement>('a.tab')];
}

function tab(href: string): HTMLAnchorElement {
	const found = tabs().find((one) => one.getAttribute('href') === href);
	if (!found) throw new Error(`no tab for ${href}`);
	return found;
}

describe('the bar a phone navigates with', () => {
	it('draws the declared tabs, and takes them from the one list', () => {
		/* Not a copy of them. A second list is a second answer to "what is on the phone bar" the day
		   one of them moves. */
		draw();

		expect(tabs().map((one) => one.getAttribute('href'))).toEqual(
			MOBILE_TABS.map((one) => one.href)
		);
	});

	it('is the five places a phone is used from, in their order', () => {
		/* Browse, Library, Remote, Downloads, More. Browse keeps meaning the wall of files, Library
		   the list of every kind, and the Remote drives the desk's screens, one press from anywhere
		   while something plays at the desk. A phone has no Theater: its own full screen holds one
		   video, and a wall is several. */
		draw();

		expect(tabs().map((one) => one.lastElementChild?.textContent)).toEqual([
			'Browse',
			'Library',
			'Remote',
			'Downloads',
			'More'
		]);
	});

	it("offers a guest no tab whose screen is an admin's", () => {
		mocks.admin = false;

		draw();

		/* The Remote stays: a guest watches, and drives their own screens, which are all the server
		   lists them. */
		expect(tabs().map((one) => one.getAttribute('href'))).toEqual([
			'/browse',
			'/library',
			'/remote',
			'/more'
		]);
	});

	it('says which section you are in, to the eye and to a screen reader', () => {
		draw();

		const browse = tab('/browse');
		expect(browse.classList.contains('active')).toBe(true);
		expect(browse.getAttribute('aria-current')).toBe('page');
		expect(tab('/library').getAttribute('aria-current')).toBeNull();
	});

	it('stays lit on a deeper page of the same section', () => {
		/* `/browse` is the section, not the page: a filter in the address must not unlight it. */
		mocks.pathname = '/downloads/d-1';

		draw();

		expect(tab('/downloads').classList.contains('active')).toBe(true);
	});

	it('lights Library on a screen its list leads to', () => {
		/* People is reached from Library on a phone, so standing on a person is standing in Library. */
		mocks.pathname = '/people/p-1';

		draw();

		expect(tab('/library').getAttribute('aria-current')).toBe('page');
		expect(tab('/browse').getAttribute('aria-current')).toBeNull();
	});

	it('lights the Remote on its own screen, and nothing else', () => {
		for (const pathname of ['/remote']) {
			mocks.pathname = pathname;
			draw();

			expect(
				tabs().filter((one) => one.getAttribute('aria-current') === 'page'),
				pathname
			).toEqual([tab('/remote')]);
			host.remove();
		}
	});

	it('lights More while a section of Settings is open over it', () => {
		mocks.pathname = '/settings/appearance';

		draw();

		expect(tab('/more').getAttribute('aria-current')).toBe('page');
	});

	it('leaves every press to the link: More is a screen of its own, not a panel', () => {
		/* A tab that opened Settings itself would put its first section up with nothing behind it,
		   and every way back would lead to that same section. A plain link to /more is a history entry that
		   Back returns to. */
		draw();

		/* Read after every handler on the way up has had it, then stopped, because the test
		   document cannot follow a link. */
		let taken: boolean | undefined;
		const seen = (event: Event) => {
			taken = event.defaultPrevented;
			event.preventDefault();
		};
		document.addEventListener('click', seen);
		tab('/more').dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
		document.removeEventListener('click', seen);

		expect(taken).toBe(false);
		expect(tab('/more').getAttribute('href')).toBe('/more');
	});

	it('announces itself, because two navigations in this app share the name', () => {
		draw();

		expect(host.querySelector('nav')?.getAttribute('aria-label')).toBe('Main');
	});
});
