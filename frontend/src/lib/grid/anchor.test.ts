/*
 * The row a wall is opened at. Each guard exists for a quiet failure: a write from a screen no
 * longer on top, a write that is a navigation, an absolute address a proxy makes external, an
 * anchor and an offset together.
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
	page: { state: {} }
}));

const went = vi.mocked(replaceState);
const going = navigating as { to: unknown };
const showing = page as unknown as { state: Record<string, unknown> };

function partway(): void {
	going.to = { url: new URL('http://sift.test/organize'), route: { id: '/organize' } };
}

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
		/* Sift is reached through proxies and tunnels, so a rebuilt origin is a real failure. */
		rememberAnchor(at('/people'), '/people', 'p7', 0);

		expect(String(went.mock.calls[0][0])).toBe('/people?from=p7&near=0');
	});

	it('carries the page state, so a panel opened over the wall is not thrown away', () => {
		/*
		 * THE FOURTH EDGE: a correction landing later must keep the page state a panel is drawn
		 * from.
		 */
		showing.state = { settings: 'library' };

		rememberAnchor(at('/people'), '/people', 'p7', 0);

		expect(went.mock.calls[0][1]).toEqual({ settings: 'library' });
	});

	it('says nothing when the screen it belongs to is no longer the one on top', () => {
		/* Opening a file pushes an address over a wall still refreshing underneath. */
		rememberAnchor(at('/assets/a1'), '/people', 'p7', 0);

		expect(went).not.toHaveBeenCalled();
	});

	it('says nothing for a row with no id', () => {
		// A card that exists to have no name is not one an address should name.
		rememberAnchor(at('/organize/identified'), '/organize/identified', null, 0);
		rememberAnchor(at('/organize/identified'), '/organize/identified', undefined, 0);
		rememberAnchor(at('/organize/identified'), '/organize/identified', '', 0);

		expect(went).not.toHaveBeenCalled();
	});

	it('says nothing when the address already names that row', () => {
		rememberAnchor(at('/people?from=p7&near=24'), '/people', 'p7', 24);

		expect(went).not.toHaveBeenCalled();
	});

	it('replaces the anchor when the page has moved on', () => {
		rememberAnchor(at('/people?from=p7&near=24'), '/people', 'p9', 48);

		expect(String(went.mock.calls[0][0])).toBe('/people?from=p9&near=48');
	});

	it('moves `near` with the row even when only the offset changed', () => {
		/* Something above was removed: the row is the fact, its offset what the way back needs. */
		rememberAnchor(at('/people?from=p7&near=24'), '/people', 'p7', 23);

		expect(String(went.mock.calls[0][0])).toBe('/people?from=p7&near=23');
	});

	it('says nothing while somebody is already on their way somewhere else', () => {
		/*
		 * During a navigation the path check still passes, and the write would land on top of the
		 * move.
		 */
		partway();

		rememberAnchor(at('/people'), '/people', 'p7', 0);

		expect(went).not.toHaveBeenCalled();
	});

	it('tells what remembers the address, because a replaceState is invisible to it', () => {
		/*
		 * THE FIFTH EDGE: `replaceState` never moves `page.url`, so the wall tells the recorder
		 * itself.
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
		expect(asked({ limit: 60, from: 'p7' })).not.toHaveProperty('offset');
		expect(asked({ limit: 60, offset: 120 })).not.toHaveProperty('from');
	});
});

/*
 * WHAT BACK RETURNS TO: the history entry's own record, which `replaceState` stamps with the stale
 * address.
 */
describe("the history entry's own record of the address it is for", () => {
	const ROUTERS_RECORD = 'sveltekit:pageurl';

	beforeEach(() => {
		history.replaceState(
			{ 'sveltekit:history': 1, [ROUTERS_RECORD]: `${window.location.origin}/people` },
			'',
			'/people'
		);
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

	/* Held against the router's own file, so a rename is caught. */
	it('is named the way the router names it', () => {
		// From the project root: `import.meta.url` is an http address under the dev server.
		const constants = readFileSync(
			resolve(process.cwd(), 'node_modules/@sveltejs/kit/src/runtime/client/constants.js'),
			'utf8'
		);

		expect(constants).toContain(`PAGE_URL_KEY = '${ROUTERS_RECORD}'`);
	});
});
