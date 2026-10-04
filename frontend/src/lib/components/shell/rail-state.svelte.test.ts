/* How the rail is arranged, where that is remembered, and why it is remembered there.
 *
 * Two storages and two reasons, which is the decision in this file worth testing. The arrangement
 * (the order, and what has been put away) belongs to the ACCOUNT and follows somebody between
 * machines; the browser keeps a copy of it so the first paint is right rather than a jump. The
 * width belongs to the BROWSER, because it is a fact about the window in front of somebody.
 *
 * See the module for the reasoning. What is asserted here is that the arrangement really reaches
 * the account, that another account's copy is never shown, that a page load cannot overwrite what
 * the account has, and that none of it can throw when storage refuses.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DEFAULT_RAIL_ORDER, RAIL_DIVIDER } from './nav';

const KEY = 'sift.rail.collapsed';
const ORDER = 'sift.rail.order';
const HIDDEN = 'sift.rail.hidden';
const ACCOUNT = 'sift.rail.account';

/* The account's copy, stubbed. The rail is the only caller of these two routes, so a stub here is
   the whole of what the server does as far as this module is concerned. */
const remote = vi.hoisted(() => ({
	state: {} as Record<string, string>,
	saved: [] as Record<string, string | null>[],
	get: vi.fn(),
	put: vi.fn()
}));

vi.mock('$lib/api/client', () => ({
	api: {
		get: (...args: unknown[]) => {
			remote.get(...args);
			return Promise.resolve({ state: remote.state });
		},
		put: (path: string, options: { body: { state: Record<string, string | null> } }) => {
			remote.put(path, options);
			remote.saved.push(options.body.state);
			return Promise.resolve(undefined);
		}
	}
}));

/** A fresh copy of the module, so it re-reads storage the way a page load does. */
async function load() {
	vi.resetModules();
	return (await import('./rail-state.svelte')).rail;
}

/** A fresh copy, signed in, with the account's arrangement already taken up. */
async function loadFor(account: string) {
	const rail = await load();
	await rail.hydrate(account);
	return rail;
}

beforeEach(() => {
	localStorage.clear();
	remote.state = {};
	remote.saved = [];
	remote.get.mockClear();
	remote.put.mockClear();
});

afterEach(() => {
	vi.restoreAllMocks();
	localStorage.clear();
});

describe('what a browser starts with', () => {
	it('is the full rail, when it has never been told otherwise', async () => {
		// Absent means expanded. A first-time visitor gets labels, which is the state that explains
		// what the icons mean.
		const rail = await load();

		expect(rail.collapsed).toBe(false);
	});

	it('and the narrow one, when it was left narrow', async () => {
		localStorage.setItem(KEY, '1');

		const rail = await load();

		expect(rail.collapsed).toBe(true);
	});

	it('reading anything else in there as "not collapsed" rather than as an error', async () => {
		// Only "1" means collapsed. Anything else is a value this never wrote (another tab, an
		// older version, somebody in the console) and the safe reading of it is the default.
		localStorage.setItem(KEY, 'yes please');

		const rail = await load();

		expect(rail.collapsed).toBe(false);
	});
});

describe('pressing the button', () => {
	it('collapses, and writes that down', async () => {
		const rail = await load();

		rail.toggle();

		expect(rail.collapsed).toBe(true);
		expect(localStorage.getItem(KEY)).toBe('1');
	});

	it('expands again, and clears the key rather than storing a "no"', async () => {
		/* Removed rather than set to "0", so the absent state and the expanded state are one thing.
		 * Two spellings of the same answer is how a third one gets invented. */
		const rail = await load();
		rail.toggle();

		rail.toggle();

		expect(rail.collapsed).toBe(false);
		expect(localStorage.getItem(KEY)).toBeNull();
	});

	it('survives the page being loaded again', async () => {
		// The whole point of storing it at all.
		const rail = await load();
		rail.toggle();

		const reloaded = await load();

		expect(reloaded.collapsed).toBe(true);
	});
});

describe('a browser that will not store anything', () => {
	it('still opens and shuts, and simply forgets', async () => {
		/* Private windows, a full quota, and storage turned off in the browser's settings all throw
		 * from `setItem`. A rail that stopped working because it could not write a preference down
		 * would be trading the whole feature for the memory of it. */
		const rail = await load();
		vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
			throw new Error('quota');
		});

		expect(() => rail.toggle()).not.toThrow();
		expect(rail.collapsed).toBe(true);
	});

	it('and starts expanded when even reading is refused', async () => {
		vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
			throw new Error('blocked');
		});

		const rail = await load();

		expect(rail.collapsed).toBe(false);
	});
});

describe('and whether the labels are on screen at all', () => {
	/* The tooltips exist only while the labels do not, and the labels go for two different reasons:
	 * somebody pressed the button, or the window has no room. Reading only the button would leave
	 * the narrow band with icons that nothing on screen names. */

	/** Stand in for `matchMedia`, answering `matches` for the query the store asks about. */
	function withWindowWidth(narrow: boolean): void {
		vi.stubGlobal('matchMedia', (query: string) => ({
			matches: narrow && query.includes('1023px'),
			media: query,
			addEventListener: () => {},
			removeEventListener: () => {}
		}));
	}

	it('says icons-only when the button was pressed, at any width', async () => {
		withWindowWidth(false);
		const rail = await load();

		expect(rail.iconsOnly).toBe(false);

		rail.toggle();

		expect(rail.iconsOnly).toBe(true);
	});

	it('and when the window has no room, whatever the button last said', async () => {
		withWindowWidth(true);
		const rail = await load();

		// Nobody has pressed anything: this is the window's own answer.
		expect(rail.collapsed).toBe(false);
		expect(rail.iconsOnly).toBe(true);
	});

	it('and follows the window as it is resized', async () => {
		let fire: ((event: { matches: boolean }) => void) | undefined;
		vi.stubGlobal('matchMedia', (query: string) => ({
			matches: false,
			media: query,
			addEventListener: (_name: string, handler: (event: { matches: boolean }) => void) => {
				fire = handler;
			},
			removeEventListener: () => {}
		}));
		const rail = await load();

		expect(rail.iconsOnly).toBe(false);

		fire?.({ matches: true });

		expect(rail.iconsOnly).toBe(true);
	});

	it('and does not throw where there is no matchMedia to ask', async () => {
		// The unit environment has one; a bare Node context would not, and a store that threw on
		// import would take every screen with it.
		vi.stubGlobal('matchMedia', undefined);
		const rail = await load();

		expect(rail.narrow).toBe(false);
		expect(rail.iconsOnly).toBe(false);
	});
});

describe('the arrangement, repaired against the version that is running', () => {
	/* Storage is old data by definition: written by whichever version of Sift this browser last
	 * ran. So what is read back is not checked for validity and thrown away on a surprise; it is
	 * repaired, because throwing it away means somebody's arrangement disappears on an update. */

	it('is what Sift ships with, for a browser that has never been told', async () => {
		const rail = await load();

		expect(rail.order).toEqual(DEFAULT_RAIL_ORDER);
		expect(rail.hidden).toEqual([]);
	});

	it('drops a destination this version no longer has', async () => {
		// A row pointing at a route that is gone is worse than a forgotten arrangement.
		localStorage.setItem(ORDER, `browse,jobs,${RAIL_DIVIDER},settings`);

		const rail = await load();

		expect(rail.order).not.toContain('jobs');
		expect(rail.order).toContain('browse');
	});

	it('puts back a destination this version has and the stored order does not', async () => {
		// An update that adds a place to go must not leave it invisible to everybody who has ever
		// touched their sidebar.
		localStorage.setItem(ORDER, `browse,${RAIL_DIVIDER},settings`);

		const rail = await load();

		for (const id of DEFAULT_RAIL_ORDER) expect(rail.order).toContain(id);
	});

	it('putting it beside what it ships next to, not at the end', async () => {
		/* Appending would file a new way of LOOKING at the library underneath Profile, which is the
		 * bottom of the rail. It goes behind the nearest thing above it that is still there. */
		localStorage.setItem(ORDER, `browse,people,${RAIL_DIVIDER},settings`);

		const rail = await load();

		// Sites ships directly after People, so that is where it lands.
		expect(rail.order.indexOf('sites')).toBe(rail.order.indexOf('people') + 1);
	});

	it('gives a rail somebody arranged the Songs page between Tags and Loops', async () => {
		/* An arrangement written before Songs existed, with Tags dragged to the top and Loops left
		 * below the rule. The arrangement stands, and Songs joins it straight after Tags, which is
		 * the row it ships after: never at the end, and never by moving anything somebody placed. */
		const before = ['tags', 'browse', 'people', RAIL_DIVIDER, 'loops', 'settings'];
		localStorage.setItem(ORDER, before.join(','));

		const rail = await load();

		expect(rail.order.indexOf('songs')).toBe(rail.order.indexOf('tags') + 1);
		expect(rail.order.filter((id) => before.includes(id))).toEqual(before);
	});

	it('keeps a repeated entry once, at the first place it appeared', async () => {
		localStorage.setItem(ORDER, `tags,browse,tags,${RAIL_DIVIDER}`);

		const rail = await load();

		expect(rail.order.filter((id) => id === 'tags')).toHaveLength(1);
		expect(rail.order.indexOf('tags')).toBeLessThan(rail.order.indexOf('browse'));
	});

	it('and puts the rule back when it is missing, rather than drawing a rail without one', async () => {
		localStorage.setItem(ORDER, 'browse,collections,settings');

		const rail = await load();

		expect(rail.order.filter((id) => id === RAIL_DIVIDER)).toHaveLength(1);
	});

	it('and keeps only one of it when there are several', async () => {
		localStorage.setItem(ORDER, `browse,${RAIL_DIVIDER},collections,${RAIL_DIVIDER},settings`);

		const rail = await load();

		expect(rail.order.filter((id) => id === RAIL_DIVIDER)).toHaveLength(1);
	});
});

describe('rearranging', () => {
	it('moves a row and writes the whole order down', async () => {
		const rail = await load();

		rail.nudge('people', -1);

		expect(rail.order.slice(0, 2)).toEqual(['people', 'browse']);
		expect(localStorage.getItem(ORDER)).toBe(rail.order.join(','));
	});

	it('lets a row cross the rule, because the rule is a position and not a wall', async () => {
		/* Any row goes anywhere. What the rule marks by default is the ways of looking at a library
		 * against the places you go to: it is not a boundary to be defended. */
		const rail = await load();
		/* Whichever row sits immediately above the rule, rather than a named one: the default order
		   gains and loses destinations, and a test naming the last of them fails for the wrong
		   reason every time one is added. It is the CROSSING that is under test. */
		const rule = rail.order.indexOf(RAIL_DIVIDER);
		const last = rail.order[rule - 1];
		expect(rail.order.indexOf(last)).toBeLessThan(rule);

		rail.nudge(last, 1);

		expect(rail.order.indexOf(last)).toBeGreaterThan(rail.order.indexOf(RAIL_DIVIDER));
	});

	it('does nothing at the ends', async () => {
		const rail = await load();
		const before = [...rail.order];

		rail.nudge('browse', -1);

		expect(rail.order).toEqual(before);
	});

	it('steps over a row that has been put away rather than swapping with it', async () => {
		/* A row nobody can see is not a position. Pressing "move up" twice to visibly move once is
		 * the control not working. */
		const rail = await load();
		rail.hide('collections');

		rail.nudge('people', -1);

		expect(rail.order.indexOf('people')).toBeLessThan(rail.order.indexOf('browse'));
	});

	it('refuses to move the rule itself', async () => {
		const rail = await load();
		const before = [...rail.order];

		rail.placeBy(RAIL_DIVIDER, 'browse', 'before');
		rail.placeInRegion(RAIL_DIVIDER, 'below');

		expect(rail.order).toEqual(before);
	});

	it('puts a row where it was dropped rather than one place past it', async () => {
		/* Taking a row out shifts everything after it up one, so an index worked out against the
		 * order as it looks BEFORE the move is wrong for every row that started above the target.
		 * Said as a relationship instead, there is no index to be off by. */
		const rail = await load();

		rail.placeBy('browse', 'sites', 'after');

		const order = rail.order;
		expect(order[order.indexOf('sites') + 1]).toBe('browse');
	});

	it('and the same going the other way', async () => {
		const rail = await load();

		rail.placeBy('tags', 'browse', 'before');

		expect(rail.order[0]).toBe('tags');
	});

	it('drops into the top half and stays in the top half', async () => {
		/*
		 * Dropping a row into the empty space above the rule must compute the rule's position after
		 * the row is taken out; computed before, the row lands one place too far down, which is the
		 * far side of the rule, in the other half of the rail.
		 */
		const rail = await load();

		rail.placeInRegion('browse', 'above');

		expect(rail.order.indexOf('browse')).toBeLessThan(rail.order.indexOf(RAIL_DIVIDER));
	});

	it('and into the bottom half and stays there', async () => {
		const rail = await load();

		rail.placeInRegion('browse', 'below');

		expect(rail.order.indexOf('browse')).toBeGreaterThan(rail.order.indexOf(RAIL_DIVIDER));
		expect(rail.order[rail.order.length - 1]).toBe('browse');
	});

	it('does nothing when a row is put back where it came from', async () => {
		const rail = await load();
		const before = [...rail.order];

		rail.placeBy('browse', 'browse', 'after');

		expect(rail.order).toEqual(before);
		expect(localStorage.getItem(ORDER)).toBeNull();
	});

	it('survives the page being loaded again', async () => {
		const rail = await load();
		rail.nudge('tags', -1);
		const arranged = [...rail.order];

		const reloaded = await load();

		expect(reloaded.order).toEqual(arranged);
	});
});

describe('putting a row away', () => {
	it('takes it off the rail and remembers that', async () => {
		const rail = await load();

		rail.hide('tags');

		expect(rail.shows('tags')).toBe(false);
		expect((await load()).shows('tags')).toBe(false);
	});

	it('refuses Settings, which is the way back', async () => {
		/* Hiding it would remove the screen where a hidden row is put back, and leave somebody with a
		 * sidebar they cannot repair from inside the application. */
		const rail = await load();

		rail.hide('settings');

		expect(rail.shows('settings')).toBe(true);
		expect(localStorage.getItem(HIDDEN)).toBeNull();
	});

	it('ignores a Settings that was written into storage by hand', async () => {
		// Storage is not trusted to hold only what the controls could have put there.
		localStorage.setItem(HIDDEN, 'settings,tags');

		const rail = await load();

		expect(rail.shows('settings')).toBe(true);
		expect(rail.shows('tags')).toBe(false);
	});

	it('and putting it back clears the key rather than storing an empty one', async () => {
		const rail = await load();
		rail.hide('tags');

		rail.show('tags');

		expect(rail.shows('tags')).toBe(true);
		expect(localStorage.getItem(HIDDEN)).toBeNull();
	});
});

describe('reset', () => {
	it('puts back both the order and everything that was put away', async () => {
		const rail = await load();
		rail.nudge('tags', -1);
		rail.hide('favorites');

		rail.reset();

		expect(rail.order).toEqual(DEFAULT_RAIL_ORDER);
		expect(rail.hidden).toEqual([]);
		expect(localStorage.getItem(ORDER)).toBeNull();
		expect(localStorage.getItem(HIDDEN)).toBeNull();
	});
});

describe('the arrangement belongs to the account', () => {
	it('takes up what the account has, over what this browser had', async () => {
		/* The point of moving it off the browser. Arranging the rail on one machine and opening Sift
		 * on another has to show the arrangement, not the second machine's blank slate. */
		localStorage.setItem(ACCOUNT, 'ada');
		localStorage.setItem(ORDER, `browse,tags,${RAIL_DIVIDER},settings`);
		remote.state = { 'rail.order': `tags,browse,${RAIL_DIVIDER},settings` };

		const rail = await loadFor('ada');

		expect(rail.order.indexOf('tags')).toBeLessThan(rail.order.indexOf('browse'));
	});

	it('and what was put away comes with it', async () => {
		remote.state = { 'rail.hidden': 'tags' };

		const rail = await loadFor('ada');

		expect(rail.shows('tags')).toBe(false);
	});

	it('writing an arrangement to the account as well as to this browser', async () => {
		const rail = await loadFor('ada');

		rail.nudge('people', -1);

		expect(remote.saved.at(-1)?.['rail.order']).toBe(rail.order.join(','));
		expect(localStorage.getItem(ORDER)).toBe(rail.order.join(','));
	});

	it('and clearing it on the account when it is reset, rather than freezing today', async () => {
		/* Storing the current default written out would keep the account on whatever Sift shipped
		 * the day reset was pressed: a later version that adds a destination or regroups the rail
		 * would never reach anybody who had ever reset. Nothing stored means the shipped one. */
		const rail = await loadFor('ada');
		rail.nudge('tags', -1);

		rail.reset();

		expect(remote.saved.at(-1)).toEqual({ 'rail.order': null, 'rail.hidden': null });
		expect(localStorage.getItem(ORDER)).toBeNull();
	});
});

describe('a machine two people share', () => {
	it('drops a cached arrangement belonging to somebody else at once', async () => {
		/* Before the request, not after it. Waiting for the server would leave one person's rail on
		 * screen while the other person's account was being fetched, and it is their arrangement,
		 * which says which parts of Sift they use. */
		localStorage.setItem(ACCOUNT, 'ada');
		localStorage.setItem(HIDDEN, 'tags');
		localStorage.setItem(ORDER, `tags,browse,${RAIL_DIVIDER},settings`);

		const rail = await load();
		const hydrating = rail.hydrate('grace');

		expect(rail.order).toEqual(DEFAULT_RAIL_ORDER);
		expect(rail.shows('tags')).toBe(true);
		await hydrating;
	});

	it('and keeps the cache when it is the same account, so nothing jumps', async () => {
		localStorage.setItem(ACCOUNT, 'ada');
		localStorage.setItem(ORDER, `tags,browse,${RAIL_DIVIDER},settings`);
		remote.state = { 'rail.order': `tags,browse,${RAIL_DIVIDER},settings` };

		const rail = await load();
		const hydrating = rail.hydrate('ada');

		expect(rail.order.indexOf('tags')).toBeLessThan(rail.order.indexOf('browse'));
		await hydrating;
	});
});

describe('what a page load must not do to the account', () => {
	it('saves nothing before the account has answered', async () => {
		/* A page load draws this browser's cached copy, which is a guess. Saving it would write that
		 * guess over whatever the account really holds, so an arrangement made on the desktop
		 * would be wiped by opening Sift on the laptop, which is the failure the account's copy
		 * exists to prevent. */
		const rail = await load();

		rail.nudge('tags', -1);

		expect(remote.saved).toEqual([]);
	});

	it('and saves it once the account has answered, because that change was somebody real', async () => {
		const rail = await load();
		rail.nudge('tags', -1);

		await rail.hydrate('ada');

		expect(remote.saved).toHaveLength(1);
		expect(remote.saved[0]['rail.order']).toBe(rail.order.join(','));
	});

	it('and the account does not overwrite a change made while it was in flight', async () => {
		remote.state = { 'rail.order': `browse,${RAIL_DIVIDER},settings` };
		const rail = await load();

		rail.hide('tags');
		await rail.hydrate('ada');

		expect(rail.shows('tags')).toBe(false);
	});
});

describe('when the account cannot be reached', () => {
	it('the rail still works, on what this browser remembers', async () => {
		localStorage.setItem(ACCOUNT, 'ada');
		localStorage.setItem(ORDER, `tags,browse,${RAIL_DIVIDER},settings`);
		const rail = await load();
		remote.get.mockImplementationOnce(() => {
			throw new Error('offline');
		});

		await rail.hydrate('ada');

		expect(rail.order.indexOf('tags')).toBeLessThan(rail.order.indexOf('browse'));
	});
});

describe('the width is the browser', () => {
	it('and is never sent to the account', async () => {
		/* The one part deliberately kept in the browser. The same account at a laptop and at a wide
		 * monitor wants two different answers and the account can only hold one. */
		const rail = await loadFor('ada');

		rail.toggle();

		expect(remote.saved).toEqual([]);
		expect(localStorage.getItem(KEY)).toBe('1');
	});
});

describe('a browser holding an older copy than the account', () => {
	/* Recently viewed above Organize, the way a rail stored under an older shipped order looks. */
	const OLDER = DEFAULT_RAIL_ORDER.filter((id) => id !== 'recent');
	OLDER.splice(OLDER.indexOf('organize'), 0, 'recent');

	/** A page load: the shell releases the rail before it knows who is signed in, then hydrates. */
	async function reload(account: string) {
		const rail = await load();
		rail.release();
		await rail.hydrate(account);
		return rail;
	}

	it('never carries that copy up over the rail the account holds as shipped', async () => {
		/* What Sift ships with is stored as nothing, so an empty account is also somebody who put
		 * the rail back. A copy carried up over it brings back what they undid. */
		localStorage.setItem(ACCOUNT, 'ada');
		localStorage.setItem(ORDER, OLDER.join(','));

		const rail = await reload('ada');

		expect(rail.order).toEqual(DEFAULT_RAIL_ORDER);
		expect(rail.order.at(-1)).toBe('recent');
		expect(remote.saved).toEqual([]);
		expect(localStorage.getItem(ORDER)).toBeNull();
	});

	it('keeps Recently viewed where it was put back, across a reload and a restart', async () => {
		remote.state = { 'rail.order': OLDER.join(',') };
		const rail = await reload('ada');
		expect(rail.order.indexOf('recent')).toBeLessThan(rail.order.indexOf('organize'));

		rail.placeInRegion('recent', 'below');
		expect(rail.order).toEqual(DEFAULT_RAIL_ORDER);
		expect(remote.saved.at(-1)).toEqual({ 'rail.order': null, 'rail.hidden': null });

		// The server holds what was saved; another window still has the older copy cached.
		remote.state = {};
		localStorage.setItem(ORDER, OLDER.join(','));
		const again = await reload('ada');
		const restarted = await reload('ada');

		expect(again.order).toEqual(DEFAULT_RAIL_ORDER);
		expect(restarted.order).toEqual(DEFAULT_RAIL_ORDER);
		expect(remote.saved).toHaveLength(1);
	});

	it('reads an arrangement written before Music back unchanged, Music beside Tags', async () => {
		/* Stored by a version without Music. The new row goes beside what it ships next to and
		 * nothing else moves, so Recently viewed stays at the bottom. */
		const beforeMusic = [...DEFAULT_RAIL_ORDER.filter((id) => id !== 'songs')];
		beforeMusic.splice(beforeMusic.indexOf('browse'), 1);
		beforeMusic.splice(beforeMusic.indexOf('settings'), 0, 'browse');
		remote.state = { 'rail.order': beforeMusic.join(',') };

		const rail = await reload('ada');

		expect(rail.order.indexOf('songs')).toBe(rail.order.indexOf('tags') + 1);
		expect(rail.order.filter((id) => id !== 'songs')).toEqual(beforeMusic);
		expect(rail.order.at(-1)).toBe('recent');
		expect(remote.saved).toEqual([]);
	});

	it('keeps whose the cache is through a sign-out, so it is never mistaken for nobody', async () => {
		localStorage.setItem(ACCOUNT, 'ada');
		const rail = await load();

		rail.release();

		expect(localStorage.getItem(ACCOUNT)).toBe('ada');
	});

	it('replaces a copy with no owner written down by the account, and sends none of it', async () => {
		localStorage.setItem(ORDER, `tags,browse,${RAIL_DIVIDER},settings`);
		localStorage.setItem(HIDDEN, 'favorites');

		const rail = await reload('ada');

		expect(rail.order).toEqual(DEFAULT_RAIL_ORDER);
		expect(rail.shows('favorites')).toBe(true);
		expect(remote.saved).toEqual([]);
	});
});

describe('a landing that changes nothing', () => {
	it('is not a move, however it is spelled', async () => {
		/* "After the row above" and "before the row below" are one place with two names, and a
		 * pointer resting on the seam between two rows alternates between them. Treated as two
		 * instructions they would re-run a move that reorders nothing, and the reflow that starts
		 * blocks the next real one, which on screen is a drag that stalls and then jumps. */
		const rail = await load();
		const [first, second] = rail.order;

		// Putting the first row after itself, or before the one it already sits above.
		expect(rail.wouldMove(first, first, 'after')).toBe(false);
		expect(rail.wouldMove(first, second, 'before')).toBe(false);

		// And a landing that genuinely differs still reads as a move.
		expect(rail.wouldMove(first, second, 'after')).toBe(true);
	});
});
