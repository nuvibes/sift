/*
 * What a file's own name said about where it came from, as the workbench draws it.
 *
 * Three things are held:
 *
 * - "See these files" goes to the person behind the username, or where nobody is, to the Files wall
 *   filtered with `?username=` (the set the pass filed these under, as a parameter), never a typed
 *   query that fills the search box with chips.
 * - A single file opens the popout over this screen; a bare anchor would run the `/asset/[id]`
 *   route, which tears the screen behind it down and leaves the panel with nothing underneath.
 * - A card's file list arrives folded with an expand control: the server sends up to two dozen rows
 *   per username and a page holds a dozen cards.
 *
 * Each is asserted as what somebody sees (an address, a prevented click, a row count) rather than
 * as the function producing it, because the markup can be wrong while the data behind it is right.
 */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';

const mocks = vi.hoisted(() => ({
	filings: vi.fn(),
	takeBack: vi.fn(),
	decided: vi.fn(),
	shown: vi.fn(),
	goto: vi.fn(),
	openAssetInstead: vi.fn()
}));

vi.mock('$app/navigation', () => ({ goto: mocks.goto, replaceState: vi.fn() }));
vi.mock('$lib/player/asset-view', () => ({ openAssetInstead: mocks.openAssetInstead }));
vi.mock('$lib/search/suggestions.svelte', () => ({
	filingsFromFilenames: mocks.filings,
	takeBackUsername: mocks.takeBack
}));
vi.mock('$lib/organize/organize.svelte', () => ({
	answered: { stamp: 0, changed: vi.fn() },
	decided: mocks.decided,
	undo: vi.fn(async () => undefined)
}));
vi.mock('$lib/shell/toasts.svelte', () => ({ toasts: { show: mocks.shown } }));

import FilenamesPanel from './FilenamesPanel.svelte';
import { phoneWidth } from '$lib/components/common/phone-width.svelte';

/** One file under a card, named the way a downloader names one. */
function file(at: number): Record<string, unknown> {
	return {
		asset_id: `asset-${at}`,
		filename: `esmewrenfield_${at}.mp4`,
		decision_id: `decision-${at}`,
		art: null
	};
}

function group(howMany: number, over: Record<string, unknown> = {}): Record<string, unknown> {
	return {
		username_id: 'a-1',
		username: 'esmewrenfield',
		site: 'SomeSite',
		// The wire always carries it; null is a username nobody has said who is behind.
		person_id: null,
		files: howMany,
		shown: Array.from({ length: howMany }, (_one, at) => file(at)),
		...over
	};
}

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

beforeEach(() => {
	vi.clearAllMocks();
	mocks.filings.mockResolvedValue({ groups: [], total: 0, offset: 0 });
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (drawn) unmount(drawn, { outro: false });
	drawn = null;
	host?.remove();
});

async function draw(): Promise<void> {
	drawn = mount(FilenamesPanel, { target: host }) as Record<string, unknown>;
	flushSync();
	await tick();
	await tick();
	flushSync();
}

/** A button of the card, by the words on it. */
function press(words: string): void {
	[...host.querySelectorAll<HTMLButtonElement>('button')]
		.find((one) => one.textContent?.includes(words))
		?.click();
	flushSync();
}

/** The row's fold control: its disclosure arrow, not the chevron beside Open. */
function toggle(): HTMLButtonElement | null {
	return host.querySelector<HTMLButtonElement>('.disclose button[aria-expanded]');
}

/* An answer behind the chevron beside Open: the menu opens on POINTERDOWN, its rows portalled. */
async function answer(words: string): Promise<void> {
	host
		.querySelector('.split .trail button')
		?.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0 }));
	flushSync();
	await tick();
	const row = [...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].find((one) =>
		one.textContent?.includes(words)
	);
	if (!row) throw new Error(`there is no answer saying ${words}`);
	row.click();
	flushSync();
	for (let turn = 0; turn < 4; turn += 1) await tick();
}

/** The filename rows that are actually on screen. */
function rows(): string[] {
	return [...host.querySelectorAll('a.file')].map((one) => one.textContent?.trim() ?? '');
}

it("sends the way in to the username's own files rather than to a typed query", async () => {
	/*
	 * `/browse?q=...` is what fills the search box with chips, and it is also the wrong set:
	 * `file_name:` matches text, so it catches another username whose files carry the same word and
	 * misses a file of this one that has since been renamed. `?username=` is the filed set, as a
	 * parameter.
	 */
	mocks.filings.mockResolvedValue({ groups: [group(2)], total: 1, offset: 0 });

	await draw();
	press('Open these files');

	expect(mocks.goto).toHaveBeenCalledWith('/browse?username=a-1');
	expect(host.innerHTML).not.toContain('/browse?q=');
	expect(host.innerHTML).not.toContain('/usernames/');
});

it('sends the way in to the person when somebody is behind the username', async () => {
	// Every file under the username already counts under them, and their page is where the username
	// is drawn: under the Site's card on their Sites tab.
	mocks.filings.mockResolvedValue({
		groups: [group(2, { person_id: 'p-9' })],
		total: 1,
		offset: 0
	});

	await draw();
	press('Open these files');

	expect(mocks.goto).toHaveBeenCalledWith('/people/p-9');
	expect(host.querySelector('.username a')?.getAttribute('href')).toBe('/people/p-9');
});

it('makes the username itself a link to the same place, with the site beside it', async () => {
	// One address for the two, so they cannot drift.
	mocks.filings.mockResolvedValue({ groups: [group(2)], total: 1, offset: 0 });

	await draw();

	const username = host.querySelector<HTMLAnchorElement>('.username a');
	expect(username?.getAttribute('href')).toBe('/browse?username=a-1');
	expect(username?.textContent?.trim()).toBe('esmewrenfield');
	expect(host.textContent).toContain('on SomeSite');
});

it('opens a file where it stands instead of running the asset route', async () => {
	/* The anchor keeps its address (middle-click and "copy link address" still work) and only a
	   plain left click is taken. Without the handler the route runs, the wall behind is rebuilt and
	   closing the panel lands on the library. */
	mocks.filings.mockResolvedValue({ groups: [group(2)], total: 1, offset: 0 });

	await draw();
	toggle()?.click();
	flushSync();
	const link = host.querySelector<HTMLAnchorElement>('a.file');
	expect(link?.getAttribute('href')).toBe('/asset/asset-0');
	link?.click();

	expect(mocks.openAssetInstead).toHaveBeenCalledOnce();
	expect(mocks.openAssetInstead.mock.calls[0][1]).toBe('asset-0');
});

it('folds a long list away, and opens it when asked', async () => {
	// Every row arrives as a still, a username, a count and the way in, at a row's height.
	mocks.filings.mockResolvedValue({ groups: [group(6)], total: 1, offset: 0 });

	await draw();

	expect(rows()).toEqual([]);
	expect(toggle()?.getAttribute('aria-expanded')).toBe('false');
	expect(host.querySelector('.count')?.textContent).toBe('6 files');

	toggle()?.click();
	flushSync();
	expect(rows()).toHaveLength(6);
	expect(toggle()?.getAttribute('aria-expanded')).toBe('true');

	/* Shutting it is asserted on the CONTROL and not on the rows, and that is not a weaker check: the
	   block leaves on the app's disclosure transition, so the rows are still in the document while it
	   plays and a row count read here would be reading the outro. What a person is told is the state,
	   and the state is what the next press acts on. */
	toggle()?.click();
	flushSync();
	expect(toggle()?.getAttribute('aria-expanded')).toBe('false');
});

it('arrives folded however short the list, one press from its files', async () => {
	// A row is a row's height whatever its group holds; the files are one press further.
	mocks.filings.mockResolvedValue({ groups: [group(2)], total: 1, offset: 0 });

	await draw();

	expect(rows()).toEqual([]);
	toggle()?.click();
	flushSync();
	expect(rows()).toHaveLength(2);
});

it('answers a row Yes by opening it, and No by taking the whole username back as one decision', async () => {
	/* Yes is Open: a filing already made needs no yes. No is behind the chevron, and its one
	   decision's Undo rides on the toast. */
	mocks.filings.mockResolvedValue({ groups: [group(2)], total: 1, offset: 0 });
	mocks.takeBack.mockResolvedValue({ files: 2, decision_id: 'decision-all' });

	await draw();
	expect(host.querySelector('.split .lead button')?.textContent).toContain('Open these files');
	await answer('No, take these files back');

	expect(mocks.takeBack).toHaveBeenCalledWith('a-1');
	expect(mocks.decided).toHaveBeenCalledWith(
		[
			'Removed 2 files from ',
			{
				text: 'esmewrenfield on SomeSite',
				kind: 'username',
				id: 'a-1',
				href: '/browse?username=a-1'
			}
		],
		'decision-all'
	);
});

it('says so when a No finds nothing left to undo', async () => {
	mocks.filings.mockResolvedValue({ groups: [group(2)], total: 1, offset: 0 });
	mocks.takeBack.mockResolvedValue({ files: 0, decision_id: '' });

	await draw();
	await answer('No, take these files back');

	expect(mocks.decided).not.toHaveBeenCalled();
	expect(mocks.shown).toHaveBeenCalledWith('There was nothing left to undo');
});

it('says the username is on the Site, in the words History uses', async () => {
	/*
	 * The sentence everywhere else in Sift is "<username> on <Site>" (see `lib/library/filings.ts`), and
	 * the card's title says the same, not "quillmoss Instagram". Asserted on the text rather than
	 * the function that builds it, because the function can be right while the markup drops a
	 * space.
	 */
	mocks.filings.mockResolvedValue({ groups: [group(2)], total: 1, offset: 0 });

	await draw();

	const title = host.querySelector('.username')?.textContent?.replace(/\s+/g, ' ').trim();
	expect(title).toBe('esmewrenfield on SomeSite');
});

it('stands a row with no still in the same columns as every other row', async () => {
	/* A username the page has no file of to draw (its files unreachable) keeps its still's cell
	   empty, so its count and its answers stand where the next row's do. */
	mocks.filings.mockResolvedValue({
		groups: [group(2), group(0, { username_id: 'a-2', username: 'orlafennimore', files: 9 })],
		total: 2,
		offset: 0
	});

	await draw();

	const lines = [...host.querySelectorAll<HTMLElement>('.row.columned')];
	expect(lines).toHaveLength(2);
	const placed = lines.map((line) => {
		const cells = [...line.querySelectorAll<HTMLElement>(':scope > .cell')];
		return {
			cells: cells.length,
			count: cells.findIndex((cell) => cell.querySelector('.count')),
			answers: cells.findIndex((cell) => cell.querySelector('.split'))
		};
	});
	expect(placed[0]).toEqual(placed[1]);
	expect(placed[1]).toEqual({ cells: 5, count: 2, answers: 3 });
	expect(lines[1].querySelector('img')).toBeNull();
});

it('stands the count and the answers under the name at a phone width, so the name keeps the row', async () => {
	/* At 393 wide the four desktop tracks would leave the username 8 px, one letter per line,
	   and Open would run off the right edge. On a phone the row is the still and the
	   name, with the count and the answers on a line under the name. */
	phoneWidth.yes = true;
	try {
		mocks.filings.mockResolvedValue({ groups: [group(2)], total: 1, offset: 0 });

		await draw();

		const line = host.querySelector<HTMLElement>('.row.columned');
		const cells = [...(line?.querySelectorAll<HTMLElement>(':scope > .cell') ?? [])];
		const named = cells.find((cell) => cell.querySelector('.username'));
		expect(named?.querySelector('.under .count')?.textContent).toContain('2');
		expect(named?.querySelector('.under .split')).not.toBeNull();
		expect(cells.filter((cell) => cell.querySelector('.count'))).toHaveLength(1);
	} finally {
		phoneWidth.yes = false;
	}
});
