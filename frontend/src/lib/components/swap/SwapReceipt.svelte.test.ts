/* A swap's receipt, drawn: the heavy number and its context, a row per group with how it was
 * filed, what was not taken and why, the folder, and the Undo that asks once and then says what it
 * removed. On the host the same card says what was sent. */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';
import SwapReceipt from './SwapReceipt.svelte';
import SwapReceiptCard from './SwapReceiptCard.svelte';
import { ApiError } from '$lib/api/client';
import type { SwapReceipt as Receipt } from './receipt';

const api = vi.hoisted(() => ({ readReceipt: vi.fn(), undoSwap: vi.fn(), goto: vi.fn() }));

vi.mock('./receipt', async (real) => ({
	...(await real<typeof import('./receipt')>()),
	readReceipt: (id: string) => api.readReceipt(id),
	undoSwap: (id: string) => api.undoSwap(id)
}));

vi.mock('$app/navigation', () => ({ goto: (to: string) => api.goto(to) }));

function guestReceipt(over: Partial<Receipt> = {}): Receipt {
	return {
		session_id: 'session-1',
		role: 'guest',
		two_way: false,
		device: 'DESK-2F9A',
		device_name: null,
		started_at: 1_790_000_000,
		ended_at: 1_790_001_260,
		seconds: 1260,
		reason: 'done',
		files: 1065,
		bytes: 29_500_000_000,
		back_files: 0,
		back_bytes: 0,
		rate: 23_600_000,
		back_rate: null,
		moving: null,
		back_moving: null,
		waited: null,
		tunnel: 'NY-711',
		cut_offs: 1,
		cut_seconds: 0,
		not_kept: 0,
		folder: { id: 'folder-9', path: 'Swaps/Swap-825HYGXC', own: true },
		groups: [
			{
				kind: 'site',
				id: 'site-1',
				name: 'Northlight Media',
				way: 'landed',
				files: 348,
				bytes: 9_000_000_000,
				filed: 'added',
				faces: 0,
				usernames: 6
			},
			{
				kind: 'person',
				id: 'person-1',
				name: 'Ava Example',
				way: 'landed',
				files: 409,
				bytes: 11_000_000_000,
				filed: 'added',
				faces: 0,
				usernames: 0
			},
			{
				kind: 'person',
				id: 'person-2',
				name: 'Bryn Calloway',
				way: 'landed',
				files: 308,
				bytes: 9_500_000_000,
				filed: 'created',
				faces: 9,
				usernames: 0
			}
		],
		skipped: [{ kind: 'collection', id: null, name: 'Summer edits', files: 51 }],
		refused: 1,
		held: 203,
		undo: null,
		can_undo: true,
		...over
	};
}

let host: HTMLElement;
let shown: ReturnType<typeof mount> | undefined;

afterEach(() => {
	if (shown) unmount(shown);
	shown = undefined;
	host?.remove();
	document.body.innerHTML = '';
	vi.clearAllMocks();
	vi.useRealTimers();
});

function draw<P extends Record<string, unknown>>(component: unknown, props: P): HTMLElement {
	host = document.createElement('div');
	document.body.append(host);
	shown = mount(component as never, { target: host, props });
	flushSync();
	return host;
}

function said(where: Element): string {
	return (where.textContent ?? '').replace(/\s+/g, ' ').trim();
}

function button(where: ParentNode, words: string): HTMLButtonElement {
	const found = [...where.querySelectorAll('button')].find(
		(one) => said(one.querySelector('.label') ?? one) === words
	);
	if (!found) throw new Error(`no "${words}" button`);
	return found;
}

async function settle(): Promise<void> {
	for (let i = 0; i < 4; i += 1) await tick();
	flushSync();
}

describe('the guest card', () => {
	it('leads with the heavy number and its context, and says from whom and how', () => {
		const card = draw(SwapReceiptCard, { receipt: guestReceipt(), onundo: vi.fn() });
		expect(said(card.querySelector('.headline')!)).toBe('Done');
		expect(said(card.querySelector('.figure')!)).toBe('1,065');
		expect(said(card.querySelector('.context')!)).toBe('files landed, 30 GB, in 21 min');
		expect(said(card)).toContain('From DESK-2F9A through NY-711, average 24 MB/s, one cut-off');
	});

	it('lists the groups in the order Sift files things, each with how it was filed', () => {
		const card = draw(SwapReceiptCard, { receipt: guestReceipt() });
		const rows = [...card.querySelectorAll('.group .said')].map(said);
		expect(rows).toEqual([
			'Ava Example added to your Ava Example',
			'Bryn Calloway created, 9 faces learned',
			'Northlight Media added to your Northlight Media, 6 usernames filed'
		]);
		const link = card.querySelector<HTMLAnchorElement>('a.named');
		expect(link?.getAttribute('href')).toBe('/people/person-1');
		expect([...card.querySelectorAll('.badge .word')].map(said)).toEqual([
			'Landed',
			'Landed',
			'Landed'
		]);
	});

	it('says what was not taken and why, and where it landed', () => {
		const card = draw(SwapReceiptCard, { receipt: guestReceipt() });
		expect(said(card)).toContain(
			'Not taken Summer edits (51) skipped \u00b7 1 refused on import \u00b7 203 already here'
		);
		expect(said(card)).toContain('Where Swaps/Swap-825HYGXC');
	});

	it('puts Open the folder and Undo this swap at the end, and Undo only where it may', () => {
		const onopen = vi.fn();
		const card = draw(SwapReceiptCard, { receipt: guestReceipt(), onundo: vi.fn(), onopen });
		button(card, 'Open the folder').click();
		expect(onopen).toHaveBeenCalledOnce();
		expect(button(card, 'Undo this swap')).toBeTruthy();
	});

	it('after the Undo, says what was removed and offers it no more', () => {
		const card = draw(SwapReceiptCard, {
			receipt: guestReceipt({
				undo: {
					at: 1_790_002_000,
					files: 1065,
					from_disk: true,
					people: 1,
					sites: 0,
					in_use: 0,
					kept: 0
				},
				can_undo: false
			}),
			onundo: vi.fn(),
			onopen: vi.fn()
		});
		expect(said(card)).toContain(
			'Undone: 1,065 files removed from the library and deleted from disk. Removed with them: 1 person it created.'
		);
		expect(() => button(card, 'Undo this swap')).toThrow();
		expect(() => button(card, 'Open the folder')).toThrow();
		expect(card.querySelector('a.named')).toBeNull();
		expect([...card.querySelectorAll('.badge .word')].map(said)).toEqual([
			'Undone',
			'Undone',
			'Undone'
		]);
	});
});

describe('the numbers agree with each other', () => {
	it('counts the time from the first byte and says the wait at the code apart', () => {
		// 42 files, 543 MB, moving 45 s after three minutes at the code.
		const card = draw(SwapReceiptCard, {
			receipt: guestReceipt({
				files: 42,
				bytes: 543_000_000,
				seconds: 420,
				moving: 45,
				waited: 180,
				rate: 12_066_666.7,
				cut_offs: 0
			})
		});
		expect(said(card.querySelector('.context')!)).toBe('files landed, 543 MB, in under a minute');
		expect(said(card)).toContain('average 12 MB/s');
		expect(said(card)).toContain('Joined 3 min before the first file.');
	});

	it('a host started before the first file says so, and a short wait says nothing', () => {
		const host = draw(SwapReceiptCard, {
			receipt: guestReceipt({ role: 'host', folder: null, can_undo: false, waited: 300 })
		});
		expect(said(host)).toContain('Started 5 min before the first file.');
		unmount(shown!);
		shown = undefined;
		const quick = draw(SwapReceiptCard, { receipt: guestReceipt({ waited: 5 }) });
		expect(said(quick)).not.toContain('before the first file');
	});

	it('a run cut off at its end says how long it sat cut off beside the count', () => {
		const card = draw(SwapReceiptCard, {
			receipt: guestReceipt({ moving: 420, cut_offs: 1, cut_seconds: 720 })
		});
		expect(said(card.querySelector('.context')!)).toBe('files landed, 30 GB, in 7 min');
		expect(said(card)).toContain('one cut-off (12 min)');
		unmount(shown!);
		shown = undefined;
		const many = draw(SwapReceiptCard, { receipt: guestReceipt({ cut_offs: 13, cut_seconds: 9 }) });
		expect(said(many)).toContain('13 cut-offs');
		expect(said(many)).not.toContain('13 cut-offs (');
	});

	it('an exchange gives the other way its own average and says the files it did not keep', () => {
		const card = draw(SwapReceiptCard, {
			receipt: guestReceipt({
				two_way: true,
				reason: 'ended by you',
				files: 13,
				bytes: 60_000_000,
				moving: 60,
				rate: 1_000_000,
				back_files: 971,
				back_bytes: 8_700_000_000,
				back_moving: 540,
				back_rate: 16_100_000,
				not_kept: 976,
				skipped: [],
				refused: 0,
				held: 0
			})
		});
		expect(said(card.querySelector('.context')!)).toBe('files landed, 60 MB, in 1 min');
		expect(said(card)).toContain('And 971 files sent the other way, 8.7 GB, average 16 MB/s.');
		expect(said(card)).toContain('average 1.0 MB/s');
		expect(said(card)).toContain(
			'Not taken 976 files received and not kept: it ended before they were added'
		);
	});

	it('after an Undo, says what it kept and why, and keeps the Undo for files in use', () => {
		const card = draw(SwapReceiptCard, {
			receipt: guestReceipt({
				files: 42,
				undo: {
					at: 1_790_002_000,
					files: 30,
					from_disk: true,
					people: 0,
					sites: 0,
					in_use: 11,
					kept: 1
				},
				can_undo: true
			}),
			onundo: vi.fn()
		});
		expect(said(card)).toContain(
			'Undone: 30 files removed from the library and deleted from disk. 1 kept: already in the library. 11 still in use: Undo again later.'
		);
		expect(button(card, 'Undo this swap')).toBeTruthy();
	});
});

describe('a group with no one name', () => {
	it('is said by its kind, and a receipt with no time says none', () => {
		const card = draw(SwapReceiptCard, {
			receipt: guestReceipt({
				seconds: null,
				groups: [
					{
						kind: 'loose',
						id: null,
						name: null,
						way: 'landed',
						files: 4,
						bytes: 4_000_000,
						filed: null,
						faces: 0,
						usernames: 0
					}
				]
			})
		});
		expect([...card.querySelectorAll('.group .said')].map(said)).toEqual(['Other files']);
		expect(said(card.querySelector('.context')!)).toBe('files landed, 30 GB');
	});
});

describe('the host card', () => {
	it('says what was sent, to whom, and offers no Undo', () => {
		const receipt = guestReceipt({
			role: 'host',
			device: 'LAPTOP-7C1E',
			device_name: 'Hollis Danforth',
			folder: null,
			can_undo: false,
			skipped: [],
			refused: 0,
			held: 0,
			groups: guestReceipt().groups.map((one) => ({
				...one,
				way: 'sent' as const,
				filed: null,
				faces: 0,
				usernames: 0
			}))
		});
		const card = draw(SwapReceiptCard, { receipt, onundo: vi.fn(), onopen: vi.fn() });
		expect(said(card.querySelector('.context')!)).toBe('files sent, 30 GB, in 21 min');
		expect(said(card)).toContain('To Hollis Danforth (LAPTOP-7C1E) through NY-711');
		expect([...card.querySelectorAll('.group .said')].map(said)[0]).toBe('Ava Example sent');
		expect(said(card)).not.toContain('Not taken');
		expect(card.querySelectorAll('button')).toHaveLength(0);
	});
});

describe('the receipt read by its session', () => {
	it('asks once, then undoes, and draws what the server answered', async () => {
		api.readReceipt.mockResolvedValue(guestReceipt());
		api.undoSwap.mockResolvedValue(
			guestReceipt({
				undo: {
					at: 1_790_002_000,
					files: 1065,
					from_disk: true,
					people: 0,
					sites: 0,
					in_use: 0,
					kept: 0
				},
				can_undo: false
			})
		);
		const where = draw(SwapReceipt, { sessionId: 'session-1' });
		await settle();
		expect(api.readReceipt).toHaveBeenCalledWith('session-1');
		button(where, 'Undo this swap').click();
		flushSync();
		const dialog = document.querySelector('[role="alertdialog"]') as HTMLElement;
		expect(said(dialog)).toContain(
			"1,065 files are removed from the library, with the People and Sites the swap created that nothing else uses. They are deleted from disk in Swaps/Swap-825HYGXC. This can't be undone."
		);
		expect(api.undoSwap).not.toHaveBeenCalled();
		(document.querySelector('.confirm') as HTMLButtonElement).click();
		await settle();
		expect(api.undoSwap).toHaveBeenCalledWith('session-1');
		expect(said(where)).toContain(
			'Undone: 1,065 files removed from the library and deleted from disk.'
		);
	});

	it('says a refused Undo under its press', async () => {
		api.readReceipt.mockResolvedValue(guestReceipt());
		api.undoSwap.mockRejectedValue(new ApiError(409, 'That swap has been undone already.'));
		const where = draw(SwapReceipt, { sessionId: 'session-1' });
		await settle();
		button(where, 'Undo this swap').click();
		flushSync();
		(document.querySelector('.confirm') as HTMLButtonElement).click();
		await settle();
		expect(said(where)).toContain('That swap has been undone already.');
	});

	it('opens the folder it landed in', async () => {
		api.readReceipt.mockResolvedValue(guestReceipt());
		const where = draw(SwapReceipt, { sessionId: 'session-1' });
		await settle();
		button(where, 'Open the folder').click();
		expect(api.goto).toHaveBeenCalledWith('/browse?in=folder-9');
	});

	it('looks again for a receipt still being written, then says there is none', async () => {
		vi.useFakeTimers();
		api.readReceipt.mockRejectedValue(new ApiError(404, 'Not found'));
		const where = draw(SwapReceipt, { sessionId: 'session-2' });
		await vi.advanceTimersByTimeAsync(0);
		expect(api.readReceipt).toHaveBeenCalledTimes(1);
		await vi.advanceTimersByTimeAsync(2000);
		expect(api.readReceipt).toHaveBeenCalledTimes(2);
		await vi.advanceTimersByTimeAsync(2000);
		flushSync();
		expect(api.readReceipt).toHaveBeenCalledTimes(3);
		expect(said(where)).toContain('Sift kept no receipt for this swap.');
	});
});
