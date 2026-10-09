import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { goto } from '$app/navigation';
import { readFileSync } from 'node:fs';

import {
	cameFrom,
	forgetScreensForTests,
	isFirstScreen,
	leaveFor,
	noteAddress,
	returnTo
} from '$lib/shell/navigation.svelte';

/* What the app remembers about moving between screens: opening a person from the People wall and
 * pressing the way back lands on the page and row the wall was left at, not on page one.
 *
 * It reads the addresses directly rather than SvelteKit's `afterNavigate`, and that is the most
 * important fact about this module. Registering an `afterNavigate` callback in the root layout
 * STOPS SVELTEKIT INTERCEPTING LINKS ALTOGETHER: every navigation becomes a full document load,
 * every module store dies on every click, and the app looks completely normal.
 */

const at = (path: string) => new URL(`${window.location.origin}${path}`);

beforeEach(() => sessionStorage.clear());

describe('whether back is the same place', () => {
	it('knows the screen before this one', () => {
		noteAddress(at('/people?from=abc'));
		noteAddress(at('/people/anna'));

		expect(cameFrom.leadsTo('/people')).toBe(true);
	});

	it('ignores an address that only changed its QUERY', () => {
		/* The walls write which row they are showing into their own address, over and over,
		   while a person is open on top of them. Counted as steps, "where I came from" would be
		   the page I am standing on, and the memory would do nothing at all. */
		noteAddress(at('/people?from=abc'));
		noteAddress(at('/people/anna'));
		noteAddress(at('/people/anna?from=one'));
		noteAddress(at('/people/anna?from=two'));

		expect(cameFrom.leadsTo('/people')).toBe(true);
	});

	it('hands back the address the wall was left at, query and all', () => {
		/* What a crumb navigates to. A blind `history.back()` steps one entry, which on a screen
		   whose tabs are real links is the previous tab and not the wall. */
		noteAddress(at('/organize/identified?from=abc'));
		noteAddress(at('/organize/identified/anna'));
		noteAddress(at('/organize/identified/anna?show=confirmed'));
		noteAddress(at('/organize/identified/anna?show=matched'));

		expect(cameFrom.addressOf('/organize/identified')).toBe('/organize/identified?from=abc');
		expect(cameFrom.addressOf('/people')).toBeNull();
	});

	it('says no when the step before was somewhere else', () => {
		noteAddress(at('/browse'));
		noteAddress(at('/people/anna'));

		expect(cameFrom.leadsTo('/people')).toBe(false);
	});

	it('says no for somebody who opened this page directly', () => {
		/* Nothing behind them. Stepping back would walk them out of Sift, which is what the
		   fixed link is there to prevent. */
		noteAddress(at('/people/anna'));

		expect(cameFrom.leadsTo('/people')).toBe(false);
	});

	it('is not fooled by a path that merely starts the same way', () => {
		noteAddress(at('/people/anna'));
		noteAddress(at('/people/anna/appearances'));

		expect(cameFrom.leadsTo('/people')).toBe(false);
	});

	it('refuses an address from another origin', () => {
		/* Cannot arise from `noteAddress`, and is asserted anyway: what is stored survives a page
		   load, and anything that survives a page load is worth checking before it is acted on. */
		sessionStorage.setItem('sift:came-from', 'https://example.test/people');

		expect(cameFrom.leadsTo('/people')).toBe(false);
	});

	it('says no rather than throwing where the tab has no storage', () => {
		/* A browser set to block site data throws on the accessor itself. A way back that throws is
		   worse than one that forgets. */
		const real = Object.getOwnPropertyDescriptor(window, 'sessionStorage');
		Object.defineProperty(window, 'sessionStorage', {
			configurable: true,
			get() {
				throw new Error('blocked');
			}
		});

		expect(() => noteAddress(at('/people'))).not.toThrow();
		expect(cameFrom.leadsTo('/people')).toBe(false);

		if (real) Object.defineProperty(window, 'sessionStorage', real);
	});
});

/* The browser's own record of this tab, as the Navigation API hands it over: the entries in order
   and which of them is current. jsdom has no such thing, which is also the case the fallback is for. */
function tabWas(paths: string[], current = paths.length - 1) {
	const entries = paths.map((path, index) => ({
		url: path.startsWith('http') ? path : `${window.location.origin}${path}`,
		index
	}));
	vi.stubGlobal('navigation', { currentEntry: entries[current], entries: () => entries });
}

describe('how many steps back the screen before this one is', () => {
	afterEach(() => vi.unstubAllGlobals());

	it('is one step where the wall is the entry just behind this one', () => {
		tabWas(['/people?from=abc', '/people/anna']);

		expect(cameFrom.stepsBackTo('/people')).toBe(1);
	});

	it('counts over the tabs this screen pushed, which a single step would land on', () => {
		/* A person page whose tabs are real links: three entries at one pathname. One step back
		   is the previous TAB; the wall is three steps back, and this says so. */
		tabWas([
			'/people?from=abc',
			'/people/anna',
			'/people/anna?tab=history',
			'/people/anna?tab=faces'
		]);

		expect(cameFrom.stepsBackTo('/people')).toBe(3);
	});

	it('says no when the screen before this one is somewhere else', () => {
		/* The wall is further back, behind another screen: stepping there would walk through the
		   browse screen's entry and land somewhere the crumb does not name as the step before. */
		tabWas(['/people', '/browse', '/people/anna']);

		expect(cameFrom.stepsBackTo('/people')).toBeNull();
	});

	it('says no for the first entry of the tab, where a step back leaves Sift', () => {
		tabWas(['/people/anna']);

		expect(cameFrom.stepsBackTo('/people')).toBeNull();
	});

	it('says no for an address with a query, which names one particular place', () => {
		tabWas(['/browse?in=b', '/asset/x']);

		expect(cameFrom.stepsBackTo('/browse?in=a')).toBeNull();
	});

	it('says no for an entry from another origin', () => {
		tabWas(['https://example.test/people', '/people/anna']);

		expect(cameFrom.stepsBackTo('/people')).toBeNull();
	});

	it('says no where the browser keeps no record at all', () => {
		expect(cameFrom.stepsBackTo('/people')).toBeNull();
	});
});

describe('the one rule for a way back', () => {
	let go: ReturnType<typeof vi.fn>;
	const gone = vi.mocked(goto);

	beforeEach(() => {
		go = vi.fn();
		vi.stubGlobal('history', { go });
		gone.mockClear();
	});

	afterEach(() => vi.unstubAllGlobals());

	function press(over: Partial<MouseEventInit> = {}): MouseEvent {
		const event = new MouseEvent('click', { bubbles: true, cancelable: true, button: 0, ...over });
		returnTo(event, '/people');
		return event;
	}

	it("takes the browser's own step, which brings the scroll back with the page", () => {
		tabWas(['/people?from=abc', '/people/anna', '/people/anna?tab=faces']);

		const event = press();

		expect(go).toHaveBeenCalledExactlyOnceWith(-2);
		expect(gone).not.toHaveBeenCalled();
		expect(event.defaultPrevented).toBe(true);
	});

	it('goes to the remembered address where the browser cannot say where the wall is', () => {
		noteAddress(at('/people?from=abc'));
		noteAddress(at('/people/anna'));

		const event = press();

		expect(go).not.toHaveBeenCalled();
		expect(gone).toHaveBeenCalledExactlyOnceWith('/people?from=abc');
		expect(event.defaultPrevented).toBe(true);
	});

	it('follows the link for a modified or middle press, whatever is behind', () => {
		tabWas(['/people?from=abc', '/people/anna']);

		for (const modifier of ['ctrlKey', 'metaKey', 'shiftKey', 'altKey'] as const) {
			expect(press({ [modifier]: true }).defaultPrevented).toBe(false);
		}
		expect(press({ button: 1 }).defaultPrevented).toBe(false);
		expect(go).not.toHaveBeenCalled();
		expect(gone).not.toHaveBeenCalled();
	});

	it('follows the link where nothing behind is this place', () => {
		tabWas(['/browse', '/people/anna']);

		expect(press().defaultPrevented).toBe(false);
		expect(go).not.toHaveBeenCalled();
		expect(gone).not.toHaveBeenCalled();
	});
});

/* THE EXIT NOBODY PRESSED. A group of faces leaves once it is emptied, an entity page once its
   subject is deleted or hidden, a "new ..." screen on Cancel. A fixed address there is page one
   of a fixed wall, and for a group of faces always Unnamed faces, even from Discarded. */
describe('the one way back for an automatic exit', () => {
	let go: ReturnType<typeof vi.fn>;
	const gone = vi.mocked(goto);

	beforeEach(() => {
		go = vi.fn();
		vi.stubGlobal('history', { go });
		gone.mockClear();
	});

	afterEach(() => vi.unstubAllGlobals());

	it("takes the browser's own step where the wall is the screen before this one", async () => {
		/* People page 2, a person opened, then deleted: back to the same entry. Page 2, and the
		   scroll with it. */
		tabWas(['/people?from=p9&near=24', '/people/anna', '/people/anna?tab=history']);

		await leaveFor('/people');

		expect(go).toHaveBeenCalledExactlyOnceWith(-2);
		expect(gone).not.toHaveBeenCalled();
	});

	it('goes to the remembered address where the browser cannot say where the wall is', async () => {
		noteAddress(at('/collections?from=c4&near=48'));
		noteAddress(at('/collections/new'));

		await leaveFor('/collections');

		expect(go).not.toHaveBeenCalled();
		expect(gone).toHaveBeenCalledExactlyOnceWith('/collections?from=c4&near=48');
	});

	it('goes to the wall itself where nothing behind is that wall', async () => {
		/* Opened directly, or reached from somewhere else: the plain address is the only answer, and
		   it is still an answer. Unlike a click, an exit cannot fall through to a link. */
		tabWas(['/browse', '/people/anna']);

		await leaveFor('/people');

		expect(go).not.toHaveBeenCalled();
		expect(gone).toHaveBeenCalledExactlyOnceWith('/people');
	});

	it('returns a group of faces to the tab it was opened from, not to Unnamed faces', async () => {
		tabWas(['/organize/ignored?from=g2&near=24', '/organize/faces-to-name/g7?via=ignored']);

		await leaveFor('/organize/ignored');

		expect(go).toHaveBeenCalledExactlyOnceWith(-1);
	});
});

/* Every exit, read from the source: one written as `goto(<fixed wall>)` is the fault this module
   exists to prevent, and it would look exactly like working code. */
describe('the screens that leave on their own', () => {
	const screens = [
		'src/lib/components/faces/PileDetail.svelte',
		'src/routes/people/[id]/+page.svelte',
		'src/routes/collections/[id]/+page.svelte',
		'src/routes/tags/[id]/+page.svelte',
		'src/routes/sites/[id]/+page.svelte',
		'src/routes/photo-sets/[id]/+page.svelte',
		'src/routes/people/new/+page.svelte',
		'src/routes/collections/new/+page.svelte',
		'src/routes/tags/new/+page.svelte',
		'src/routes/sites/new/+page.svelte',
		'src/routes/photo-sets/new/+page.svelte'
	];
	const fixedWall =
		/goto\(\s*['`]\/(people|collections|tags|sites|photo-sets|organize\/[a-z-]+)['`]\s*\)/;

	it('leave through `leaveFor`, never to a fixed address', () => {
		const fixed = screens.filter((file) => fixedWall.test(readFileSync(file, 'utf8')));
		const through = screens.filter((file) => readFileSync(file, 'utf8').includes('leaveFor('));

		expect(fixed).toEqual([]);
		expect(through).toEqual(screens);
	});

	it('the group of faces leaves for the tab it was opened from', () => {
		const source = readFileSync('src/lib/components/faces/PileDetail.svelte', 'utf8');

		expect(source).toContain('const home = $derived(`/organize/${opened}`);');
		expect(source.match(/await leaveFor\(home\)/g)).toHaveLength(2);
	});

	it('finds a fixed exit when there is one', () => {
		expect(fixedWall.test("await goto('/people');")).toBe(true);
		expect(fixedWall.test("await goto('/organize/faces-to-name');")).toBe(true);
		expect(fixedWall.test('await goto(`/people/${made.id}`);')).toBe(false);
	});
});

describe('whether an address is the first screen of this page load', () => {
	/* The asset route asks this to decide where closing the panel goes. Asking `afterNavigate`
	   instead would fail on a cold load: the route is drawn too late to hear it, so no cold
	   `/asset/<id>` would open a panel at all. */
	beforeEach(() => forgetScreensForTests());

	it('is true before anything has been noted, and for the address the load opened on', () => {
		expect(isFirstScreen(at('/asset/a1'))).toBe(true);
		noteAddress(at('/asset/a1'));
		expect(isFirstScreen(at('/asset/a1?t=500'))).toBe(true);
	});

	it('is false for an address reached from another screen, in either order', () => {
		noteAddress(at('/browse'));
		expect(isFirstScreen(at('/asset/a1'))).toBe(false);
		noteAddress(at('/asset/a1'));
		expect(isFirstScreen(at('/asset/a1'))).toBe(false);
	});

	it('is false for the first address again after leaving it', () => {
		noteAddress(at('/asset/a1'));
		noteAddress(at('/browse'));
		noteAddress(at('/asset/a1'));
		expect(isFirstScreen(at('/asset/a1'))).toBe(false);
	});
});
