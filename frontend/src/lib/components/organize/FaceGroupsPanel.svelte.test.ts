/*
 * Unnamed faces's small groups: one chip on the tab line, in the same spot both ways.
 *
 * The way into the small groups and the way back out are one chip reported to the tab line through
 * `ontools`, the slot every panel shares, so the control does not move when pressed. Pinned here is
 * the panel's half: it reports the chip whenever there is somewhere to lead, reports nothing on
 * Ignored, takes it back as it goes, and draws nothing at the list's head or foot. The route's half
 * (drawing what lands at the far end of the tab line) is pinned beside the route.
 */
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount, type Snippet } from 'svelte';

import Panel from './FaceGroupsPanel.svelte';
import panelSource from './FaceGroupsPanel.svelte?raw';
import groupsSource from '../faces/FaceGroups.svelte?raw';
import type { PagerProps } from '$lib/components/common/Pager.svelte';
import { noServerAt } from '../../../test-setup';

/* Left unanswered on purpose: the people list and how well each is known, read on the way past. */
noServerAt(
	'/api/people',
	'/api/faces/references/strength',
	'/api/faces/fingerprints/offers',
	'/api/faces/work-left'
);

const mocks = vi.hoisted(() => ({
	toCheck: vi.fn(),
	replaced: [] as string[],
	at: {
		params: { queue: 'faces-to-name' },
		url: new URL('http://sift.test/organize/faces-to-name')
	} as { params: { queue: string }; url: URL }
}));

vi.mock('$app/state', () => ({
	page: {
		get params() {
			return mocks.at.params;
		},
		get url() {
			return mocks.at.url;
		},
		state: {}
	},
	navigating: { to: null, from: null, type: null, complete: null, delta: null, willUnload: false }
}));

/* The anchor is written with the router's `replaceState`; what it was handed is what the address
   bar, and so the browser's Back, would carry. */
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

let host: HTMLElement;
let panel: Record<string, unknown> | undefined;
const reported: (Snippet | null)[] = [];
const pagers: (PagerProps | null)[] = [];

async function render(queue: string, search = '') {
	mocks.at.params = { queue };
	mocks.at.url = new URL(`http://sift.test/organize/${queue}${search}`);
	host = document.createElement('div');
	document.body.append(host);
	panel = mount(Panel, {
		target: host,
		props: {
			ontools: (tools: Snippet | null) => void reported.push(tools),
			onpaging: (pager: PagerProps | null) => void pagers.push(pager)
		}
	});
	flushSync();
	for (let turn = 0; turn < 4; turn += 1) await Promise.resolve();
	flushSync();
}

beforeEach(() => {
	reported.length = 0;
	pagers.length = 0;
	mocks.replaced.length = 0;
	mocks.toCheck.mockReset();
	mocks.toCheck.mockResolvedValue({ items: [], total: 0, offset: 0, small_groups: 4171 });
});

afterEach(() => {
	if (panel) void unmount(panel);
	panel = undefined;
	host?.remove();
});

it('reports the chip to the tab line while there are small groups to open', async () => {
	await render('faces-to-name');

	expect(reported.at(-1)).toBeTypeOf('function');
	// And neither the list's head nor its foot draws anything.
	expect(host.querySelector('.rest')).toBeNull();
	expect(host.querySelector('.narrowing')).toBeNull();
	expect(host.textContent).not.toContain('small groups');
});

it('reports the same chip on the small groups, where it leads back', async () => {
	await render('faces-to-name', '?show=small');

	expect(reported.at(-1)).toBeTypeOf('function');
	expect(host.textContent).not.toContain('Back to the groups');
});

it('reports nothing where there is nowhere for it to lead', async () => {
	mocks.toCheck.mockResolvedValue({ items: [], total: 0, offset: 0, small_groups: 0 });

	await render('faces-to-name');

	expect(reported.filter((one) => one !== null)).toEqual([]);
});

it('reports nothing on Discarded, and takes the chip back as it goes', async () => {
	await render('discarded-faces');
	expect(reported.filter((one) => one !== null)).toEqual([]);

	void unmount(panel as Record<string, unknown>);
	host.remove();
	reported.length = 0;
	await render('faces-to-name');
	expect(reported.at(-1)).toBeTypeOf('function');
	void unmount(panel as Record<string, unknown>);
	panel = undefined;
	flushSync();

	expect(reported.at(-1)).toBeNull();
});

/*
 * The place kept on the way back.
 *
 * Opening a group from a later page of Unnamed faces and pressing the crumb or Back must return to
 * that page. The first group on screen is written as `from`, and the way back asks for the page
 * that group is on once: the landing answers from the rows it was handed (see `CardPaging.land`).
 */

const LISTED = 135;

/** The server's answer, with every group named after its place so `from` resolves as it does. */
function listing(query: { limit: number; offset?: number; from?: string }) {
	const offset = query.from ? Number(query.from.replace('g', '')) : (query.offset ?? 0);
	const length = Math.max(0, Math.min(query.limit, LISTED - offset));
	return {
		items: Array.from({ length }, (_x, i) => ({
			kind: 'group',
			id: `g${offset + i}`,
			size: 9,
			status: 'open',
			faces: []
		})),
		total: LISTED,
		offset,
		small_groups: 4171
	};
}

it('comes back to the page it was left on, asking once, with the pager saying where', async () => {
	mocks.toCheck.mockImplementation(async (query) => listing(query));

	await render('faces-to-name', '?from=g48');

	expect(mocks.toCheck.mock.calls.map((call) => call[0])).toEqual([{ limit: 24, from: 'g48' }]);
	expect(mocks.toCheck.mock.calls[0][2]).toBe('group');
	// The pager's own figure: "49-72 of 135", not "1-24".
	expect(pagers.at(-1)).toMatchObject({ offset: 48, shown: 24, total: LISTED });
	expect(host.textContent).not.toContain('g0');
});

it('writes the first group on screen to the address, so Back and the crumb can find it', async () => {
	mocks.toCheck.mockImplementation(async (query) => listing(query));

	await render('faces-to-name');
	(pagers.at(-1) as PagerProps).onnext();
	for (let turn = 0; turn < 6; turn += 1) await Promise.resolve();
	flushSync();

	// Asked by offset for the turn: an anchor is only how the FIRST page of a visit is found.
	expect(mocks.toCheck.mock.calls.map((call) => call[0])).toEqual([
		{ limit: 24, offset: 0 },
		{ limit: 24, offset: 24 }
	]);
	expect(mocks.replaced.at(-1)).toBe('/organize/faces-to-name?from=g24&near=24');
});

it('keeps the small groups in the anchor it writes, so the way back reopens them', async () => {
	mocks.toCheck.mockImplementation(async (query) => listing(query));

	await render('faces-to-name', '?show=small&from=g24');

	expect(mocks.toCheck.mock.calls.map((call) => [call[0], call[1]])).toEqual([
		[{ limit: 24, from: 'g24' }, 'small']
	]);
	expect(pagers.at(-1)).toMatchObject({ offset: 24, noun: 'small groups' });
});

it('pages groups by a noun the empty pager can say: "No unnamed groups"', async () => {
	await render('faces-to-name');
	await vi.waitFor(() => expect(pagers.at(-1)).toMatchObject({ noun: 'unnamed groups' }));
});

it('measures the wall it hands its rows to, so a page is whole rows of it', () => {
	/* The host pages, so the host measures: a fixed page under seven columns ends on a row of three. */
	expect(panelSource).toContain("new CardPaging(PAGE, 'faces.unnamed')");
	expect(panelSource).toContain('measure={paging.cards}');
	expect(groupsSource).toContain('<CardWall cards={handed ? measure : paging.cards}>');
});

it('says groups appear as the scan goes on while a scan still has files to look at', async () => {
	mocks.toCheck.mockResolvedValue({ items: [], total: 0, offset: 0, small_groups: 0 });
	const answered = vi.fn(async (input: RequestInfo | URL) => {
		const url = new URL(String(input instanceof Request ? input.url : input), 'http://sift.test');
		if (url.pathname !== '/api/faces/work-left') throw new TypeError('not listening');
		return new Response(JSON.stringify({ files: 1234, moments: 2000 }), {
			headers: { 'content-type': 'application/json' }
		});
	});
	vi.stubGlobal('fetch', answered);
	try {
		await render('faces-to-name');
		for (let turn = 0; turn < 4; turn += 1) await Promise.resolve();
		flushSync();
	} finally {
		vi.unstubAllGlobals();
	}

	expect(host.textContent).toContain('Sift is still looking for faces in 1,234 files.');
	expect(host.textContent).toContain('Groups appear here as the scan goes on.');
	expect(host.textContent).not.toContain('Faces Sift doesn');
});

it('says what the tab is for once no scan has files left', async () => {
	mocks.toCheck.mockResolvedValue({ items: [], total: 0, offset: 0, small_groups: 0 });

	await render('faces-to-name');

	expect(host.textContent).toContain('Faces Sift doesn');
	expect(host.textContent).not.toContain('as the scan goes on');
});
