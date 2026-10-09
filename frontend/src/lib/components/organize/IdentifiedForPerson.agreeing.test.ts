// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * A Yes on a person's own page. Agreeing with her proposals is a task: the press says what it
 * handed over ("Agreeing with N faces") and marks her while it and the re-match after it run.
 * Agreeing with her matches marks her the same way. The server's answers are faked.
 */
import { afterEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

const at = vi.hoisted(() => ({ url: new URL('http://localhost/organize/known-people/person-1') }));
const said = vi.hoisted(() => [] as unknown[]);
const posted = vi.hoisted(() => [] as string[]);

vi.mock('$app/state', () => ({
	page: {
		get url() {
			return at.url;
		},
		params: { id: 'person-1' },
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
vi.mock('$lib/shell/toasts.svelte', () => ({
	toasts: { show: (words: unknown) => said.push(words) }
}));

function face(id: string, attribution: string) {
	return {
		track_id: id,
		asset_id: `asset-${id}`,
		started_ms: 0,
		ended_ms: 0,
		art: 'a',
		person_id: 'person-1',
		person_name: 'Wren Halloway',
		confidence: null,
		attribution,
		is_reference: false
	};
}

vi.mock('$lib/api/client', async (importActual) => ({
	...(await importActual<typeof import('$lib/api/client')>()),
	api: {
		get: vi.fn(async (path: string, options?: { query?: { attribution?: string } }) => {
			if (!path.startsWith('/faces/identified/people/')) return { jobs: [] };
			const kind = options?.query?.attribution ?? 'suggested';
			return {
				items: ['f1', 'f2', 'f3'].map((id) => face(id, kind)),
				total: 3,
				offset: 0,
				person_name: 'Wren Halloway',
				waiting: 3,
				confirmed: 0,
				matched: 3
			};
		}),
		post: vi.fn(async (path: string) => {
			posted.push(path);
			return path.endsWith('/confirm-matches')
				? { confirmed: 3, references: 3 }
				: { changed: 3, person_id: 'person-1', offered: 0, skipped: 0 };
		}),
		put: vi.fn(async () => ({})),
		del: vi.fn(async () => ({}))
	}
}));

import IdentifiedForPerson from './IdentifiedForPerson.svelte';
import { rematching } from '$lib/components/faces/WaitingForYou.svelte';

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

async function settle() {
	for (let round = 0; round < 12; round += 1) {
		flushSync();
		await Promise.resolve();
	}
	flushSync();
}

async function pressYes(show: string) {
	at.url = new URL(`http://localhost/organize/known-people/person-1?show=${show}`);
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(IdentifiedForPerson, { target: host }) as Record<string, unknown>;
	await vi.waitFor(async () => {
		await settle();
		const yes = [...document.querySelectorAll<HTMLButtonElement>('button')].find((one) =>
			(one.textContent ?? '').includes('Yes (')
		);
		expect(yes?.disabled).toBe(false);
	});
	[...document.querySelectorAll<HTMLButtonElement>('button')]
		.find((one) => (one.textContent ?? '').includes('Yes ('))
		?.click();
	await settle();
}

afterEach(() => {
	if (mounted) void unmount(mounted);
	mounted = null;
	host?.remove();
	document.body.innerHTML = '';
	rematching.people.clear();
	said.length = 0;
	posted.length = 0;
});

it('says what a Yes to her proposals handed over, and marks her', async () => {
	await pressYes('suggested');

	expect(posted).toEqual(['/faces/look-alikes/person-1/confirm']);
	expect(said).toContain('Agreeing with 3 faces');
	expect(rematching.people.has('person-1')).toBe(true);
});

it('marks her after a Yes to her matches', async () => {
	await pressYes('matched');

	expect(posted).toEqual(['/faces/people/person-1/confirm-matches']);
	expect(rematching.people.has('person-1')).toBe(true);
});
