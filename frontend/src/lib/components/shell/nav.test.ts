import { existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import {
	DEFAULT_RAIL_ORDER,
	LIBRARY_ROWS,
	MOBILE_TABS,
	navItem,
	RAIL_DIVIDER,
	RAIL_NAV,
	remoteGoesTo,
	THEATER_DESK_HREF,
	THEATER_ON_A_PHONE,
	theaterTabHref,
	thirdTab
} from './nav';

const ROUTES = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', '..', 'routes');

/** Whether an address has a screen of its own in the client. */
function hasScreen(href: string): boolean {
	return existsSync(join(ROUTES, ...href.split('/').filter(Boolean), '+page.svelte'));
}

/*
 * The two lists that have to agree.
 *
 * `RAIL_NAV` says what a destination IS; `DEFAULT_RAIL_ORDER` says where it sits. They are separate
 * because the second is rearrangeable and the first is not, and the cost of that is that adding a
 * destination to one and not the other fails in silence: the rail is drawn from the stored order,
 * and the repair that puts a missing id back only knows the ids the default order names. Nothing
 * about writing the first list makes anybody think of the second.
 */
describe('the rail lists', () => {
	it('names the same destinations in both lists', () => {
		const declared = RAIL_NAV.map((item) => item.id).sort();
		const arranged = DEFAULT_RAIL_ORDER.filter((id) => id !== RAIL_DIVIDER).sort();
		expect(arranged).toEqual(declared);
	});

	it('places the rule exactly once', () => {
		expect(DEFAULT_RAIL_ORDER.filter((id) => id === RAIL_DIVIDER)).toHaveLength(1);
	});

	it('repeats no destination', () => {
		expect(new Set(DEFAULT_RAIL_ORDER).size).toBe(DEFAULT_RAIL_ORDER.length);
	});

	/* The order the app ships with, written out rather than derived, so changing it is a decision
	   somebody makes here rather than a side effect of editing the list above. The ways of looking
	   above the rule; the places you go to below it. */
	it('ships in the order a library is organised in', () => {
		expect(DEFAULT_RAIL_ORDER).toEqual([
			'browse',
			'people',
			'sites',
			'collections',
			'photo-sets',
			'tags',
			'songs',
			'loops',
			'favorites',
			'theater',
			RAIL_DIVIDER,
			'organize',
			'downloads',
			'hidden',
			'insights',
			'settings',
			'recent'
		]);
	});

	/* The declarations are written in the order they ship in, so the list reads the way the rail
	   does; a destination added to one place in each list would otherwise drift apart unseen. */
	it('declares the destinations in the order they ship in', () => {
		expect(RAIL_NAV.map((item) => item.id)).toEqual(
			DEFAULT_RAIL_ORDER.filter((id) => id !== RAIL_DIVIDER)
		);
	});
});

describe('Organize', () => {
	it('is admin-only, like the download queue beside it', () => {
		expect(navItem('organize')?.admin).toBe(true);
	});

	it('is a page rather than a panel over whatever you were looking at', () => {
		expect(navItem('organize')?.panel).toBeUndefined();
	});
});

describe('the phone tab bar', () => {
	/* A second list, not the one above: it is deliberately not rearrangeable and does not share the
	   rail's stored order. Searching is the box at the top of every screen, so a tab leading to it
	   would be a destination standing in for a control already on screen. */
	it('offers no destination for searching', () => {
		expect(MOBILE_TABS.map((tab) => tab.id)).not.toContain('search');
		expect(MOBILE_TABS.map((tab) => tab.href)).not.toContain('/search');
	});
});

/*
 * Every place the rail goes has a way in on a phone, which has no rail.
 *
 * Unchecked, a destination could be reached on a phone only by typing its address. Each
 * destination says where a phone finds it, and these hold every answer true.
 */
describe('a way in on a phone', () => {
	it('is declared for every destination the rail has', () => {
		for (const item of RAIL_NAV) {
			expect(['tab', 'library', 'more', 'cut'], item.id).toContain(item.phone);
		}
	});

	it('is a tab for exactly the destinations that say so', () => {
		const tabs = MOBILE_TABS.map((tab) => tab.id);
		for (const item of RAIL_NAV) {
			expect(tabs.includes(item.id), item.id).toBe(item.phone === 'tab');
		}
	});

	it('is a row of Library for every destination that says so, and nothing else', () => {
		expect(LIBRARY_ROWS.map((item) => item.id).sort()).toEqual(
			RAIL_NAV.filter((item) => item.phone === 'library')
				.map((item) => item.id)
				.sort()
		);
	});

	it('cuts Theater alone, and puts the Remote in its place', () => {
		/* A phone's own full screen holds one video, so a wall cannot fill it: the phone drives the
		   desk's walls from the Remote instead. */
		expect(THEATER_ON_A_PHONE).toBe(false);
		expect(RAIL_NAV.filter((item) => item.phone === 'cut').map((item) => item.id)).toEqual([
			'theater'
		]);
		expect(MOBILE_TABS.find((tab) => tab.id === 'remote')?.href).toBe('/remote');
	});

	it('puts Recently viewed first on Library, then the rest in the order the rail ships', () => {
		expect(LIBRARY_ROWS.map((item) => item.id)).toEqual([
			'recent',
			'people',
			'sites',
			'collections',
			'photo-sets',
			'tags',
			'songs',
			'loops',
			'favorites',
			'organize',
			'hidden',
			'insights'
		]);
	});

	it('is five tabs at most, each a screen that exists', () => {
		/* A tab to an address with no screen behind it is a door into a wall. */
		expect(MOBILE_TABS.length).toBeLessThanOrEqual(5);
		for (const tab of MOBILE_TABS) expect(hasScreen(tab.href), tab.href).toBe(true);
	});

	it('leads every Library row to a screen that exists', () => {
		for (const item of LIBRARY_ROWS) expect(hasScreen(item.href), item.href).toBe(true);
	});

	it('names Browse and Library as two different screens', () => {
		/* Browse means the wall of files on every device; Library is the list of every kind. */
		expect(MOBILE_TABS.map((tab) => tab.href).slice(0, 2)).toEqual(['/browse', '/library']);
	});

	it('is Browse, Library, Remote, Downloads, More, in that order', () => {
		expect(MOBILE_TABS.map((tab) => tab.id)).toEqual([
			'browse',
			'library',
			'remote',
			'downloads',
			'more'
		]);
		expect(MOBILE_TABS.some((tab) => tab.id === 'theater')).toBe(false);
	});
});

/*
 * The Remote is its own tab and its own screen while Theater has no phone screen, which is today;
 * with one, the third tab would open Theater and the Remote's address would send a phone to
 * Theater's second mode. Both states are held here, so the one switch cannot leave a tab pointing
 * at a refusal or an address pointing nowhere.
 */
describe("the Remote's place", () => {
	it('opens the Remote from the Theater tab until Theater has a phone screen', () => {
		expect(theaterTabHref(false)).toBe('/remote');
		expect(remoteGoesTo(false)).toBeNull();
		expect(hasScreen('/remote')).toBe(true);
	});

	it("opens Theater once it has one, and sends the Remote's address to its second mode", () => {
		expect(theaterTabHref(true)).toBe('/theater');
		expect(remoteGoesTo(true)).toBe(THEATER_DESK_HREF);
		expect(new URL(THEATER_DESK_HREF, 'http://sift').pathname).toBe('/theater');
	});

	it('draws the third tab as the switch says', () => {
		expect(MOBILE_TABS[2]).toEqual(thirdTab(THEATER_ON_A_PHONE));
		expect(thirdTab(false)).toMatchObject({ id: 'remote', href: '/remote', label: 'Remote' });
		const theater = thirdTab(true);
		expect(theater).toMatchObject({ id: 'theater', href: theaterTabHref(true) });
		expect(theater.covers).toEqual(expect.arrayContaining(['/theater', '/remote']));
	});
});
