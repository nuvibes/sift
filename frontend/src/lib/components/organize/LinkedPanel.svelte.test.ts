/*
 * The ledger: every person, site and tag a stash-box has been agreed to know. A record rather than
 * a decision.
 *
 * What is held is what a reader could not check for themselves. The two numbers on this screen
 * count different things (the Browse link counts files carrying what a box said, the rows below
 * count subjects), and they would read as a disagreement unless the screen says which is which.
 * That sentence is why the panel has a bar at all.
 */
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';
import { words } from '$lib/design/testing.svelte';

const mocks = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock('$lib/api/client', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/client')>()),
	api: { get: mocks.get }
}));

import LinkedPanel from './LinkedPanel.svelte';

function link(over: Record<string, unknown> = {}) {
	return {
		subject: 'person',
		id: 'p-1',
		name: 'Nadia Vance',
		box_id: 'box-1',
		box: 'StashDB',
		known_as: 'Nadia Vance',
		fetched_at: 0,
		said: [piece('StashDB was linked and had nothing new to fill in')],
		...over
	};
}

/** One run of a line as the wire sends it: every field present, as the server writes them. */
function piece(text: string, over: Record<string, unknown> = {}) {
	return { text, kind: null, id: null, href: null, gone: false, rest: [], lead: '', ...over };
}

function ledger(over: Record<string, unknown> = {}) {
	return { items: [link()], total: 1, files: 0, ...over };
}

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

beforeEach(() => {
	vi.clearAllMocks();
	mocks.get.mockResolvedValue(ledger());
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
	document.body.innerHTML = '';
});

async function draw(props: Record<string, unknown> = {}): Promise<void> {
	drawn = mount(LinkedPanel, { target: host, props }) as Record<string, unknown>;
	flushSync();
	await tick();
	await tick();
	flushSync();
}

it('draws a row per link, with which box said it', async () => {
	await draw();

	expect(words(host)).toContain('Nadia Vance');
	expect(words(host)).toContain('StashDB');
});

it('names the kind of thing each row is, for anybody who cannot see the glyph', async () => {
	/*
	 * The row's only statement of its kind is an icon, labelled by the app's tooltip rather than a
	 * `title` attribute, which the operating system draws in its own typeface and a keyboard cannot
	 * reach. `gate:chrome` refuses `title`; this is the half a gate cannot see.
	 */
	mocks.get.mockResolvedValue(ledger({ items: [link({ subject: 'site' })] }));

	await draw();

	expect(host.querySelector('.kind')?.getAttribute('aria-label')).toBe('Site');
});

it('says the other name a box goes by, and never twice', async () => {
	mocks.get.mockResolvedValue(ledger({ items: [link({ known_as: 'N. Vance' })] }));
	await draw();
	expect(words(host)).toContain('as N. Vance');

	if (drawn) unmount(drawn);
	host.remove();
	host = document.createElement('div');
	document.body.append(host);
	mocks.get.mockResolvedValue(ledger({ items: [link({ known_as: 'Nadia Vance' })] }));
	await draw();
	// The same name twice is a row saying "Nadia Vance as Nadia Vance".
	expect(words(host)).not.toContain('as Nadia Vance');
});

it('says which number counts files and which counts subjects', async () => {
	/* The Browse column counts FILES and these rows count SUBJECTS. Two numbers on one screen that
	   do not match, with nothing saying why, read as one of them being wrong. */
	mocks.get.mockResolvedValue(ledger({ files: 595 }));

	await draw();

	// `words` collapses the whitespace: what is asserted is the sentence, not where prettier
	// chose to wrap it.
	expect(words(host)).toContain('595 files have details from a stash-box');
	expect(words(host)).toContain('People, Sites and tags');
});

it('counts one file in the singular', async () => {
	mocks.get.mockResolvedValue(ledger({ files: 1 }));

	await draw();

	expect(words(host)).toContain('1 file has details');
});

it('says nothing has been enriched rather than drawing an empty list', async () => {
	mocks.get.mockResolvedValue(ledger({ items: [], total: 0 }));

	await draw();

	expect(words(host)).toContain('Nothing enriched by a stash-box yet');
});

it('says so when the ledger could not be read, rather than looking empty', async () => {
	/* An empty wall and a failed read look identical, and the difference is whether there is
	   anything to do about it. */
	mocks.get.mockRejectedValue(new Error('nope'));

	await draw();

	expect(words(host)).toContain("couldn't be loaded");
});

it('reads which kind it shows from the address, so the way back keeps it', async () => {
	/* The kind decides which list the row in `from` is a place in. Held in memory it would be
	   lost on the way back, and the anchor looked for in the other list. A word that is not a kind is
	   no kind at all: `toString` included, which `in` would have taken off the prototype. */
	const { page } = await import('$app/state');
	const was = page.url;
	try {
		(page as { url: URL }).url = new URL('http://localhost/organize/linked?kind=site');
		await draw();
		expect(mocks.get.mock.calls.at(-1)?.[1]?.query?.subject).toBe('site');

		if (drawn) unmount(drawn);
		drawn = null;
		(page as { url: URL }).url = new URL('http://localhost/organize/linked?kind=toString');
		await draw();
		expect(mocks.get.mock.calls.at(-1)?.[1]?.query?.subject).toBe('');
	} finally {
		(page as { url: URL }).url = was;
	}
});

it('publishes a pager to the frame and takes it back down', async () => {
	/* The band is drawn by the frame's foot, not here, so a panel that published one and never
	   withdrew it would leave it under the next screen, counting a list that is not on it. */
	const onpaging = vi.fn();
	mocks.get.mockResolvedValue(ledger({ total: 120 }));

	await draw({ onpaging });

	const published = onpaging.mock.calls.at(-1)?.[0];
	expect(published?.total).toBe(120);
	expect(published?.noun).toBe('links');

	if (drawn) unmount(drawn);
	drawn = null;
	expect(onpaging).toHaveBeenLastCalledWith(null);
});

/** The caret on a row, found by what it is FOR.
 *
 * Not `button[aria-expanded]` alone: the kind picker above the list is a `Select`, and its trigger
 * carries the same attribute, so a bare query would answer the bar's control and every press would
 * go to the wrong thing while reading perfectly plausibly.
 */
function caret(): HTMLButtonElement | null {
	return [...host.querySelectorAll<HTMLButtonElement>('button[aria-expanded]')].find((one) =>
		one.getAttribute('aria-label')?.includes('what StashDB wrote')
	) as HTMLButtonElement | null;
}

it('says what the enrichment filled in, value by value, behind the caret', async () => {
	/*
	 * Each row can say what came of the link, behind a press rather than on the row: this is a
	 * record of hundreds, and a list of fields on every line would be a wall of text where the
	 * point is the column of names. What it says is the SERVER'S line, drawn and never built here:
	 * every field with the value it took, a username as a way to its person.
	 */
	mocks.get.mockResolvedValue(
		ledger({
			items: [
				link({
					said: [
						piece('StashDB filled in birthdate (1991-02-02), usernames ('),
						piece('wren_k', { kind: 'username', id: 'u-1', href: '/people/p-1' }),
						piece(') and gender (Female, since changed)')
					]
				})
			]
		})
	);

	await draw();

	expect(words(host)).not.toContain('birthdate');
	expect(caret()?.getAttribute('aria-expanded')).toBe('false');

	caret()?.click();
	flushSync();

	expect(caret()?.getAttribute('aria-expanded')).toBe('true');
	expect(words(host)).toContain(
		'StashDB filled in birthdate (1991-02-02), usernames (wren_k) and gender (Female, since changed)'
	);
	const named = host.querySelector<HTMLAnchorElement>('.wrote a');
	expect(named?.textContent).toBe('wren_k');
	expect(named?.getAttribute('href')).toBe('/people/p-1');
});

it('draws the line the server sent, and builds none of its own', async () => {
	/* The words are the server's; a row builds no sentence of its own from a null list and says
	   only what `said` says. */
	mocks.get.mockResolvedValue(
		ledger({
			items: [
				link({
					said: [piece('StashDB was linked before Sift recorded what a stash-box fills in')]
				})
			]
		})
	);

	await draw();
	caret()?.click();
	flushSync();

	expect(words(host)).toContain(
		'StashDB was linked before Sift recorded what a stash-box fills in'
	);
	expect(words(host).toLowerCase()).not.toContain('no record');
});
