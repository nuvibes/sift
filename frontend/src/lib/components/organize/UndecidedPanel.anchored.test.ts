// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * AN ORGANIZE LIST KEEPS ITS PAGE IN ITS ADDRESS.
 *
 * Seven lists under Organize (Duplicates, Copies, Filenames, Linked, the tagger, Undecided and the
 * record of decisions) are left for a person, a file or a chain and come back to the page they
 * were on, because they page through the shared anchor (`$lib/grid/anchor`): the first row of the
 * page on screen goes into the address as `from`, with the place it was at as `near`, and an
 * arrival asks for the page BY that row. This is the mechanism, held once on the plainest of the
 * seven; each list's own route resolves the row the way `resume_at` says.
 */
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';

import type { PagerProps } from '$lib/components/common/Pager.svelte';

const TOTAL = 120;
const mocks = vi.hoisted(() => ({
	get: vi.fn(),
	replaceState: vi.fn(),
	at: { url: new URL('http://localhost/organize/undecided') }
}));

vi.mock('$app/state', () => ({
	page: {
		get url() {
			return mocks.at.url;
		},
		state: {},
		params: {}
	},
	navigating: { to: null, from: null, type: null, complete: null, delta: null, willUnload: false }
}));
vi.mock('$app/navigation', () => ({ goto: vi.fn(), replaceState: mocks.replaceState }));
vi.mock('$lib/api/client', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/client')>()),
	api: { get: mocks.get }
}));

import UndecidedPanel from './UndecidedPanel.svelte';

/* The list as the server answers it: each row named after its place, so the stand-in resolves a
   `from` the way the route does: the row's place when it is on the list, else `near`, else 0. */
function answering(gone: string | null = null): void {
	mocks.get.mockImplementation(
		async (_path: string, options: { query: Record<string, string> }) => {
			const query = options.query;
			const rows = Array.from(
				{ length: TOTAL },
				(_x, at) => `n${String(at).padStart(3, '0')}`
			).filter((one) => one !== gone);
			let offset = Number(query.offset ?? 0);
			if (query.from !== undefined) {
				const found = rows.indexOf(query.from);
				offset = found >= 0 ? found : Number(query.near ?? 0);
			}
			const limit = Number(query.limit);
			return {
				items: rows.slice(offset, offset + limit).map((id) => ({
					subject: 'person',
					id,
					name: `Wren ${id}`,
					candidates: 2,
					seen_at: 0
				})),
				total: rows.length,
				offset
			};
		}
	);
}

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;
let pager: PagerProps | null = null;

beforeEach(() => {
	vi.clearAllMocks();
	mocks.at.url = new URL('http://localhost/organize/undecided');
	answering();
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
	document.body.innerHTML = '';
});

async function settle(): Promise<void> {
	for (let round = 0; round < 6; round += 1) {
		await tick();
		await Promise.resolve();
		flushSync();
	}
}

async function draw(): Promise<void> {
	drawn = mount(UndecidedPanel, {
		target: host,
		props: { onpaging: (reported: PagerProps | null) => (pager = reported) }
	}) as Record<string, unknown>;
	await settle();
}

function written(): string | undefined {
	return mocks.replaceState.mock.calls.at(-1)?.[0] as string | undefined;
}

it('writes the first row of the page it turned to into the address, and opens there again', async () => {
	await draw();
	(pager as unknown as PagerProps).onnext();
	await settle();

	// The first row of page two, and where it was, on the address it is standing on.
	expect(written()).toBe('/organize/undecided?from=n050&near=50');

	// Back to it: the page is asked for BY that row, and drawn.
	unmount(drawn as Record<string, unknown>);
	drawn = null;
	mocks.get.mockClear();
	mocks.at.url = new URL('http://localhost/organize/undecided?from=n050&near=50');
	await draw();

	expect(mocks.get).toHaveBeenCalledWith('/stash-boxes/undecided', {
		query: { limit: '50', from: 'n050', near: '50' }
	});
	expect((pager as unknown as PagerProps).offset).toBe(50);
	expect(host.textContent).toContain('Wren n050');
});

it('opens where the page was when the row the address names has been decided since', async () => {
	/* Choosing for the page's first name is what takes it off the list: the ordinary way the row
	   goes. The rows after it close up, so the same place is the same page with the gap filled. */
	answering('n050');
	mocks.at.url = new URL('http://localhost/organize/undecided?from=n050&near=50');
	await draw();

	expect((pager as unknown as PagerProps).offset).toBe(50);
	expect(host.textContent).toContain('Wren n051');
	expect(written()).toBe('/organize/undecided?from=n051&near=50');
});
