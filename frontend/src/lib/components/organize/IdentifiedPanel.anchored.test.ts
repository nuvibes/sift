// SPDX-License-Identifier: AGPL-3.0-or-later
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import { forgetMeasurements } from '$lib/grid/cards.svelte';
import { measuring } from '$lib/grid/measuring';

const TOTAL = 90;
const asks = vi.hoisted(() => [] as Record<string, unknown>[]);
const at = vi.hoisted(() => ({ url: new URL('http://localhost/organize/known-people') }));

vi.mock('$app/state', () => ({
	page: {
		get url() {
			return at.url;
		},
		state: {},
		params: {}
	},
	navigating: { to: null, from: null, type: null, complete: null, delta: null, willUnload: false }
}));

vi.mock('$lib/people/faces.svelte', () => ({
	CROPS_ON_A_CARD: 12,
	IDENTIFIED_PEOPLE_PER_PAGE: 24,
	identifiedPeople: vi.fn(async (query: Record<string, unknown>) => {
		asks.push({ ...query });
		// The row is named after its position, so the stand-in resolves an anchor as the server does.
		const offset = query.from ? Number(String(query.from).replace('p', '')) : Number(query.offset);
		const limit = Number(query.limit);
		return {
			people: Array.from({ length: Math.max(0, Math.min(limit, TOTAL - offset)) }, (_x, i) => ({
				person_id: `p${offset + i}`,
				person_name: `Wren ${offset + i}`,
				size: 3,
				waiting: 0,
				matched: 0,
				confirmed: 3,
				surest: null,
				faces: []
			})),
			total: TOTAL,
			offset
		};
	}),
	confirmMatches: vi.fn(),
	confirmLookAlikes: vi.fn(),
	rejectLookAlikes: vi.fn(),
	rejectMatches: vi.fn(),
	referenceStrengths: vi.fn(async () => ({
		people: {},
		verdicts: {},
		target: 20,
		floor: 5,
		strong: 10
	})),
	recognitionOf: vi.fn(),
	referenceVerdict: () => 'good',
	cropUrl: (id: string) => `/api/faces/${id}/crop`,
	faceCoverUrl: (id: string) => `/api/faces/${id}/cover`,
	blankOnRefusal: () => {}
}));
vi.mock('$lib/shell/toasts.svelte', () => ({ toasts: { show: () => {} } }));
vi.mock('$app/navigation', () => ({ goto: vi.fn(), replaceState: () => {} }));
vi.mock('$lib/library/changes.svelte', async () => ({
	...(await vi.importActual<typeof import('$lib/library/changes.svelte')>(
		'$lib/library/changes.svelte'
	)),
	reloadOnLibraryChange: () => {}
}));

import IdentifiedPanel from './IdentifiedPanel.svelte';

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

async function settle() {
	for (let round = 0; round < 8; round += 1) {
		flushSync();
		await Promise.resolve();
	}
	flushSync();
}

async function visit(address: string) {
	at.url = new URL(`http://localhost${address}`);
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(IdentifiedPanel, { target: host }) as Record<string, unknown>;
	await settle();
}

function leave() {
	if (mounted) void unmount(mounted);
	mounted = null;
	host?.remove();
}

beforeEach(() => {
	asks.length = 0;
	forgetMeasurements();
	Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
		configurable: true,
		get: () => 1200
	});
});

afterEach(() => {
	leave();
	document.body.innerHTML = '';
});

describe('an anchored arrival on People Sift can recognize', () => {
	for (const [row, why] of [
		['p30', 'the row sits two pages in, so the offset moves when it lands'],
		['p0', 'the row is on the page already open, so only the anchor is cleared']
	] as const) {
		it(`asks for the anchored page once (${why})`, async () => {
			const browser = measuring({ width: 150, height: 300 });
			try {
				await visit('/organize/known-people');
				browser.deliver();
				await settle();
				leave();
				asks.length = 0;

				await visit(`/organize/known-people?from=${row}`);
				await settle();
				expect(asks).toHaveLength(1);
				expect(asks[0].from).toBe(row);
				expect(host.textContent).toContain(`Wren ${row.slice(1)}`);
			} finally {
				browser.done();
			}
		});
	}
});
