/*
 * The row a wall is opened at, carried in the address.
 *
 * Seven screens read these four functions, and every guard underneath exists because of a specific
 * way this goes wrong quietly: a browser spec asserting that the address grows a `?from=` and
 * loses it again covers two of the behaviours, on one of the seven:
 *
 * - Writing the anchor from a screen that is not the one on top any more navigates somebody out of
 *   whatever they just opened.
 * - Writing it as a real navigation makes Back walk through every page anybody scrolled past.
 * - Rebuilding an absolute address hands the router an origin the browser may not be on, which it
 *   refuses outright as external, and Sift is reached through proxies and tunnels.
 * - Sending an anchor and an offset together asks two questions, and which one wins would then be
 *   a detail of the server rather than a decision.
 *
 * None of those shows up as an error. Each shows up as a screen that jumps, or a Back button that
 * stops working, or a link that opens somewhere else.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { replaceState } from '$app/navigation';
import { navigating, page } from '$app/state';

import { cameFrom, noteAddress } from '$lib/shell/navigation.svelte';

import { ANCHOR, NEAR, anchorIn, asked, forgetAnchor, rememberAnchor } from './anchor';

vi.mock('$app/navigation', () => ({ replaceState: vi.fn() }));
vi.mock('$app/state', () => ({
	navigating: { to: null, from: null, type: null },
	// What a panel over this screen is drawn from. It has to survive an address correction.
	page: { state: {} }
}));

const went = vi.mocked(replaceState);
const going = navigating as { to: unknown };
const showing = page as unknown as { state: Record<string, unknown> };

/** Somebody is on their way to another screen and has not arrived. */
function partway(): void {
	going.to = { url: new URL('http://sift.test/organize'), route: { id: '/organize' } };
}

/** An address, as the app sees it: a real URL with an origin on it. */
function at(path: string): URL {
	return new URL(path, 'http://sift.test');
}

beforeEach(() => {
	went.mockClear();
	going.to = null;
	showing.state = {};
});

describe('reading the anchor out of the address', () => {
	it('is the row named there, and where it was', () => {
		expect(anchorIn(at('/people?from=p7&near=48'))).toEqual({ from: 'p7', near: 48 });
	});

	it('is the row alone for an address written before `near` existed', () => {
		expect(anchorIn(at('/people?from=p7'))).toEqual({ from: 'p7', near: null });
	});

	it('is nothing when the address names none', () => {
		expect(anchorIn(at('/people?prefix=b'))).toBeNull();
	});

	it('is nothing for a `near` with no row, which would be a page number by another name', () => {
		expect(anchorIn(at('/people?near=48'))).toBeNull();
	});

	it('refuses a `near` that is not a whole number from zero up', () => {
		/* The address is somebody else's to write. */
		for (const bad of ['-1', '1.5', 'abc', '', '1e3', '9999999999']) {
			expect(anchorIn(at(`/people?from=p7&near=${bad}`))).toEqual({ from: 'p7', near: null });
		}
	});

	it('uses one name on every screen', () => {
		expect(ANCHOR).toBe('from');
		expect(NEAR).toBe('near');
	});
});

describe('writing where the page landed', () => {
	it('puts the row in the address, keeping everything else that was asked', () => {
		rememberAnchor(at('/people?prefix=b&sort=rating'), '/people', 'p7', 48);

		expect(went).toHaveBeenCalledTimes(1);
		const [where] = went.mock.calls[0];
		const written = new URL(String(where), 'http://sift.test');
		expect(written.searchParams.get('from')).toBe('p7');
		expect(written.searchParams.get('near')).toBe('48');
		expect(written.searchParams.get('prefix')).toBe('b');
		expect(written.searchParams.get('sort')).toBe('rating');
	});

	it('goes as a path, never as an absolute address', () => {
		/* An origin the router does not recognise is an external navigation and is refused. Sift is
		   reached through proxies and tunnels, so rebuilding the address is a real way to produce
		   one, not only a thing that happens in a test. */
		rememberAnchor(at('/people'), '/people', 'p7', 0);

		expect(String(went.mock.calls[0][0])).toBe('/people?from=p7&near=0');
	});

	it('carries the page state, so a panel opened over the wall is not thrown away', () => {
		/*
		 * THE FOURTH EDGE: a correction that lands LATER.
		 *
		 * A `goto` lands some frames after it is asked for and drops the page state on the way. A
		 * wall's page settling and somebody opening Settings in the same breath is enough: the
		 * panel goes up, the correction lands, the state a panel is drawn from is gone and so is
		 * the panel: the address going from `/settings/library` back to `/browse?from=...` with
		 * nothing having been pressed.
		 *
		 * Writing it as a `replaceState` closes the window and keeps the state; this holds the
		 * second half, which is the half a mock of the first would let through.
		 */
		showing.state = { settings: 'library' };

		rememberAnchor(at('/people'), '/people', 'p7', 0);

		expect(went.mock.calls[0][1]).toEqual({ settings: 'library' });
	});

	it('says nothing when the screen it belongs to is no longer the one on top', () => {
		/* Opening a file pushes an address over a wall that stays mounted underneath, and its
		   background refresh keeps running. Without this the refresh navigates the person out of the
		   file they just opened. */
		rememberAnchor(at('/assets/a1'), '/people', 'p7', 0);

		expect(went).not.toHaveBeenCalled();
	});

	it('says nothing for a row with no id', () => {
		// The Identified wall gathers everybody a viewer may not be told about under one nameless
		// card. A card that exists in order to have no name is not one an address should name.
		rememberAnchor(at('/organize/identified'), '/organize/identified', null, 0);
		rememberAnchor(at('/organize/identified'), '/organize/identified', undefined, 0);
		rememberAnchor(at('/organize/identified'), '/organize/identified', '', 0);

		expect(went).not.toHaveBeenCalled();
	});

	it('says nothing when the address already names that row', () => {
		// Most background refreshes land here, and a `goto` to where you already are still costs a
		// navigation.
		rememberAnchor(at('/people?from=p7&near=24'), '/people', 'p7', 24);

		expect(went).not.toHaveBeenCalled();
	});

	it('replaces the anchor when the page has moved on', () => {
		rememberAnchor(at('/people?from=p7&near=24'), '/people', 'p9', 48);

		expect(String(went.mock.calls[0][0])).toBe('/people?from=p9&near=48');
	});

	it('moves `near` with the row even when only the offset changed', () => {
		/* The same first row at a new offset: something above it was removed. The row is the fact,
		   and where it now sits is what the way back needs if it goes too. */
		rememberAnchor(at('/people?from=p7&near=24'), '/people', 'p7', 23);

		expect(String(went.mock.calls[0][0])).toBe('/people?from=p7&near=23');
	});

	it('says nothing while somebody is already on their way somewhere else', () => {
		/* The path check reads where we are NOW, and during a navigation that is still the screen
		   being left, so it passes, and the write lands on top of the move. Pressing a sidebar
		   link while a wall's first page arrives would change the screen and pull it straight back. */
		partway();

		rememberAnchor(at('/people'), '/people', 'p7', 0);

		expect(went).not.toHaveBeenCalled();
	});

	it('tells what remembers the address, because a replaceState is invisible to it', () => {
		/*
		 * THE FIFTH EDGE: leaving a wall at `/people?from=p16` must remember that, not plain
		 * `/people`, or the crumb comes back to the front.
		 *
		 * `replaceState` never assigns `page.url` (it sets the history entry and `page.state` and
		 * nothing else), so an effect watching `page.url` cannot see this write at all. The wall
		 * says so itself. Asserted through `cameFrom` rather than through the stored key, because
		 * what the crumb reads is the promise.
		 */
		sessionStorage.clear();
		const wall = `${window.location.origin}/people`;
		noteAddress(new URL(wall)); // arrived at the wall
		rememberAnchor(new URL(wall), '/people', 'p7', 24); // turned a page
		noteAddress(new URL(`${wall}/p7`)); // opened somebody on it

		expect(cameFrom.addressOf('/people')).toBe('/people?from=p7&near=24');
	});
});

describe('forgetting it', () => {
	it('takes the anchor out and leaves the rest of the question alone', () => {
		forgetAnchor(at('/people?prefix=b&from=p7&near=24'), '/people');

		expect(String(went.mock.calls[0][0])).toBe('/people?prefix=b');
	});

	it('says nothing when there is no anchor to take out', () => {
		// Otherwise every reset would cost a navigation, and there is one on every new question.
		forgetAnchor(at('/people?prefix=b'), '/people');

		expect(went).not.toHaveBeenCalled();
	});

	it('says nothing from a screen that is no longer the one on top', () => {
		forgetAnchor(at('/assets/a1?from=p7'), '/people');

		expect(went).not.toHaveBeenCalled();
	});

	it('says nothing while somebody is already on their way somewhere else', () => {
		partway();

		forgetAnchor(at('/people?from=p7'), '/people');

		expect(went).not.toHaveBeenCalled();
	});
});

describe('what a wall asks the server for', () => {
	it('is a place in the list, when there is no anchor', () => {
		expect(asked({ limit: 60, offset: 120 })).toEqual({ limit: '60', offset: '120' });
	});

	it('is a row to start AT, when there is one', () => {
		expect(asked({ limit: 60, from: 'p7' })).toEqual({ limit: '60', from: 'p7' });
	});

	it('carries where that row was, for the server to use if the row is gone', () => {
		expect(asked({ limit: 60, from: 'p7', near: 48 })).toEqual({
			limit: '60',
			from: 'p7',
			near: '48'
		});
		expect(asked({ limit: 60, from: 'p7', near: null })).toEqual({ limit: '60', from: 'p7' });
	});

	it('is never both together', () => {
		/* A request carrying an anchor and an offset asks two questions, and which of them the
		   server honours would then be a detail of the server rather than a decision. */
		expect(asked({ limit: 60, from: 'p7' })).not.toHaveProperty('offset');
		expect(asked({ limit: 60, offset: 120 })).not.toHaveProperty('from');
	});
});

/*
 * WHAT BACK RETURNS TO, which is not the address in the bar.
 *
 * The router keeps each history entry's own record of the address it is for, and on a `popstate` it
 * navigates to THAT rather than to what the bar says. `replaceState` writes `page.url` into it
 * (the address the screen arrived at) and never moves `page.url`, so every correction a wall makes
 * stamps the entry with the arrival address again: on /people, two pages forward, the bar reads
 * `?from=...` and the entry's record still reads `/people`. Back would go to the bare address, the
 * wall would draw page one, and the anchor it then wrote would make this look like the last
 * correction being lost.
 */
describe("the history entry's own record of the address it is for", () => {
	const ROUTERS_RECORD = 'sveltekit:pageurl';

	beforeEach(() => {
		history.replaceState(
			{ 'sveltekit:history': 1, [ROUTERS_RECORD]: `${window.location.origin}/people` },
			'',
			'/people'
		);
		// The router's own write, as it behaves: the bar moves and the record does not.
		went.mockImplementation((where) => {
			history.replaceState(history.state, '', String(where));
		});
	});

	afterEach(() => {
		went.mockReset();
	});

	it('is corrected to the address that was just written', () => {
		rememberAnchor(new URL('/people', window.location.origin), '/people', 'p7', 0);

		expect(window.location.pathname + window.location.search).toBe('/people?from=p7&near=0');
		expect(history.state[ROUTERS_RECORD]).toBe(`${window.location.origin}/people?from=p7&near=0`);
	});

	it('is corrected when the anchor is taken off again', () => {
		history.replaceState(history.state, '', '/people?from=p7');

		forgetAnchor(new URL('/people?from=p7', window.location.origin), '/people');

		expect(history.state[ROUTERS_RECORD]).toBe(`${window.location.origin}/people`);
	});

	it('keeps everything else the router put on the entry', () => {
		rememberAnchor(new URL('/people', window.location.origin), '/people', 'p7', 0);

		expect(history.state['sveltekit:history']).toBe(1);
	});

	/* The key is the router's, so it is held against the router's own file rather than trusted. A
	   SvelteKit that renames it would otherwise take this correction quietly back out. */
	it('is named the way the router names it', () => {
		// From the project root, which is where vitest runs: `import.meta.url` is an http address
		// under the dev server's transform and cannot be handed to the file system.
		const constants = readFileSync(
			resolve(process.cwd(), 'node_modules/@sveltejs/kit/src/runtime/client/constants.js'),
			'utf8'
		);

		expect(constants).toContain(`PAGE_URL_KEY = '${ROUTERS_RECORD}'`);
	});
});
