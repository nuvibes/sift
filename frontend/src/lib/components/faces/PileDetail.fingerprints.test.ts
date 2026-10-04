// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * A group's own page, when the group looks like somebody a facial fingerprints file holds and
 * making people from fingerprints is off: the page asks the card's question above the group's row,
 * one press makes the person, and then it says who was made as a link to them. Naming the group as
 * somebody else stays on the row below, second to the question. The server's answers are faked.
 */
import { afterEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

vi.mock('$app/state', () => ({
	page: {
		url: new URL('http://localhost/organize/faces-to-name/pile-1'),
		params: { id: 'pile-1' },
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

const offered = vi.hoisted(() => ({ items: [] as unknown[] }));
const posted = vi.hoisted(() => [] as string[]);

vi.mock('$lib/api/client', async (importActual) => ({
	...(await importActual<typeof import('$lib/api/client')>()),
	api: {
		get: vi.fn(async (path: string) => {
			if (path === '/faces/fingerprints/offers') return { items: offered.items };
			if (path.startsWith('/faces/groups/'))
				return {
					group: {
						id: 'pile-1',
						status: 'open',
						size: 1,
						faces: [{ track_id: 't1', asset_id: 'a1', started_ms: 0, ended_ms: 0 }]
					},
					total: 1,
					offset: 0
				};
			return {};
		}),
		post: vi.fn(async (path: string) => {
			posted.push(path);
			return { person_id: 'person-made' };
		}),
		put: vi.fn(async () => ({})),
		del: vi.fn(async () => ({}))
	}
}));

import PileDetail from './PileDetail.svelte';

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

async function visit() {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(PileDetail, { target: host, props: {} }) as Record<string, unknown>;
	for (let round = 0; round < 10; round += 1) {
		flushSync();
		await Promise.resolve();
	}
	flushSync();
}

afterEach(() => {
	if (mounted) void unmount(mounted);
	mounted = null;
	host?.remove();
	offered.items = [];
	posted.length = 0;
});

function said(): string | undefined {
	return host.querySelector('.offer .asks')?.textContent?.replace(/\s+/g, ' ').trim();
}

it('asks to make them a person, then links to the person made', async () => {
	offered.items = [{ entry_id: 'entry-7', name: 'Liora Fenwick', pile_id: 'pile-1' }];
	await visit();
	await vi.waitFor(
		() =>
			expect(said()).toBe(
				'This group looks like Liora Fenwick, from a facial fingerprints file. Create a person for them?'
			),
		{ interval: 1 }
	);

	const press = [...host.querySelectorAll<HTMLButtonElement>('.offer button')].find((one) =>
		one.textContent?.includes('Create a person')
	);
	press?.click();
	await vi.waitFor(() => expect(said()).toBe('Liora Fenwick is a person now'), { interval: 1 });

	expect(posted).toEqual(['/faces/fingerprints/entry-7/person']);
	expect(host.querySelector('.offer .asks a')?.getAttribute('href')).toBe('/people/person-made');
	expect(host.querySelector('.offer button')).toBeNull();
});

it('asks nothing about fingerprints on a group no file looks like', async () => {
	offered.items = [{ entry_id: 'entry-7', name: 'Liora Fenwick', pile_id: 'pile-9' }];
	await visit();
	await vi.waitFor(() => expect(host.textContent).toContain('Add as person'), { interval: 1 });

	expect(host.querySelector('.offer')).toBeNull();
	expect(host.textContent).not.toContain('fingerprints');
});
