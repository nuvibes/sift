/*
 * The Disagreements tab, gathered by person.
 *
 * One row per person, the most files first; the chosen person's faces as a wall beside them (the
 * address names her, else the one with the most); and the answers: one face's Yes and No, and a
 * Yes or No over the page, each sent as the files it is about. A thousand one-file cards about a
 * handful of people would ask one question a thousand times; these pin the shape that asks it once.
 */
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import Panel from './DisagreementsPanel.svelte';
import type { PagerProps } from '$lib/components/common/Pager.svelte';

const mocks = vi.hoisted(() => ({
	people: vi.fn(),
	of: vi.fn(),
	answer: vi.fn(),
	decided: vi.fn(),
	goto: vi.fn(),
	at: { url: new URL('http://sift.test/organize/disagreements') } as { url: URL }
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

vi.mock('$app/navigation', () => ({ goto: mocks.goto, pushState: vi.fn(), replaceState: vi.fn() }));

vi.mock('$lib/organize/disagreements', async (importOriginal) => ({
	...(await importOriginal<Record<string, unknown>>()),
	disagreeingPeople: mocks.people,
	disagreementsOf: mocks.of,
	answerDisagreements: mocks.answer
}));

vi.mock('$lib/organize/organize.svelte', async (importOriginal) => ({
	...(await importOriginal<Record<string, unknown>>()),
	decided: mocks.decided
}));

vi.mock('$lib/library/changes.svelte', async () => ({
	...(await vi.importActual<typeof import('$lib/library/changes.svelte')>(
		'$lib/library/changes.svelte'
	)),
	reloadOnLibraryChange: () => {}
}));

const PEOPLE = [
	{
		person_id: 'p1',
		person_name: 'Wren Halloway',
		count: 3,
		source: 'folder',
		filed: 'Added from the names of 4 folders: Summer, Beach, Archive and 1 more',
		filed_links: [
			{ kind: 'folder', id: 'Summer', name: 'Summer', href: '/browse?folders=f1', gone: false },
			{ kind: 'folder', id: 'Beach', name: 'Beach', href: '/browse?folders=f2', gone: false },
			{ kind: 'folder', id: 'Archive', name: 'Archive', href: '/browse?folders=f3', gone: false }
		]
	},
	{ person_id: 'p2', person_name: 'Rasha Emberlin', count: 1, source: 'stash_box' },
	{
		person_id: 'p3',
		person_name: 'Ilsa Marrow',
		count: 1,
		source: 'stash_box',
		filed: 'Added by Northlight',
		filed_links: []
	}
];

function hers(personId: string, count: number) {
	return {
		items: Array.from({ length: count }, (_x, i) => ({
			kind: 'mismatch',
			id: `${personId}-a${i}`,
			size: 1,
			person_id: personId,
			source: 'folder',
			faces: [
				{ track_id: `${personId}-f${i}`, asset_id: `${personId}-a${i}`, started_ms: 0, ended_ms: 0 }
			]
		})),
		total: count,
		offset: 0
	};
}

let host: HTMLElement;
let panel: Record<string, unknown> | undefined;
const pagers: (PagerProps | null)[] = [];

async function settle() {
	for (let turn = 0; turn < 8; turn += 1) await Promise.resolve();
	flushSync();
}

async function render(search = '') {
	mocks.at.url = new URL(`http://sift.test/organize/disagreements${search}`);
	host = document.createElement('div');
	document.body.append(host);
	panel = mount(Panel, {
		target: host,
		props: { onpaging: (pager: PagerProps | null) => void pagers.push(pager) }
	});
	flushSync();
	await settle();
}

function press(label: string) {
	const button = host.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`);
	if (!button) throw new Error(`no button named ${label}`);
	button.click();
}

beforeEach(() => {
	pagers.length = 0;
	for (const mock of [mocks.people, mocks.of, mocks.answer, mocks.decided, mocks.goto]) {
		mock.mockReset();
	}
	mocks.people.mockResolvedValue({ people: PEOPLE, total: 4 });
	mocks.of.mockImplementation(async (personId: string) =>
		hers(personId, personId === 'p1' ? 3 : 1)
	);
	mocks.answer.mockResolvedValue({ changed: 1, skipped: 0, decision_id: 'receipt-1' });
});

afterEach(() => {
	if (panel) void unmount(panel);
	panel = undefined;
	host?.remove();
});

it('draws one row per person and opens on the one with the most, a page of her faces', async () => {
	await render();

	const rows = [...host.querySelectorAll('.people li')].map((row) => row.textContent ?? '');
	expect(rows[0]).toContain('Wren Halloway');
	expect(rows[0]).toContain('3 files');
	expect(rows[0]).toContain('Added from a folder name');
	expect(rows[1]).toContain('Added by a stash-box');
	expect(mocks.of).toHaveBeenCalledWith('p1', { limit: 60, offset: 0 });
	expect(host.querySelectorAll('.faces li')).toHaveLength(3);
	expect(host.textContent).toContain('Do these faces look like Wren Halloway?');
	expect(pagers.at(-1)).toMatchObject({ offset: 0, shown: 3, total: 3, noun: 'files' });
});

it('shows the person the address names, and a press on another row moves the address', async () => {
	await render('?person=p2');

	expect(mocks.of).toHaveBeenCalledWith('p2', { limit: 60, offset: 0 });
	expect(host.querySelectorAll('.faces li')).toHaveLength(1);

	host.querySelector<HTMLButtonElement>('button[aria-label="Wren Halloway, 3 files"]')?.click();
	expect(String(mocks.goto.mock.calls.at(-1)?.[0])).toContain('person=p1');
});

it("answers one face by its own file, and says what moved with the receipt's Undo", async () => {
	await render();

	press('No, take Wren Halloway off this file');
	await settle();

	expect(mocks.answer).toHaveBeenCalledWith('p1', false, 'picked', ['p1-a0']);
	expect(mocks.decided).toHaveBeenCalledWith(
		'Wren Halloway is no longer on this file',
		'receipt-1',
		expect.anything()
	);
});

it('answers the page as the files on it', async () => {
	await render();

	[...host.querySelectorAll<HTMLButtonElement>('button')]
		.find((button) => button.textContent?.includes('Yes, these 3 are Wren Halloway'))
		?.click();
	await settle();

	expect(mocks.answer).toHaveBeenCalledWith('p1', true, 'page', ['p1-a0', 'p1-a1', 'p1-a2']);
});

it('picks faces by the walls own gesture, and the answers are about the ones picked', async () => {
	await render();
	const tiles = [
		...host.querySelectorAll<HTMLButtonElement>(
			'button[aria-label="Open the file where this face was found"]'
		)
	];

	// Every tile says which face it is, which is what the press-and-drag reads as it crosses them.
	expect(tiles.map((tile) => tile.getAttribute('data-tile-id'))).toEqual([
		'p1-a0',
		'p1-a1',
		'p1-a2'
	]);
	for (const tile of [tiles[0], tiles[2]]) {
		tile.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, ctrlKey: true }));
	}
	flushSync();

	expect(tiles.map((tile) => tile.getAttribute('aria-pressed'))).toEqual(['true', null, 'true']);
	const lead = [...host.querySelectorAll<HTMLButtonElement>('button')].find((button) =>
		button.textContent?.includes('Yes, these 2 are Wren Halloway')
	);
	expect(lead).toBeDefined();
	lead?.click();
	await settle();

	expect(mocks.answer).toHaveBeenCalledWith('p1', true, 'picked', ['p1-a0', 'p1-a2']);
});

it('names the folders the server names, each a way to it in the folder view', async () => {
	await render();
	const detail = host.querySelector('.detail');

	expect(detail?.textContent).toContain(
		'Added from the names of 4 folders: Summer, Beach, Archive and 1 more'
	);
	expect(
		[...(detail?.querySelectorAll('a') ?? [])].map((link) => [
			link.textContent,
			link.getAttribute('href')
		])
	).toEqual([
		['Summer', '/browse?folders=f1'],
		['Beach', '/browse?folders=f2'],
		['Archive', '/browse?folders=f3']
	]);
});

it('keeps the word for where the name came from when the server sends no sentence', async () => {
	await render('?person=p2');

	expect(host.querySelector('.detail')?.textContent).toContain('Added by a stash-box');
});

it('names the box where the server says which, in her row and under her heading', async () => {
	await render('?person=p3');

	const rows = [...host.querySelectorAll('.people li')].map((row) => row.textContent ?? '');
	expect(rows[2]).toContain('Added by Northlight');
	expect(host.querySelector('.detail')?.textContent).toContain(
		'1 file \u00b7 Added by Northlight. Sift recognized someone else in these.'
	);
});

it('says nothing disagrees when nobody is on the list', async () => {
	mocks.people.mockResolvedValue({ people: [], total: 0 });
	await render();

	expect(host.textContent).toContain('Nothing disagrees');
	expect(mocks.of).not.toHaveBeenCalled();
});
