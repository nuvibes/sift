/*
 * The cookies sheet: the four states, the read-back in front of Save, and the two asks.
 *
 * What is pinned is what a person can tell from this screen, because what it is about is invisible
 * by design. A cookie travels one way (sent, sealed, never rendered back), so the state pill, the
 * line under it and the read-back are the only evidence that the right file went to the right Site.
 * Each is checked against its words rather than a class, since the words are the feature.
 *
 * The last test holds the vocabulary: the thing is cookies, and "login" is not a word this surface
 * uses.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), del: vi.fn() }));

vi.mock('$lib/api/client', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/client')>()),
	api: { get: mocks.get, post: mocks.post, del: mocks.del }
}));

import { reactiveProps, words as wordsOn } from '$lib/design/testing.svelte';
import CookiesSheet from './CookiesSheet.svelte';
import codepoints from '$lib/generated/icon-codepoints.json';

/* A fixed moment. Not tidiness: a wall clock can step backwards, so a relative sentence read
   twice can come back as two different sentences. */
const NOW = Date.UTC(2026, 8, 19, 12, 0, 0);
const AT = (days: number) => (NOW + days * 86400000) / 1000;

const SITES = [
	{
		key: 'sunhollow',
		name: 'Sunhollow',
		hosts: ['sunhollow.example'],
		media: [],
		walls: [],
		bulk: false,
		tested: true,
		supported: true,
		names_creators: false,
		cookies: 'partial',
		cookies_why: 'Age-restricted videos need cookies.',
		cookies_with_a_tool: null
	},
	{
		key: 'tidewater',
		name: 'Tidewater',
		hosts: ['tidewater.example', 'tide.example'],
		media: [],
		walls: [],
		bulk: false,
		tested: true,
		supported: true,
		names_creators: false,
		cookies: 'not_needed',
		cookies_why: 'Everything downloads without them.',
		cookies_with_a_tool: null
	},
	{
		key: 'marchfield',
		name: 'Marchfield',
		hosts: [],
		media: [],
		walls: [],
		bulk: false,
		tested: true,
		supported: true,
		names_creators: false,
		cookies: 'required',
		cookies_why: 'Every post needs them.',
		cookies_with_a_tool: null
	},
	{
		key: 'quillbrook',
		name: 'Quillbrook',
		hosts: [],
		media: [],
		walls: [],
		bulk: false,
		tested: true,
		supported: true,
		names_creators: false,
		cookies: 'not_needed',
		cookies_why: 'Sift never sends cookies here.',
		cookies_with_a_tool: 'required'
	}
];

const SAVED = [
	{
		id: 'c-1',
		site: 'sunhollow',
		state: 'saved',
		expires_at: AT(56),
		expires_last: AT(56),
		last_used_at: AT(-1),
		status: null,
		updated_at: null
	},
	{
		id: 'c-2',
		site: 'tidewater',
		state: 'ending_soon',
		expires_at: AT(2),
		expires_last: AT(2),
		last_used_at: null,
		status: null,
		updated_at: null
	},
	{
		id: 'c-3',
		site: 'marchfield',
		state: 'expired',
		expires_at: AT(-9),
		expires_last: AT(-9),
		last_used_at: AT(-30),
		status: null,
		updated_at: null
	}
];

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

beforeEach(() => {
	vi.clearAllMocks();
	vi.useFakeTimers();
	vi.setSystemTime(NOW);
	mocks.get.mockImplementation((path: string) =>
		Promise.resolve(path === '/supported-sites' ? SITES : SAVED)
	);
	mocks.post.mockResolvedValue({});
	mocks.del.mockResolvedValue({});
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
	document.body.innerHTML = '';
	vi.useRealTimers();
});

/** Mount the sheet open, and let the two list reads land. */
async function render(overrides: Record<string, unknown> = {}) {
	host = document.createElement('div');
	document.body.append(host);
	const props = reactiveProps({ open: true, site: null, ...overrides });
	drawn = mount(CookiesSheet, { target: host, props: props as never });
	flushSync();
	await vi.advanceTimersByTimeAsync(0);
	flushSync();
	return props;
}

/** Everything a person can read on the sheet, portal and all. */
const words = () => document.body.textContent ?? '';

/** One Site's row on the list. */
const rowNamed = (name: string) =>
	[...document.querySelectorAll('ul.sites > li')].find(
		(row) => row.querySelector('.who')?.textContent?.trim() === name
	);

/** One Site's row as it reads across. */
const rowText = (name: string) => (rowNamed(name)?.textContent ?? '').replace(/\s+/g, ' ');

/** The badge on one Site's row, and its word. */
const badgeOf = (name: string) => rowNamed(name)?.querySelector('.badge') ?? null;
/* The character an icon name is drawn as, so a badge's mark can be named rather than guessed. */
const glyphOf = (name: string) =>
	String.fromCodePoint(parseInt((codepoints as Record<string, string>)[name], 16));
/* The badge's own word: its text nodes, without the icon's ligature beside them. */
const rowBadge = (name: string) =>
	(badgeOf(name)?.querySelector('.word, .said')?.textContent ?? '').replace(/\s+/g, ' ').trim();

/** One named button, wherever it was portalled to. */
function button(name: string): HTMLButtonElement {
	const found = [...document.querySelectorAll('button')].find(
		(one) => one.getAttribute('aria-label') === name || wordsOn(one).startsWith(name)
	);
	expect(found, `no button called ${name}`).toBeTruthy();
	return found as HTMLButtonElement;
}

/** Escape, as a browser sends it: at the document, and cancelable. */
async function escape() {
	document.dispatchEvent(
		new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })
	);
	await vi.advanceTimersByTimeAsync(0);
	flushSync();
}

async function press(name: string) {
	button(name).click();
	await vi.advanceTimersByTimeAsync(0);
	flushSync();
}

describe('the four states, in words', () => {
	it('says what each Site stands at, and when it ends and was last used', async () => {
		await render();

		// The three the server sent, each in the state's own words rather than a colour alone.
		expect(words()).toContain('Cookies saved');
		expect(words()).toContain('Ending soon');
		expect(words()).toContain('Cookies expired');

		// The line under the name. Both halves, and neither invented: Tidewater has never been used,
		// so it says so rather than reporting a moment it does not have.
		expect(words()).toContain('Last used yesterday.');
		expect(words()).toContain('Not used yet.');
		expect(words()).toContain('Ended ');
	});

	it('gives a supported Site with nothing saved a line of its own', async () => {
		await render();

		// The Site somebody opens this sheet FOR is by definition the one with no row, so a list of
		// only what is already saved cannot show it.
		expect(words()).toContain('Quillbrook');
		// Its badge says what the Site NEEDS, never "None": that is the question somebody with
		// nothing saved is asking, and "None" answers a different one.
		expect(rowBadge('Quillbrook')).toBe('Not required');
		expect(words()).not.toContain('None');
		// And the sentence under it is the catalog's, for this Site, not a guess about the history.
		expect(words()).toContain('Sift never sends cookies here.');
		expect(words()).not.toContain('have not needed any');
	});

	it('says each need in its own word and colour when nothing is saved, and "Cookies saved" once it is', async () => {
		// Nothing saved anywhere, so every row says its need.
		mocks.get.mockImplementation((path: string) =>
			Promise.resolve(path === '/supported-sites' ? SITES : [])
		);
		await render();

		expect(rowBadge('Marchfield')).toBe('Required');
		expect(badgeOf('Marchfield')?.classList.contains('state-failed')).toBe(true);
		expect(rowBadge('Sunhollow')).toBe('Partial');
		expect(badgeOf('Sunhollow')?.classList.contains('state-blocked')).toBe(true);
		expect(rowBadge('Tidewater')).toBe('Not required');
		expect(badgeOf('Tidewater')?.classList.contains('state-queued')).toBe(true);
		// The grey answer wears the grey state's own list mark, never a tick, which is the mark of
		// a thing done.
		expect(badgeOf('Tidewater')?.querySelector('.icon')?.textContent).toBe(
			glyphOf('playlist_add_check')
		);
		expect(badgeOf('Tidewater')?.querySelector('.icon')?.textContent).not.toBe(glyphOf('check'));
		// What Partial means on this Site, under the badge.
		expect(rowText('Sunhollow')).toContain('Age-restricted videos need cookies.');
	});

	it('says how saved cookies stand once there are some, whatever the Site needs', async () => {
		await render();

		// Sunhollow is Partial and has cookies saved: the badge is about the cookies now.
		expect(rowBadge('Sunhollow')).toBe('Cookies saved');
		expect(badgeOf('Sunhollow')?.classList.contains('state-done')).toBe(true);
	});
});

describe('the read-back in front of Save', () => {
	/** Type a file into the box and let the settling timer fire. */
	async function paste(text: string) {
		const box = document.querySelector('textarea') as HTMLTextAreaElement;
		box.value = text;
		box.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		await vi.advanceTimersByTimeAsync(500);
		flushSync();
	}

	it('will not save until the server has said what it read', async () => {
		await render({ site: 'sunhollow' });

		// Nothing pasted: nothing to describe, and nothing to save.
		expect(button('Save cookies').disabled).toBe(true);

		mocks.post.mockResolvedValueOnce({
			cookies: 14,
			domains: ['sunhollow.example'],
			expires_at: AT(74),
			expired: false
		});
		await paste('sunhollow.example\tTRUE\t/\tTRUE\t0\tsid\tx');

		expect(words()).toContain('Read: 14 cookies for sunhollow.example, until ');
		expect(button('Save cookies').disabled).toBe(false);
	});

	it('refuses a file that is not one, and says how to get one that is', async () => {
		await render({ site: 'sunhollow' });

		mocks.post.mockResolvedValueOnce({ cookies: 0, domains: [], expires_at: null, expired: false });
		await paste('a shopping list');

		expect(words()).toContain(
			"That isn't a cookie file. Export one with a browser extension while signed in to Sunhollow."
		);
		expect(button('Save cookies').disabled).toBe(true);
	});

	it('refuses cookies that have already run out, which look fine to the eye', async () => {
		await render({ site: 'sunhollow' });

		mocks.post.mockResolvedValueOnce({
			cookies: 9,
			domains: ['sunhollow.example'],
			expires_at: AT(-3),
			expired: true
		});
		await paste('sunhollow.example\tTRUE\t/\tTRUE\t0\tsid\told');

		expect(words()).toContain(
			'These cookies have already run out. Open the Site in your browser so it hands out new ones, then export a fresh file.'
		);
		expect(button('Save cookies').disabled).toBe(true);
	});

	it('saves, then tries the download again, when it was opened from a stopped row', async () => {
		const props = await render({ site: 'sunhollow', retry: 'd-42' });

		// The words say what the press will do. "Save cookies" here would be true and incomplete,
		// and somebody would go back to the queue to press Try again on a row that is already going.
		expect(button('Save and try the download again')).toBeTruthy();

		mocks.post.mockResolvedValueOnce({
			cookies: 14,
			domains: ['sunhollow.example'],
			expires_at: AT(74),
			expired: false
		});
		await paste('sunhollow.example\tTRUE\t/\tTRUE\t0\tsid\tgood');
		await press('Save and try the download again');

		const paths = mocks.post.mock.calls.map((call) => call[0]);
		expect(paths).toContain('/site-connections');
		expect(paths).toContain('/downloads/d-42/retry');
		// The retry goes AFTER the save, or it starts against cookies that are only half written.
		expect(paths.indexOf('/downloads/d-42/retry')).toBeGreaterThan(
			paths.indexOf('/site-connections')
		);
		expect(props.open).toBe(false);
	});
});

describe('the two asks', () => {
	it('asks before forgetting, and names the Site and what waits on it', async () => {
		await render();

		// The menu is the door; what matters is that the press does not remove anything by itself.
		await press('More for Sunhollow');
		// A menu row is the library's `menuitem`, not a button, and the icon in front of its words
		// is a LIGATURE: a private-use codepoint that `trim()` leaves in place, so the words are
		// matched with it stripped rather than compared whole.
		const rows = [...document.querySelectorAll('[role="menuitem"]')];
		const forget = rows.find((row) =>
			(row.textContent ?? '')
				.replace(/[\uE000-\uF8FF]/g, '')
				.trim()
				.startsWith('Delete')
		);
		expect(forget, 'no Delete row in the menu').toBeTruthy();
		(forget as HTMLElement).click();
		await vi.advanceTimersByTimeAsync(0);
		flushSync();

		expect(words()).toContain('Delete the Sunhollow cookies?');
		expect(words()).toContain(
			'Downloads that need these cookies wait until you add new ones. Nothing already downloaded is affected.'
		);
		expect(mocks.del).not.toHaveBeenCalled();
	});

	it('repeats what the Site itself said when Check is pressed', async () => {
		mocks.post.mockResolvedValueOnce({ accepted: true, said: 'Sunhollow still accepts these.' });
		await render();

		await press('Check the Sunhollow cookies');

		expect(mocks.post).toHaveBeenCalledWith('/site-connections/c-1/check');
		expect(words()).toContain('Sunhollow still accepts these.');
		// Read out where the press was, as an answer rather than an interruption.
		expect(document.querySelector('.said')?.getAttribute('role')).toBe('status');
	});
});

describe('the word', () => {
	/*
	 * Nobody hands Sift an account here, and calling it a login says Sift holds more than it holds,
	 * so the word does not appear at all, in copy or in comment, in any of the files this feature
	 * is written in.
	 */
	const here = dirname(fileURLToPath(import.meta.url));
	const FILES = [
		join(here, 'CookiesSheet.svelte'),
		join(here, 'cookies.ts'),
		resolve(here, '..', '..', 'settings-ui', 'connections-state.svelte.ts'),
		resolve(here, '..', '..', 'settings-ui', 'Sites.svelte')
	];

	for (const file of FILES) {
		it(`${file.split(/[\\/]/).pop()} never says login`, () => {
			expect(readFileSync(file, 'utf8')).not.toMatch(/\blogins?\b/i);
		});
	}
});

describe('the table', () => {
	/** Each row's Site name, in order. */
	const rows = () =>
		[...document.querySelectorAll('ul.sites > li .who')].map((one) => one.textContent?.trim());

	/** One Site's row, as a person reads it across. */
	const rowOf = (name: string) =>
		(
			[...document.querySelectorAll('ul.sites > li')].find(
				(row) => row.querySelector('.who')?.textContent?.trim() === name
			)?.textContent ?? ''
		).replace(/\s+/g, ' ');

	it("says on every row what the Site does without cookies, in the catalog's words", async () => {
		await render();

		expect(rowOf('Sunhollow')).toContain('Age-restricted videos need cookies.');
		expect(rowOf('Tidewater')).toContain('Everything downloads without them.');
		expect(rowOf('Marchfield')).toContain('Every post needs them.');
		expect(rowOf('Quillbrook')).toContain('Sift never sends cookies here.');
	});

	/* Every row has a mark: the pack's logo asked by the Site's own address where it has one, and
	   the letter where it has none, never an empty cell. */
	it('gives every row a mark, asked by the Site address', async () => {
		await render();

		const marks = [...document.querySelectorAll('ul.sites > li > .mark')];
		expect(marks.length).toBe(4);
		for (const mark of marks) expect(mark.childElementCount).toBeGreaterThan(0);
		const asked = [...document.querySelectorAll('ul.sites img')].map((one) =>
			one.getAttribute('src')
		);
		expect(asked).toContain('/api/sites/icons/for?host=sunhollow.example');
	});

	/* Drawn as the download queue draws it: the logo alone, no plate behind it. */
	it("draws every Site's mark bare, as the download queue does", async () => {
		await render();

		const drawn = [...document.querySelectorAll('ul.sites > li > .mark > .avatar')];
		expect(drawn.length).toBe(4);
		for (const one of drawn) expect(one.classList.contains('bare')).toBe(true);
	});

	it('narrows the list to what is typed, by name or by address', async () => {
		await render();

		const box = document.querySelector('input[aria-label="Search Sites"]') as HTMLInputElement;
		expect(box, 'no search box').toBeTruthy();
		box.value = 'tide.exa';
		box.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		expect(rows()).toEqual(['Tidewater']);

		box.value = 'MARCH';
		box.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		expect(rows()).toEqual(['Marchfield']);
	});

	it('says so when nothing matches', async () => {
		await render();

		const box = document.querySelector('input[aria-label="Search Sites"]') as HTMLInputElement;
		box.value = 'no such place';
		box.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		expect(rows()).toEqual([]);
		expect(words()).toContain('No Site matches that search.');
	});
});

describe('the stack', () => {
	/* Cancel on the add step takes that step off and leaves the list showing. As the dialog's Close
	   it would drop somebody back on the page having only meant not to add these cookies. */
	it('goes back to the list on Cancel, and stays open', async () => {
		const props = await render();

		await press('Add cookies for Quillbrook');
		expect(words()).toContain('Add cookies for Quillbrook');

		await press('Cancel');
		expect(props.open).toBe(true);
		expect(document.querySelector('ul.sites')).not.toBeNull();
		expect(words()).not.toContain('Drop the cookie file here');
	});

	it('goes back to the list on Cancel when it was opened on one Site', async () => {
		const props = await render({ site: 'sunhollow' });

		await press('Cancel');
		expect(props.open).toBe(true);
		expect(document.querySelector('ul.sites')).not.toBeNull();
	});

	/*
	 * Escape says what Cancel says on the add step: back a step, not closing the whole sheet, so
	 * the same wish gets one answer whichever hand it comes from.
	 */
	it('goes back to the list on Escape, and stays open', async () => {
		const props = await render();

		await press('Add cookies for Quillbrook');
		await escape();
		expect(props.open).toBe(true);
		expect(document.querySelector('ul.sites')).not.toBeNull();
		expect(words()).not.toContain('Drop the cookie file here');
	});

	it('closes on Escape from the list, where there is nothing under it', async () => {
		const props = await render();

		await escape();
		expect(props.open).toBe(false);
	});
});
