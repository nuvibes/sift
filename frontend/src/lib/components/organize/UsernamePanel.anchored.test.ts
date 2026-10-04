// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * USERNAMES WAITING KEEPS ITS PAGE IN ITS ADDRESS, the way every other Organize list does.
 *
 * The queue is left all the time (for a username's files, or for a person picked for it), so it
 * pages through the shared anchor (`$lib/grid/anchor`): the first card of the page on screen goes
 * into the address as `from`, with the place it was at as `near`, and an arrival asks for the page
 * BY that username. Answering the page's first card is exactly what takes it off the queue, so
 * the server then serves where the page was (`resume_at`); the stand-in below resolves a `from`
 * the same way.
 *
 * Through the real `usernames` client and a stand-in for the transport, so what is proved is the
 * request that actually leaves: `from` and `near` beside `unattached`, never an offset.
 */
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';

import type { PagerProps } from '$lib/components/common/Pager.svelte';

const TOTAL = 150;
const mocks = vi.hoisted(() => ({
	get: vi.fn(),
	replaceState: vi.fn(),
	at: { url: new URL('http://localhost/organize/usernames') }
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

/* Two draws of sixty cards each: under coverage instrumentation the transform alone takes the
   default five seconds, and the test is about the address, not the speed. */
vi.setConfig({ testTimeout: 20_000 });
import UsernamePanel from './UsernamePanel.svelte';

/* The queue as the server answers it: each username named after its place, so the stand-in
   resolves a `from` the way the route does: its place while it waits, else `near`, else 0. */
function answering(gone: string | null = null): void {
	mocks.get.mockImplementation(
		async (_path: string, options: { query: Record<string, string> }) => {
			const query = options.query;
			const rows = Array.from(
				{ length: TOTAL },
				(_x, at) => `u${String(at).padStart(3, '0')}`
			).filter((one) => one !== gone);
			let offset = Number(query.offset ?? 0);
			if (query.from !== undefined) {
				const found = rows.indexOf(query.from);
				offset = found >= 0 ? found : Number(query.near ?? 0);
			}
			const limit = Number(query.limit);
			return {
				items: rows.slice(offset, offset + limit).map((id) => ({
					id,
					username: `name${id}`,
					display_name: null,
					asset_count: 2,
					site_name: 'SomeSite',
					person_id: null,
					name_candidates: 0
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
	mocks.at.url = new URL('http://localhost/organize/usernames');
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
	drawn = mount(UsernamePanel, {
		target: host,
		props: { onpaging: (reported: PagerProps | null) => (pager = reported) }
	}) as Record<string, unknown>;
	await settle();
}

function written(): string | undefined {
	return mocks.replaceState.mock.calls.at(-1)?.[0] as string | undefined;
}

it('writes the first username of the page it turned to into the address, and opens there again', async () => {
	await draw();
	(pager as unknown as PagerProps).onnext();
	await settle();

	// The first card of page two, and where it was, on the address it is standing on.
	expect(written()).toBe('/organize/usernames?from=u060&near=60');

	// Back to it, from the username's files, or after picking somebody: the page is asked for BY
	// that username, still only among the ones waiting, and drawn.
	unmount(drawn as Record<string, unknown>);
	drawn = null;
	mocks.get.mockClear();
	mocks.at.url = new URL('http://localhost/organize/usernames?from=u060&near=60');
	await draw();

	expect(mocks.get).toHaveBeenCalledWith('/usernames', {
		query: { unattached: 'true', limit: '60', from: 'u060', near: '60' }
	});
	expect((pager as unknown as PagerProps).offset).toBe(60);
	expect(host.textContent).toContain('nameu060');
});

it('opens where the page was when the username the address names has been answered since', async () => {
	/* Saying who the page's first username is takes it off the queue: the ordinary way the row
	   goes. The cards after it close up, so the same place is the same page with the gap filled. */
	answering('u060');
	mocks.at.url = new URL('http://localhost/organize/usernames?from=u060&near=60');
	await draw();

	expect((pager as unknown as PagerProps).offset).toBe(60);
	expect(host.textContent).toContain('nameu061');
	expect(written()).toBe('/organize/usernames?from=u061&near=60');
});
