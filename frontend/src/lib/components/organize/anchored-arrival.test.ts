// SPDX-License-Identifier: AGPL-3.0-or-later
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount, type Component } from 'svelte';

import { forgetMeasurements } from '$lib/grid/cards.svelte';
import { measuring } from '$lib/grid/measuring';

const TOTAL = 400;
const asks = vi.hoisted(() => [] as Array<{ path: string; query: Record<string, unknown> }>);
const at = vi.hoisted(() => ({
	url: new URL('http://localhost/organize/folders'),
	params: {} as Record<string, string>
}));

vi.mock('$app/state', () => ({
	page: {
		get url() {
			return at.url;
		},
		get params() {
			return at.params;
		},
		state: {}
	},
	navigating: { to: null, from: null, type: null, complete: null, delta: null, willUnload: false }
}));
vi.mock('$app/navigation', () => ({ goto: vi.fn(), replaceState: () => {}, pushState: () => {} }));
vi.mock('$lib/library/changes.svelte', async () => ({
	...(await vi.importActual<typeof import('$lib/library/changes.svelte')>(
		'$lib/library/changes.svelte'
	)),
	reloadOnLibraryChange: () => {}
}));

/* The row is named after its position, so the stand-in resolves an anchor as the server does. */
function window(query: Record<string, unknown>): { offset: number; ids: string[] } {
	const offset = query.from ? Number(String(query.from).replace('r', '')) : Number(query.offset);
	const length = Math.max(0, Math.min(Number(query.limit), TOTAL - offset));
	return { offset, ids: Array.from({ length }, (_x, i) => `r${offset + i}`) };
}

function face(id: string) {
	return {
		track_id: id,
		asset_id: `asset-${id}`,
		started_ms: 0,
		ended_ms: 0,
		art: 'a',
		person_id: 'person-1',
		person_name: 'Wren Halloway',
		confidence: null,
		attribution: 'suggested',
		is_reference: false
	};
}

vi.mock('$lib/api/client', async (importActual) => ({
	...(await importActual<typeof import('$lib/api/client')>()),
	api: {
		get: vi.fn(async (path: string, options?: { query?: Record<string, unknown> }) => {
			const query = options?.query ?? {};
			const listed = /^\/(suggestions|faces\/groups(\/[^/]+)?|faces\/identified\/people\/[^/]+)$/;
			if (!listed.test(path)) return {};
			asks.push({ path, query: { ...query } });
			const { offset, ids } = window(query);
			if (path === '/suggestions')
				return {
					proposals: ids.map((id) => ({
						id,
						kind: 'person',
						proposed: `folder ${id}`,
						evidence: 'folder_name',
						folder: `Folder ${id}`,
						folder_id: `f-${id}`,
						path: `Models/${id}`,
						files: 3,
						art: {},
						group_id: null,
						face_id: null,
						face_art: null,
						near_miss: null,
						site: null,
						handle: false,
						dissenting: [],
						per_file: [],
						cover: ''
					})),
					total: TOTAL,
					offset
				};
			if (path === '/faces/groups')
				return {
					groups: ids.map((id) => ({ id, status: 'open', size: 2, faces: [face(`${id}-a`)] })),
					total: TOTAL,
					offset
				};
			if (path.startsWith('/faces/groups/'))
				return {
					group: { id: 'pile-1', status: 'open', size: TOTAL, faces: ids.map(face) },
					total: TOTAL,
					offset
				};
			return {
				items: ids.map(face),
				total: TOTAL,
				offset,
				person_name: 'Wren Halloway',
				waiting: TOTAL,
				confirmed: 0,
				matched: 0
			};
		}),
		post: vi.fn(async () => ({})),
		put: vi.fn(async () => ({})),
		del: vi.fn(async () => ({}))
	}
}));

import FaceGroups from '$lib/components/faces/FaceGroups.svelte';
import PileDetail from '$lib/components/faces/PileDetail.svelte';
import IdentifiedForPerson from './IdentifiedForPerson.svelte';
import FolderSuggestions from '$lib/components/suggestions/FolderSuggestions.svelte';

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

async function settle() {
	for (let round = 0; round < 10; round += 1) {
		flushSync();
		await Promise.resolve();
	}
	flushSync();
}

async function visit(wall: Component<never>, address: string, props: Record<string, unknown>) {
	at.url = new URL(`http://localhost${address}`);
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(wall as Component<Record<string, unknown>>, { target: host, props });
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

const WALLS = [
	{
		name: 'Faces to name (the groups)',
		wall: FaceGroups,
		path: '/organize/faces-to-name',
		params: {},
		props: { status: 'open' }
	},
	{
		name: 'a face group',
		wall: PileDetail,
		path: '/organize/faces-to-name/pile-1',
		params: { id: 'pile-1' },
		props: {}
	},
	{
		name: "a person's faces",
		wall: IdentifiedForPerson,
		path: '/organize/known-people/person-1',
		params: { id: 'person-1' },
		props: {}
	},
	{
		name: 'Folders Needing Your Input',
		wall: FolderSuggestions,
		path: '/organize/folders',
		params: {},
		props: {}
	}
] as const;

describe('an anchored arrival on an Organize wall', () => {
	for (const one of WALLS) {
		for (const row of ['r200', 'r0']) {
			it(`asks once on ${one.name} (from=${row})`, async () => {
				const browser = measuring({ width: 150, height: 300 });
				try {
					at.params = one.params;
					await visit(one.wall as Component<never>, one.path, one.props);
					browser.deliver();
					await settle();
					leave();
					asks.length = 0;

					const join = one.path.includes('?') ? '&' : '?';
					await visit(one.wall as Component<never>, `${one.path}${join}from=${row}`, one.props);
					await settle();
					expect(asks.map((ask) => ask.query)).toEqual([expect.objectContaining({ from: row })]);
				} finally {
					browser.done();
				}
			});
		}
	}
});
