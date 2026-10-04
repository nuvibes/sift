/* The swap screens, drawn: the token with its sentence, the code before anything else, the offer's
 * rows and whole screen with Take and Skip, the progress with the session's own estimate, and the
 * Swaps block on Privacy.
 *
 * What is held here is what only a drawing can show: the sentence beside the token word for word,
 * a Skip reaching the answer that is sent, the shared-files sentence, the unticked files that are
 * already here, and that nothing on any of these screens is an address.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';
import OfferScreen from './OfferScreen.svelte';
import SwapCode from './SwapCode.svelte';
import DeviceId from '$lib/settings-ui/DeviceId.svelte';
import SwapProgress from './SwapProgress.svelte';
import SwapToken from './SwapToken.svelte';
import SwapLaunch from './SwapLaunch.svelte';
import { settingChanges } from '$lib/library/changes.svelte';
import { noServerAt } from '../../../test-setup';

// The Start panel's settings link pulls the settings store in, which reads the settings once.
noServerAt('/api/settings');
// Send and receive's folder chooser reads the library's folders and the download settings.
noServerAt('/api/library/folders', '/api/site-options');
import type { OfferScreen as Screen, PersonRow, SwapSession } from './swap';

const api = vi.hoisted(() => ({
	swapDevice: vi.fn(),
	swapTunnels: vi.fn(),
	resetDevice: vi.fn(),
	weighPicks: vi.fn(),
	weighAnswer: vi.fn()
}));

vi.mock('./swap', async (real) => ({
	...(await real<typeof import('./swap')>()),
	swapDevice: () => api.swapDevice(),
	swapTunnels: () => api.swapTunnels(),
	resetDevice: () => api.resetDevice(),
	weighPicks: (chosen: unknown) => api.weighPicks(chosen),
	weighAnswer: (id: string, skipped: number[]) => api.weighAnswer(id, skipped)
}));

const SENTENCE = "This token holds your VPN's address, not your home's, and it works once.";

/** A dotted IPv4 address anywhere in what is drawn. */
const ADDRESS = /\b\d{1,3}(?:\.\d{1,3}){3}\b/;

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

function press(where: HTMLElement, words: string, nth = 0): void {
	const buttons = [...where.querySelectorAll('button')].filter(
		(one) => words === (one.querySelector('.label') ?? one).textContent?.replace(/\s+/g, ' ').trim()
	);
	const button = buttons[nth];
	if (!button) throw new Error(`no "${words}" button`);
	button.click();
	flushSync();
}

function row(index: number, name: string, over: Partial<PersonRow> = {}): PersonRow {
	return {
		index,
		name,
		files: 38,
		bytes: 6_000_000_000,
		held: 0,
		faces: 0,
		confirmed: null,
		match_id: null,
		match_name: null,
		matched_by: null,
		...over
	};
}

function screen(over: Partial<Screen> = {}): Screen {
	return {
		layout: 'rows',
		rows: [
			row(0, 'Ava Example'),
			row(1, 'Bea Sample', { match_id: 'p9', match_name: 'Bea Sample', matched_by: 'box' })
		],
		everyone: [],
		offered_files: 70,
		offered_bytes: 11_000_000_000,
		files: 70,
		bytes: 11_000_000_000,
		shared: 0,
		unfiled_files: 0,
		unfiled_bytes: 0,
		held: [],
		...over
	};
}

function session(over: Partial<SwapSession> = {}): SwapSession {
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
		peer_device: 'ABCD-EFGH',
		offered_files: 70,
		wanted_files: 38,
		sent_files: 12,
		sent_bytes: 1_000_000_000,
		wanted_bytes: 4_000_000_000,
		rate_bps: 10_000_000,
		unwanted_files: 0,
		started_at: 1,
		ended_at: null,
		end_reason: null,
		offer: null,
		rejoin_until: null,
		// Received is the receiving side's own count while the session runs; none here.
		received_files: null,
		...over
	};
}

describe('the token', () => {
	it('carries the sentence beside it word for word, and waits for them', () => {
		const where = draw(SwapToken, { token: 'ABCD-EFGH-IJKL', sentence: SENTENCE });
		expect(where.textContent).toContain('ABCD-EFGH-IJKL');
		expect(where.querySelector('.sentence')?.textContent).toBe(SENTENCE);
		expect(where.textContent).toContain('Waiting for them to join');
		expect(where.textContent).not.toMatch(ADDRESS);
	});
});

describe('the code', () => {
	it('is drawn large with the sentence, and each answer reaches the screen', () => {
		const answers: boolean[] = [];
		const where = draw(SwapCode, {
			code: 'K7Q2ZP',
			device: 'ABCD-EFGH',
			onanswer: (match: boolean) => answers.push(match)
		});
		expect(where.querySelector('.code')?.textContent).toBe('K7Q2ZP');
		expect(where.textContent).toContain('Compare this code with them before you go on.');
		press(where, "They don't match");
		press(where, 'They match');
		expect(answers).toEqual([false, true]);
	});
});

describe('the offer', () => {
	it('draws a row per person with Take and Skip, and a Skip reaches the answer', () => {
		const taken: number[][] = [];
		const where = draw(OfferScreen, {
			screen: screen({ shared: 212 }),
			ontake: (skipped: number[]) => taken.push(skipped)
		});
		expect(where.textContent).toContain('Ava Example: 38 files, 6.0 GB');
		expect(where.textContent).toContain('New to your library');
		expect(where.textContent).toContain('Bea Sample here, by stash-box');
		expect(where.textContent).toContain('212 files appear under two of these people.');
		press(where, 'Skip', 1);
		press(where, 'Take these');
		press(where, 'Take', 1);
		press(where, 'Take these');
		expect(taken).toEqual([[1], []]);
		expect(where.textContent).not.toMatch(ADDRESS);
	});

	it('says a person offered for their facial fingerprints alone brings those and no files', () => {
		const where = draw(OfferScreen, {
			screen: screen({
				rows: [row(0, 'Bea Sample', { files: 0, bytes: 0, faces: 12 })]
			}),
			ontake: () => undefined
		});
		expect(where.textContent).toContain('Bea Sample: 12 facial fingerprints, no files');
	});

	it('says how many confirmed faces the other side has of somebody offered for their facial fingerprints', () => {
		const where = draw(OfferScreen, {
			screen: screen({
				rows: [
					row(0, 'Bea Sample', { files: 0, bytes: 0, faces: 3, confirmed: 3 }),
					row(1, 'Ava Example', { files: 0, bytes: 0, faces: 64, confirmed: 210 })
				]
			}),
			ontake: () => undefined
		});
		expect(where.textContent).toContain(
			'Bea Sample: 3 facial fingerprints from 3 confirmed faces, no files'
		);
		expect(where.textContent).toContain(
			'Ava Example: 64 facial fingerprints from 210 confirmed faces, no files'
		);
	});

	it('shows what is already here unticked, and says why', () => {
		const where = draw(OfferScreen, {
			screen: screen({
				held: [
					{ key: 'k1', title: 'Ava Example, clip 1 of 38', reason: 'same' },
					{ key: 'k2', title: 'Ava Example, clip 2 of 38', reason: 'near' }
				]
			}),
			ontake: () => undefined
		});
		press(where, 'Show the 2 you have');
		expect(where.textContent).toContain('You have this one');
		expect(where.textContent).toContain('You have one that looks the same');
		const ticks = [...where.querySelectorAll('.held [role="checkbox"], .held button')];
		expect(ticks.length).toBeGreaterThan(0);
		for (const one of ticks) expect(one.getAttribute('aria-checked')).not.toBe('true');
	});

	it('above ten people is the whole offer, with everyone folded and searchable', async () => {
		const everyone = Array.from({ length: 11 }, (_, index) =>
			row(index, `Person ${String.fromCharCode(65 + index)}`, { files: 20 - index })
		);
		const taken: number[][] = [];
		const where = draw(OfferScreen, {
			screen: screen({ layout: 'whole', rows: everyone.slice(0, 5), everyone }),
			ontake: (skipped: number[]) => taken.push(skipped)
		});
		expect(where.textContent?.replace(/\s+/g, ' ')).toContain('under 11 people');
		expect(where.textContent).not.toContain('Person K');
		press(where, 'Show everyone (11)');
		expect(where.textContent).toContain('Person K');
		const box = where.querySelector<HTMLInputElement>('input');
		if (!box) throw new Error('no search box');
		box.value = 'person k';
		box.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		await tick();
		expect(where.textContent).not.toContain('Person J:');
		const tick10 = where.querySelector<HTMLElement>('.people:last-of-type [role="checkbox"]');
		tick10?.click();
		flushSync();
		press(where, 'Take these');
		expect(taken).toEqual([[10]]);
	});
});

describe('what a swap carries, as a size', () => {
	it('the offer says everything offered, and what taking these would bring as people are skipped', async () => {
		api.weighAnswer.mockResolvedValue({ files: 32, bytes: 5_000_000_000 });
		const where = draw(OfferScreen, {
			screen: screen({ offered_files: 72, offered_bytes: 11_400_000_000 }),
			sessionId: 'S1',
			ontake: () => undefined
		});
		expect(where.textContent).toContain('They offered 72 files, 11 GB.');
		expect(where.textContent).toContain('You would receive 70 files, 11 GB.');
		expect(api.weighAnswer).not.toHaveBeenCalled();

		press(where, 'Skip', 1);
		await vi.waitFor(() =>
			expect(where.textContent).toContain('You would receive 32 files, 5.0 GB.')
		);
		expect(api.weighAnswer).toHaveBeenCalledWith('S1', [1]);
		press(where, 'Take', 1);
		expect(where.textContent).toContain('You would receive 70 files, 11 GB.');
	});

	it('the sender sees what the picks add up to, asked again as they change', async () => {
		vi.useFakeTimers();
		try {
			api.swapTunnels.mockResolvedValue([]);
			api.weighPicks.mockResolvedValue({
				files: 38,
				bytes: 6_000_000_000,
				left_out: [],
				left_out_other: 0
			});
			const where = draw(SwapLaunch, {
				chosen: [{ kind: 'person', id: 'p1' }],
				onstarted: () => {}
			});
			expect(api.weighPicks).not.toHaveBeenCalled();
			await vi.advanceTimersByTimeAsync(300);
			expect(api.weighPicks).toHaveBeenCalledWith([{ kind: 'person', id: 'p1' }]);
			flushSync();
			expect(where.textContent).toContain('You would send 38 files, 6.0 GB.');
		} finally {
			vi.useRealTimers();
		}
	});

	it('Send and receive asks where what comes back lands before it starts', async () => {
		api.swapTunnels.mockResolvedValue([{ id: 't1', name: 'Home', can_host: true }]);
		const started = vi.fn();
		const where = draw(SwapLaunch, {
			chosen: [{ kind: 'person', id: 'p1' }],
			onstarted: started,
			both: true
		});
		expect(where.textContent).toContain('Put received files in');
		press(where, 'Start');
		await tick();
		expect(where.textContent).toContain('Choose a folder to put received files in.');
		expect(started).not.toHaveBeenCalled();
	});

	it('turns Start off when the picks offer no file, and says why; an exchange still starts', async () => {
		vi.useFakeTimers();
		try {
			api.swapTunnels.mockResolvedValue([{ id: 't1', name: 'Home', can_host: true }]);
			api.weighPicks.mockResolvedValue({ files: 0, bytes: 0, left_out: [], left_out_other: 0 });
			const chosen = [{ kind: 'person' as const, id: 'p1' }];
			const where = draw(SwapLaunch, { chosen, onstarted: () => {} });
			await vi.advanceTimersByTimeAsync(300);
			flushSync();
			const start = () =>
				[...where.querySelectorAll('button')].find((one) => one.textContent?.trim() === 'Start');
			expect(where.textContent).toContain(
				"None of what you picked can be sent, so there's nothing to start."
			);
			expect(where.textContent).not.toContain('0 files');
			expect(start()?.disabled).toBe(true);
			unmount(shown!);

			const both = draw(SwapLaunch, { chosen, onstarted: () => {}, both: true });
			await vi.advanceTimersByTimeAsync(300);
			flushSync();
			expect(both.textContent).toContain(
				'You would send no files, and still receive what they send.'
			);
			const go = [...both.querySelectorAll('button')].find(
				(one) => one.textContent?.trim() === 'Start'
			);
			expect(go?.disabled).toBe(false);
		} finally {
			vi.useRealTimers();
		}
	});

	it('says exchange in every word under the Exchange door, and the drawer heading follows', async () => {
		api.swapTunnels.mockResolvedValue([]);
		const where = draw(SwapLaunch, { chosen: [], onstarted: () => {}, both: true });
		await tick();
		await tick();
		flushSync();
		expect(where.textContent).toContain('An exchange goes through one of your tunnels');
		expect(where.textContent).not.toMatch(/\bswap\b/i);
	});

	it('nothing picked says nothing and asks nothing', async () => {
		api.swapTunnels.mockResolvedValue([]);
		const where = draw(SwapLaunch, { chosen: [], onstarted: () => {} });
		await new Promise((done) => setTimeout(done, 350));
		expect(api.weighPicks).not.toHaveBeenCalled();
		expect(where.textContent).not.toContain('You would send');
	});
});

describe('the progress', () => {
	it('both ways, draws what is sent and what is received, each with its size, and one estimate', () => {
		const where = draw(SwapProgress, {
			session: session({
				state: 'transferring',
				two_way: true,
				answered: true,
				sending: {
					offered_files: 4,
					wanted_files: 2,
					files: 1,
					bytes: 3_000_000_000,
					wanted_bytes: 6_000_000_000,
					rate_bps: 80_000_000,
					moving: true,
					received_files: null
				},
				receiving: {
					offered_files: 9,
					wanted_files: 3,
					files: 0,
					bytes: 0,
					wanted_bytes: 0,
					rate_bps: null,
					moving: false,
					received_files: null
				}
			}),
			onend: () => {}
		});
		const text = where.textContent ?? '';
		expect(text).toContain('Send');
		expect(text).toContain('1 of 2 files sent');
		expect(text).toContain('3.0 GB of 6.0 GB');
		expect(text).toContain('Receive');
		// Answered here, nothing moving yet: the first file is on its way.
		expect(text).toContain('Starting');
		// 3 GB left at 80 Mbit/s, the one direction moving: five minutes, as every estimate says it.
		expect(text).toContain('A few minutes left');
		expect(text).not.toMatch(ADDRESS);
	});

	it("counts files and bytes and says the session's own estimate", () => {
		let ended = 0;
		const where = draw(SwapProgress, { session: session(), onend: () => (ended += 1) });
		expect(where.textContent).toContain('12 of 38 files received');
		expect(where.textContent).toContain('1.0 GB of 4.0 GB');
		expect(where.textContent).toContain('About 30 to 45 minutes left');
		press(where, 'End the swap');
		expect(ended).toBe(1);
		expect(where.textContent).not.toMatch(ADDRESS);
	});

	it('says received and filed on the receiving side, and the sender as it was', () => {
		// 30 received, 12 of them filed so far: the landing is behind the pieces arriving.
		const receiving = draw(SwapProgress, {
			session: { ...session(), received_files: 30 } as SwapSession,
			onend: () => {}
		});
		expect(receiving.textContent).toContain('30 of 38 files received, 12 filed');
		const sending = draw(SwapProgress, {
			session: { ...session({ role: 'host' }), received_files: null } as SwapSession,
			onend: () => {}
		});
		expect(sending.textContent).toContain('12 of 38 files sent');
		expect(sending.textContent).not.toContain('filed');
	});

	it("keeps a tenth of a gigabyte moving to the end, in the total's unit", () => {
		const where = draw(SwapProgress, {
			session: session({ sent_bytes: 11_030_000_000, wanted_bytes: 11_530_000_000 }),
			onend: () => undefined
		});
		expect(where.textContent).toContain('11.0 GB of 11.5 GB');
		expect(where.textContent).not.toContain('12 GB');
	});

	it('says there is not enough to say yet before the first measurement', () => {
		const where = draw(SwapProgress, {
			session: session({ rate_bps: null, role: 'host' }),
			onend: () => undefined
		});
		expect(where.textContent).toContain('12 of 38 files sent');
		expect(where.textContent).toContain('Not enough to say yet');
		expect(where.textContent).not.toContain('Working out the speed');
	});
});

describe('Start the swap, the tunnel chooser', () => {
	it('re-reads the tunnels when a setting moves, so one imported while the panel is open is offered', async () => {
		/* A tunnel imported on the Sites pane while this panel stands open must appear in the
		   chooser without a reload, not only the list read once on arrival. Every tunnel route
		   rings the settings bell, and the chooser reads again on it. */
		api.swapTunnels.mockResolvedValue([{ id: 't1', name: 'Tunnel one', can_host: false }]);
		const where = draw(SwapLaunch, { chosen: [], onstarted: () => {} });
		await vi.waitFor(() => expect(where.textContent).toContain('Tunnel one'));
		expect(where.textContent).not.toContain('Tunnel two');

		api.swapTunnels.mockResolvedValue([
			{ id: 't1', name: 'Tunnel one', can_host: false },
			{ id: 't2', name: 'Tunnel two', can_host: null }
		]);
		settingChanges.changed();
		flushSync();
		await tick();
		await vi.waitFor(() => expect(api.swapTunnels).toHaveBeenCalledTimes(2));
		// The chooser holds the tunnel that can host, or the first: with none able, the first stays.
		const chooser = where.querySelector('[aria-label="Tunnel"]');
		expect(chooser?.textContent).toContain('Tunnel one');
		// The second is offered once the list opens; the options are the chooser's own rows.
		expect(api.swapTunnels).toHaveBeenCalledTimes(2);
	});
});

describe('Settings > Updates and Info > Your device id', () => {
	it('shows the device id in fours, and asks before a reset', async () => {
		api.swapDevice.mockResolvedValue({ device_id: 'ABCDEFGHIJKLMNOP', locked: false });
		const where = draw(DeviceId, {});
		await vi.waitFor(() => expect(where.textContent).toContain('ABCD-EFGH-IJKL-MNOP'));
		expect(where.textContent).toContain('Your device id');
		expect(where.querySelector('[id="updates.device_id"]')).not.toBeNull();
		// The swap starts from the Swap screen alone: no row here starts one or lists the tunnels.
		expect(where.textContent).not.toContain('Start or join a swap');
		expect(where.textContent).not.toContain('Tunnels that can host a swap');
		// The reset waits behind the row's three dots, and the menu row only asks.
		const more = where.querySelector<HTMLButtonElement>('[aria-label="More for your device id"]');
		expect(more, 'no three dots on the id row').toBeTruthy();
		more?.click();
		await tick();
		flushSync();
		let reset: HTMLElement | undefined;
		await vi.waitFor(() => {
			reset = [...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].find((one) =>
				(one.textContent ?? '').includes('Reset device id')
			);
			expect(reset, 'no Reset device id row in the menu').toBeTruthy();
		});
		reset?.click();
		flushSync();
		await vi.waitFor(() =>
			expect(document.body.textContent).toContain('Swaps you start after this show a new id.')
		);
		expect(api.resetDevice).not.toHaveBeenCalled();
	});
});
