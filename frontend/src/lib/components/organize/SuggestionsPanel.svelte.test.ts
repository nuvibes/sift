/*
 * The Suggestions tab keeps its place on the way back.
 *
 * Opening a row from a later page and pressing the crumb back must return to that page. It pages as
 * Unnamed faces does (`FaceGroupsPanel.svelte.test.ts` is the same shape): the first row on screen
 * is written as `from`, and the way back asks for the page that row is on once, because the landing
 * answers from the rows it was handed (`CardPaging.land`).
 */
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import { words } from '$lib/design/testing.svelte';

import Panel from './SuggestionsPanel.svelte';
import panelSource from './SuggestionsPanel.svelte?raw';
import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import type { PagerProps } from '$lib/components/common/Pager.svelte';

const mocks = vi.hoisted(() => ({
	toCheck: vi.fn(),
	replaced: [] as string[],
	at: { url: new URL('http://sift.test/organize/faces') } as { url: URL }
}));

vi.mock('$app/state', () => ({
	page: {
		params: {},
		get url() {
			return mocks.at.url;
		},
		state: {}
	},
	navigating: { to: null, from: null, type: null, complete: null, delta: null, willUnload: false }
}));

/* The anchor is written with the router's `replaceState`; what it was handed is what the address
   bar, and so the browser's Back and the crumb, would carry. */
vi.mock('$app/navigation', () => ({
	goto: vi.fn(),
	pushState: vi.fn(),
	replaceState: (url: string) => void mocks.replaced.push(url)
}));

vi.mock('$lib/people/faces.svelte', async (importOriginal) => ({
	...(await importOriginal<Record<string, unknown>>()),
	toCheck: mocks.toCheck
}));

vi.mock('$lib/library/changes.svelte', async () => ({
	...(await vi.importActual<typeof import('$lib/library/changes.svelte')>(
		'$lib/library/changes.svelte'
	)),
	reloadOnLibraryChange: () => {}
}));

const LISTED = 60;

/** The server's answer, every row named after its place so `from` resolves as it does there. */
function listing(query: { limit: number; offset?: number; from?: string }) {
	const offset = query.from ? Number(query.from.replace('p', '')) : (query.offset ?? 0);
	const length = Math.max(0, Math.min(query.limit, LISTED - offset));
	return {
		items: Array.from({ length }, (_x, i) => ({
			kind: 'person',
			id: `p${offset + i}`,
			size: 1,
			person_id: 'person-1',
			person_name: 'Ada Lovelace',
			source: 'folder',
			best: 0.7,
			faces: [
				{ track_id: `face-${offset + i}`, asset_id: `a${offset + i}`, started_ms: 0, ended_ms: 0 }
			]
		})),
		total: LISTED,
		offset,
		small_groups: 0
	};
}

let host: HTMLElement;
let panel: Record<string, unknown> | undefined;
const pagers: (PagerProps | null)[] = [];

async function render(search = '') {
	mocks.at.url = new URL(`http://sift.test/organize/faces${search}`);
	host = document.createElement('div');
	document.body.append(host);
	panel = mount(Panel, {
		target: host,
		props: { onpaging: (pager: PagerProps | null) => void pagers.push(pager) }
	});
	flushSync();
	for (let turn = 0; turn < 6; turn += 1) await Promise.resolve();
	flushSync();
}

beforeEach(() => {
	pagers.length = 0;
	mocks.replaced.length = 0;
	mocks.toCheck.mockReset();
	mocks.toCheck.mockImplementation(async (query) => listing(query));
});

afterEach(() => {
	if (panel) void unmount(panel);
	panel = undefined;
	host?.remove();
});

it('asks each question in the one card shape, naming the person once', async () => {
	/* The question names her; the line under it says what she was compared with and how close the
	   best came, without the name a second time. A card of one face holds the room of two rows of
	   six, so every question card is one height. */
	await render();

	const card = host.querySelector('.people > li');
	expect(card?.querySelector('.section-heading')?.textContent?.trim()).toBe(
		'Does this one face look like Ada Lovelace?'
	);
	expect(card?.querySelector('.detail')?.textContent).toBe(
		'Compared with the faces already named. Surest at 70%'
	);
	expect(card?.querySelectorAll('.faces > *')).toHaveLength(12);
	expect(words(card?.querySelector('.foot .lead button'))).toBe('Yes');
});

it('comes back to the page it was left on, asking once, with the pager saying where', async () => {
	await render('?from=p24');

	expect(mocks.toCheck.mock.calls.map((call) => call[0])).toEqual([{ limit: 24, from: 'p24' }]);
	// Both tiers this tab draws: her questions, then the groups that may be her.
	expect(mocks.toCheck.mock.calls[0].slice(1)).toEqual(['waiting', ['person', 'may_be'], '']);
	// The pager's own figure: "25-48 of 60", not "1-24".
	expect(pagers.at(-1)).toMatchObject({
		offset: 24,
		shown: 24,
		total: LISTED,
		noun: 'faces to confirm'
	});
});

it('writes the first row on screen to the address, so Back and the crumb can find it', async () => {
	await render();
	(pagers.at(-1) as PagerProps).onnext();
	for (let turn = 0; turn < 6; turn += 1) await Promise.resolve();
	flushSync();

	// Asked by offset for the turn: an anchor is only how the FIRST page of a visit is found.
	expect(mocks.toCheck.mock.calls.map((call) => call[0])).toEqual([
		{ limit: 24, offset: 0 },
		{ limit: 24, offset: 24 }
	]);
	expect(mocks.replaced.at(-1)).toBe('/organize/faces?from=p24&near=24');
});

it('packs the cards in columns, each at its own height, none split between two', () => {
	/* A card of groups beside a card of faces leaves no dead space under the shorter one. */
	const list = document.createElement('ul');
	list.className = 'people svelte-probe1';
	const question = document.createElement('li');
	question.className = 'svelte-probe1';
	const mayBe = document.createElement('li');
	mayBe.className = 'svelte-probe1';
	list.append(question, mayBe);
	document.body.append(list);
	try {
		applyStyles(panelSource, list);
		expect(getComputedStyle(list).columns).toBe('22rem');
		for (const card of [question, mayBe]) {
			expect(getComputedStyle(card).breakInside).toBe('avoid');
		}
	} finally {
		removeStyles();
		list.remove();
	}
});

it('asks the server for the words in its address, and hands the tab line its search box', async () => {
	const tools: unknown[] = [];
	mocks.at.url = new URL('http://sift.test/organize/faces?who=ada');
	host = document.createElement('div');
	document.body.append(host);
	panel = mount(Panel, {
		target: host,
		props: {
			onpaging: (pager: PagerProps | null) => void pagers.push(pager),
			ontools: (snippet: unknown) => void tools.push(snippet)
		}
	});
	flushSync();
	for (let turn = 0; turn < 6; turn += 1) await Promise.resolve();
	flushSync();

	// Narrowed on the server, so the pager counts what the words found.
	expect(mocks.toCheck.mock.calls[0][3]).toBe('ada');
	expect(typeof tools.at(-1)).toBe('function');
});

it('says a search that found nobody, rather than that nothing is left to confirm', async () => {
	mocks.toCheck.mockImplementation(async () => ({
		items: [],
		total: 0,
		offset: 0,
		small_groups: 0
	}));
	await render('?who=Quill');

	expect(host.textContent).toContain('No people match "Quill".');
	expect(host.textContent).not.toContain('Nothing to confirm');
});

it("opens the person's review on a press anywhere on the card's ground", async () => {
	await render();
	const card = host.querySelector('.people > li');
	const link = card?.querySelector<HTMLAnchorElement>('a.faces');
	expect(link?.getAttribute('href')).toBe('/organize/known-people/p0?show=suggested&via=faces');
	const opened = vi.fn((event: Event) => event.preventDefault());
	link?.addEventListener('click', opened);

	card?.querySelector<HTMLElement>('.detail')?.click();

	expect(opened).toHaveBeenCalledTimes(1);
});
