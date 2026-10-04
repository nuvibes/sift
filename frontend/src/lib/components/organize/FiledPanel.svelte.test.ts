/*
 * What a pass filed under somebody without asking, as the record tab draws it.
 *
 * Every card must be the same size regardless of how much it says. The height is a stylesheet rule
 * that a document applying no styles cannot read; what can be asserted, and is what makes the rule
 * hold, is that a long list arrives folded and opens on a press.
 */
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';

const mocks = vi.hoisted(() => ({ filed: vi.fn(), takeBack: vi.fn(), decided: vi.fn() }));

vi.mock('$lib/search/suggestions.svelte', () => ({
	filedWithoutAsking: mocks.filed,
	takeBackFolder: mocks.takeBack
}));
vi.mock('$lib/organize/organize.svelte', () => ({ decided: mocks.decided }));

import FiledPanel from './FiledPanel.svelte';

/** One folder filed under one person, as the wire sends it. */
function folder(at: number, over: Record<string, unknown> = {}) {
	return {
		person_id: 'p-1',
		person: 'Neve Arbogast',
		folder_id: `f-${at}`,
		folder: `Shoot ${at}`,
		path: `Pictures/Shoot ${at}`,
		files: 3,
		art: null,
		cover_asset_id: null,
		cover_upload_id: null,
		...over
	};
}

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

beforeEach(() => {
	vi.clearAllMocks();
	mocks.filed.mockResolvedValue([]);
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (drawn) unmount(drawn, { outro: false });
	drawn = null;
	host?.remove();
});

async function draw(): Promise<void> {
	drawn = mount(FiledPanel, { target: host }) as Record<string, unknown>;
	flushSync();
	await tick();
	await tick();
	flushSync();
}

/** The folder rows actually on screen. */
function rows(): string[] {
	return [...host.querySelectorAll('.path')].map((one) => one.textContent?.trim() ?? '');
}

/** The card's fold control, which is the only thing on it carrying `aria-expanded`. */
function toggle(): HTMLButtonElement | null {
	return host.querySelector<HTMLButtonElement>('button[aria-expanded]');
}

it('folds a long list away, and opens it when asked', async () => {
	mocks.filed.mockResolvedValue(Array.from({ length: 9 }, (_one, at) => folder(at)));

	await draw();

	expect(rows()).toHaveLength(4);
	expect(toggle()?.textContent?.trim()).toBe('Show all 9 folders');

	toggle()?.click();
	flushSync();

	expect(rows()).toHaveLength(9);
	expect(toggle()?.getAttribute('aria-expanded')).toBe('true');
});

it('leaves a short list open, with no control at all', async () => {
	// A toggle over four rows is a press that saves nothing, and a card that folds one thing and
	// not the next reads as the fold meaning something.
	mocks.filed.mockResolvedValue(Array.from({ length: 4 }, (_one, at) => folder(at)));

	await draw();

	expect(rows()).toHaveLength(4);
	expect(toggle()).toBeNull();
});

it('sums the folders and the files a person was filed for', async () => {
	/* The card's own line, which is what the folded rows leave somebody with, so it has to be
	   about the WHOLE group rather than about what is showing. */
	mocks.filed.mockResolvedValue(Array.from({ length: 6 }, (_one, at) => folder(at)));

	await draw();

	expect(host.querySelector('.tally')?.textContent?.replace(/\s+/g, ' ')).toContain('6 folders');
	expect(host.querySelector('.tally')?.textContent?.replace(/\s+/g, ' ')).toContain('18 files');
});

/*
 * The count on each row opens those files: the person's own page, filtered by two ordinary query
 * fields its Files grid reads out of the address, the folder and what a folder-name reading wrote.
 * See `filedHref`.
 */
it("links each row's count to the person's page, narrowed to that folder's filings", async () => {
	mocks.filed.mockResolvedValue([folder(1, { files: 7007 })]);

	await draw();

	const count = host.querySelector<HTMLAnchorElement>('a.count');
	expect(count).not.toBeNull();
	const href = new URL(count?.getAttribute('href') ?? '', 'http://localhost');
	expect(href.pathname).toBe('/people/p-1');
	expect(href.searchParams.get('in')).toBe('Pictures/Shoot 1');
	expect(href.searchParams.get('enriched')).toBe('folder');
	expect(count?.textContent?.trim()).toBe(`${(7007).toLocaleString()} files`);
});

it('names a library folder itself by its own name, since its path is empty', async () => {
	mocks.filed.mockResolvedValue([folder(1, { path: '', folder: 'Library One' })]);

	await draw();

	const href = new URL(
		host.querySelector('a.count')?.getAttribute('href') ?? '',
		'http://localhost'
	);
	expect(href.searchParams.get('in')).toBe('Library One');
});

/* The portrait's address names the chosen moment, so the server may let the browser keep it
   (`kernel/covers.py names_its_cover`). Without the moment it is re-checked on every visit. */
it("addresses the portrait by the person's chosen moment", async () => {
	mocks.filed.mockResolvedValue([
		folder(1, { cover_asset_id: 'a1', cover_at_ms: 900, art: 'stamp' })
	]);
	await draw();
	expect(host.querySelector('.portrait img')?.getAttribute('src')).toBe(
		'/api/people/p-1/cover?v=stamp.a1.900'
	);
});

/*
 * Take back on a row: the folder and the person it was added to go to the server, and the toast
 * says what came off with the record's Undo. Every row has one, however old its folder is.
 */
it("takes a row's folder back from its person and offers the Undo", async () => {
	mocks.filed.mockResolvedValue([folder(1), folder(2)]);
	mocks.takeBack.mockResolvedValue({ files: 3, decision_id: 'd-1' });

	await draw();

	const presses = [...host.querySelectorAll<HTMLButtonElement>('button')].filter((one) =>
		one.textContent?.includes('Undo')
	);
	expect(presses).toHaveLength(2);
	expect(presses[1].getAttribute('aria-label')).toBe(
		'Undo adding Pictures/Shoot 2 to Neve Arbogast'
	);
	presses[1].click();
	await tick();
	await tick();

	expect(mocks.takeBack).toHaveBeenCalledWith('f-2', 'p-1');
	expect(mocks.decided).toHaveBeenCalledWith(
		['Removed 3 files from ', { text: 'Neve Arbogast', kind: 'person', id: 'p-1' }],
		'd-1',
		expect.anything()
	);
});
