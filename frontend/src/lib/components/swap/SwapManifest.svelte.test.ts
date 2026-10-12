/* The Manifest drawn at each step: the rows in filing order with the state on the right, a row kept
 * out in the danger colour with its mark, a group folding open to its files and reading the next
 * page once, the guest's ticks reaching the answer, the after number, the file crossing now, and a
 * swap of one thing said as a sentence. */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';
import { SvelteSet } from 'svelte/reactivity';
import SwapManifest from './SwapManifest.svelte';
import ManifestOffer from './ManifestOffer.svelte';
import SwapProgress from './SwapProgress.svelte';
import SwapToken from './SwapToken.svelte';
import SwapCode from './SwapCode.svelte';
import type { ManifestFile, ManifestGroup, Session } from './wire';

const api = vi.hoisted(() => ({
	groupFiles: vi.fn(),
	weighAnswer: vi.fn(),
	swapTunnels: vi.fn()
}));

vi.mock('./swap', async (real) => ({
	...(await real<typeof import('./swap')>()),
	groupFiles: (...args: unknown[]) => api.groupFiles(...args),
	weighAnswer: (...args: unknown[]) => api.weighAnswer(...args),
	swapTunnels: () => api.swapTunnels()
}));

function group(over: Partial<ManifestGroup> = {}): ManifestGroup {
	return {
		key: 'person:0',
		kind: 'person',
		index: 0,
		name: 'Ava Example',
		match_name: null,
		matched_by: null,
		local_id: null,
		usernames: 0,
		faces: 0,
		files: 612,
		bytes: 17_900_000_000,
		have: null,
		to_take: 612,
		held_same: 0,
		held_look: 0,
		state: 'offered',
		done: 0,
		failed: 0,
		crossing: 0,
		left_out: null,
		sample: [],
		more: 0,
		...over
	};
}

let host: HTMLElement;
let shown: ReturnType<typeof mount> | undefined;

afterEach(() => {
	if (shown) unmount(shown);
	shown = undefined;
	host?.remove();
	vi.clearAllMocks();
});

function draw<P extends Record<string, unknown>>(component: unknown, props: P): HTMLElement {
	host = document.createElement('div');
	document.body.append(host);
	shown = mount(component as never, { target: host, props });
	flushSync();
	return host;
}

function words(where: Element | null): string {
	return (where?.textContent ?? '').replace(/\s+/g, ' ').trim();
}

function file(key: string, over: Partial<ManifestFile> = {}): ManifestFile {
	return {
		key,
		title: `${key}.mp4`,
		kind: 'video',
		size: 1_200_000_000,
		duration_ms: 60_000,
		width: 1920,
		height: 1080,
		held: null,
		...over
	};
}

const OFFERED = [
	group({ key: 'site:0', kind: 'site', index: null, name: 'Northlight Media', usernames: 6 }),
	group({ key: 'person:1', index: 1, name: 'Bryn Calloway', files: 308, to_take: 308 }),
	group({ key: 'person:2', index: 2, name: 'Orla Tennant', left_out: 'local', files: 1200 }),
	group({ sample: [file('a1'), file('a2', { held: 'look' })], more: 150 })
];

function session(over: Partial<Session> = {}): Session {
	return {
		two_way: false,
		answered: false,
		sending: null,
		receiving: null,
		id: 'S1',
		short_id: 'S1',
		role: 'guest',
		state: 'transferring',
		code: 'K7Q2ZP',
		token: null,
		sentence: null,
		peer_device: 'abcd1234efgh5678',
		offer: null,
		offered_files: 0,
		unwanted_files: 0,
		wanted_files: 1066,
		wanted_bytes: 29_600_000_000,
		sent_files: 412,
		sent_bytes: 11_400_000_000,
		received_files: 412,
		rate_bps: 192_800_000,
		end_reason: null,
		ended_at: null,
		rejoin_until: null,
		started_at: 1000,
		...over
	} as Session;
}

describe('the Manifest', () => {
	it('draws the groups in filing order, the kept-out row with its mark', () => {
		const at = draw(SwapManifest, { groups: OFFERED, label: 'What you are offering' });
		const names = [...at.querySelectorAll('.what .name')].map((one) => words(one));
		expect(names).toEqual(['Bryn Calloway', 'Orla Tennant', 'Ava Example', 'Northlight Media']);
		const kept = [...at.querySelectorAll('.what')].find((one) => one.classList.contains('out'));
		expect(words(kept ?? null)).toContain('Kept local \u00b7 1,200 files not offered');
		expect(words(at)).toContain('Site \u00b7 6 Usernames');
	});

	it('folds a group open to its files, and reads the next page once', async () => {
		api.groupFiles.mockResolvedValue({
			files: [file('a3')],
			total: 152
		});
		const at = draw(SwapManifest, {
			groups: [OFFERED[3], OFFERED[1]],
			label: 'L',
			sessionId: 'S1'
		});
		const arrow = at.querySelector<HTMLButtonElement>(
			'button[aria-label="Show more: the files of Ava Example"]'
		);
		arrow?.click();
		flushSync();
		expect(words(at.querySelector('[aria-label="The files of Ava Example"]'))).toContain('a1.mp4');
		expect(words(at.querySelector('[aria-label="The files of Ava Example"]'))).toContain(
			'You have one that looks the same'
		);
		const more = [...at.querySelectorAll('button')].find((one) =>
			words(one).startsWith('Show 100 more of 150')
		);
		more?.click();
		await tick();
		await Promise.resolve();
		flushSync();
		expect(api.groupFiles).toHaveBeenCalledTimes(1);
		expect(api.groupFiles).toHaveBeenCalledWith('S1', 'person:0', 2, 100);
		expect(words(at.querySelector('[aria-label="The files of Ava Example"]'))).toContain('a3.mp4');
	});

	it('says a swap of one thing as a sentence', () => {
		const at = draw(SwapManifest, { groups: [group()], label: 'L' });
		expect(at.querySelector('li')).toBeNull();
		expect(words(at.querySelector('.sentence'))).toBe('Ava Example: 612 files \u00b7 18 GB');
	});

	it('draws the file crossing now on its group, and the states', () => {
		const at = draw(SwapManifest, {
			groups: [
				group({ state: 'landed', done: 409 }),
				group({ key: 'person:1', name: 'Bryn Calloway', state: 'crossing', done: 3, to_take: 308 })
			],
			label: 'L',
			now: { title: 'beach set 03.mp4', size: 1_200_000_000, group: 'person:1' }
		});
		expect(words(at)).toContain('Now: beach set 03.mp4, 1.2 GB');
		expect(words(at)).toContain('Landed');
		expect(words(at)).toContain('3 of 308');
	});

	it('ticks a group off and a file off, into the answer', () => {
		const choose = { skipped: new SvelteSet<string>(), unticked: new SvelteSet<string>() };
		const at = draw(SwapManifest, { groups: OFFERED, label: 'L', choose });
		at.querySelector<HTMLElement>('[aria-label="Take Bryn Calloway"]')?.click();
		flushSync();
		expect([...choose.skipped]).toEqual(['person:1']);
		at.querySelector<HTMLButtonElement>(
			'button[aria-label="Show more: the files of Ava Example"]'
		)?.click();
		flushSync();
		at.querySelector<HTMLElement>('[aria-label="Take a1.mp4"]')?.click();
		flushSync();
		expect([...choose.unticked]).toEqual(['a1']);
		// A kept-out row offers no tick.
		expect(at.querySelector('[aria-label="Take Orla Tennant"]')).toBeNull();
	});
});

describe('the offer as the Manifest', () => {
	it('shows the after number and the held reasons, and sends people by index and others by key', async () => {
		api.swapTunnels.mockResolvedValue([]);
		api.weighAnswer.mockResolvedValue({
			files: 612,
			bytes: 17_900_000_000,
			left_out: [],
			left_out_other: 0
		});
		const ontake = vi.fn();
		const at = draw(ManifestOffer, {
			session: session({
				state: 'offered',
				peer_name: 'DESK-2F9A',
				offer: {
					layout: 'rows',
					rows: [],
					everyone: [],
					offered_files: 1412,
					offered_bytes: 38_200_000_000,
					files: 1066,
					bytes: 29_600_000_000,
					shared: 0,
					unfiled_files: 0,
					unfiled_bytes: 0,
					held: []
				},
				manifest: [
					group({ have: 203, to_take: 409, held_same: 32, held_look: 171 }),
					group({ key: 'person:1', index: 1, name: 'Bryn Calloway', have: 0, to_take: 308 }),
					group({
						key: 'site:0',
						kind: 'site',
						index: null,
						name: 'Northlight Media',
						have: 92,
						to_take: 349
					})
				]
			}),
			ontake
		});
		expect(words(at)).toContain('203 \u2192 612');
		expect(words(at)).toContain('1,412 files \u00b7 38 GB offered');
		expect(words(at)).toContain('32 the very same files and 171 that look the same');
		expect(words(at)).toContain('Take 1,066 files');
		at.querySelector<HTMLElement>('[aria-label="Take Bryn Calloway"]')?.click();
		at.querySelector<HTMLElement>('[aria-label="Take Northlight Media"]')?.click();
		flushSync();
		await tick();
		await Promise.resolve();
		flushSync();
		expect(api.weighAnswer).toHaveBeenLastCalledWith('S1', [1], [], ['site:0']);
		expect(words(at)).toContain('Take 612 files');
		[...at.querySelectorAll('button')].find((one) => words(one) === 'Take 612 files')?.click();
		expect(ontake).toHaveBeenCalledWith([1], [], ['site:0']);
	});

	it('Skip all answers with every group skipped', () => {
		api.swapTunnels.mockResolvedValue([]);
		api.weighAnswer.mockResolvedValue({ files: 0, bytes: 0, left_out: [], left_out_other: 0 });
		const ontake = vi.fn();
		const at = draw(ManifestOffer, {
			session: session({
				state: 'offered',
				manifest: [
					group({ have: 0 }),
					group({ key: 'songs', kind: 'songs', index: null, name: null })
				]
			}),
			ontake
		});
		[...at.querySelectorAll('button')].find((one) => words(one) === 'Skip all')?.click();
		expect(ontake).toHaveBeenCalledWith([0], [], ['songs']);
	});
});

describe('the transfer', () => {
	it('draws the rate, the streams, the rows and the files refused on arrival', () => {
		const at = draw(SwapProgress, {
			session: session({
				streams: 16,
				manifest: [group({ state: 'landed', done: 409, local_id: 'p1' })],
				refused: [{ title: 'clip 9.mp4', reason: 'bytes' }],
				refused_count: 1
			}),
			onend: () => undefined
		});
		expect(words(at)).toContain('24 MB/s on 16 streams');
		expect(words(at)).toContain('1 file refused on import');
		expect(words(at)).toContain('Ava Example');
	});
});

describe('the waiting steps', () => {
	it('says how long the host has waited, how long the token is good for, and what is known', () => {
		const at = draw(SwapToken, {
			token: 'ABCD-EFGH',
			sentence: 'S',
			since: Date.now() / 1000 - 125,
			expires: Date.now() / 1000 + 23 * 3600 + 50 * 60 + 10,
			known: "You've swapped with this device once before."
		});
		expect(words(at)).toContain('2 min in \u00b7 The token is good for another 23 h 50 min.');
		expect(words(at)).toContain("You've swapped with this device once before.");
	});

	it('names the device beside its id, says how to compare, and draws what goes under the code', () => {
		const at = draw(SwapCode, {
			code: 'K7Q2ZP',
			device: 'abcd1234',
			name: 'DESK-2F9A',
			onanswer: () => undefined
		});
		expect(words(at)).toContain('Their device: DESK-2F9A (ABCD-1234)');
		expect(words(at)).toContain(
			"Read it to them on the chat the token went through. If theirs differs, another device has the token: press They don't match."
		);
	});
});
