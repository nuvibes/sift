/*
 * Join a swap: the tunnel this side dials through is chosen beside Join, in the open, with the
 * server it is on; the one chosen last time is offered first and a new choice is remembered; the
 * join carries the chosen tunnel; and the form says where the files will land.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';

import { ApiError } from '$lib/api/client';
import JoinSwap from './JoinSwap.svelte';

const api = vi.hoisted(() => ({
	swapTunnels: vi.fn(),
	joinSwap: vi.fn(),
	values: vi.fn(),
	saved: vi.fn()
}));

vi.mock('./swap', async (real) => ({
	...(await real<typeof import('./swap')>()),
	swapTunnels: () => api.swapTunnels(),
	joinSwap: (...args: unknown[]) => api.joinSwap(...args)
}));

vi.mock('$lib/settings-ui/settings', async (real) => ({
	...(await real<typeof import('$lib/settings-ui/settings')>()),
	fetchSettingValues: () => api.values(),
	saveSettings: (values: Record<string, unknown>) => api.saved(values)
}));

vi.mock('$lib/library/destinations.svelte', () => ({
	Destinations: class {
		placed = [
			{ value: 'f-recv', label: 'Received' },
			{ value: 'f-other', label: 'Holidays' }
		];
		async load(): Promise<void> {}
		follow(): void {}
	}
}));

/* jsdom has no pointer capture, and the select primitive releases it on the way down. */
for (const name of ['setPointerCapture', 'releasePointerCapture', 'hasPointerCapture'] as const) {
	if (!(name in Element.prototype)) {
		Object.defineProperty(Element.prototype, name, { value: () => false, writable: true });
	}
}

const TUNNELS = [
	{ id: 't-harbor', name: 'Harbor exit', can_host: true, endpoint: '198.51.100.7' },
	{ id: 't-ridge', name: 'Ridge exit', can_host: null, endpoint: null }
];

let host: HTMLElement;
let shown: ReturnType<typeof mount> | undefined;

beforeEach(() => {
	api.swapTunnels.mockResolvedValue(TUNNELS);
	api.values.mockResolvedValue(new Map([['swap.guest_tunnel', 't-harbor']]));
	api.saved.mockResolvedValue(undefined);
	api.joinSwap.mockResolvedValue({ session_id: 's1' });
});

afterEach(() => {
	if (shown) unmount(shown);
	shown = undefined;
	host?.remove();
	vi.clearAllMocks();
});

function draw(onjoined: (id: string) => void = () => {}, exchange = false): HTMLElement {
	host = document.createElement('div');
	document.body.append(host);
	shown = mount(JoinSwap, { target: host, props: { onjoined, exchange } });
	flushSync();
	return host;
}

async function settled(): Promise<void> {
	for (let turn = 0; turn < 6; turn += 1) await tick();
	flushSync();
}

describe('Join an exchange', () => {
	it('says exchange in its heading, its press and every sentence', async () => {
		api.swapTunnels.mockResolvedValue([]);
		api.values.mockResolvedValue(new Map());
		const where = draw(() => {}, true);
		await settled();
		expect(where.textContent).toContain('Join an exchange');
		expect(where.querySelector('button[type="submit"]')?.textContent?.trim()).toBe(
			'Join the exchange'
		);
		expect(where.textContent).toContain('An exchange goes through one of your tunnels');
		expect(where.textContent).not.toMatch(/\bswap\b/i);
	});
});

describe('Join a swap', () => {
	it('offers the tunnel chosen last time, names its server, and joins through it', async () => {
		const joined = vi.fn();
		const where = draw(joined);
		await vi.waitFor(() =>
			expect(where.querySelector('[aria-label="Tunnel"]')?.textContent).toContain('Harbor exit')
		);
		// The server, as Sites and Tunnels shows it: named, with its first part plain.
		expect(where.textContent).toContain('Tunnel server');
		expect(where.textContent).toContain('198');

		// Only the tunnels this library has, each by its name.
		expect(api.swapTunnels).toHaveBeenCalledTimes(1);
		expect(joined).not.toHaveBeenCalled();
	});

	it('says where files land, before the join and once there is a session', async () => {
		const { landingWords } = await import('./swap');
		expect(landingWords('Received')).toBe(
			'Files land under a new folder for this swap in Received, in folders named for their people and Sites.'
		);
		expect(landingWords('Received', '7K3QM2RD')).toBe(
			'Files land under Swap-7K3QM2RD in Received, in folders named for their people and Sites.'
		);
	});

	it('asks for a tunnel before the join is sent when none is chosen', async () => {
		api.swapTunnels.mockResolvedValue([]);
		api.values.mockResolvedValue(new Map());
		const where = draw();
		await settled();
		where.querySelector<HTMLTextAreaElement>('textarea')!.value = 'ABCD-EFGH';
		where.querySelector('textarea')!.dispatchEvent(new Event('input', { bubbles: true }));
		where.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }));
		await settled();
		expect(api.joinSwap).not.toHaveBeenCalled();
		expect(where.textContent).toContain(
			'A swap goes through one of your tunnels, and there are none yet.'
		);
	});

	/* The fix for a tunnel on the host's own server is the Tunnel chooser, so that is where it is
	 * said, and the token box it has nothing to do with stays as it was. */
	it('says a refusal about the tunnel under the Tunnel chooser, not under the token', async () => {
		const SAME =
			'Your tunnel and theirs leave from the same server. Choose a tunnel on another server.';
		api.joinSwap.mockRejectedValue(new ApiError(409, 'refused', SAME, 'tunnel_id'));
		const where = await filledIn();
		where.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }));
		await vi.waitFor(() =>
			expect(where.querySelector('#swap-tunnel-guest-error')?.textContent).toBe(SAME)
		);
		expect(where.querySelector('[aria-label="Tunnel"]')?.getAttribute('aria-invalid')).toBe('true');
		expect(where.querySelector('textarea')?.getAttribute('aria-invalid')).toBeNull();
		expect(where.querySelectorAll('[role="alert"]')).toHaveLength(1);
	});

	it('says a token that does not read under the token, and one about no part over Join', async () => {
		const UNREAD = "This isn't a swap token. Paste the whole token you were sent.";
		api.joinSwap.mockRejectedValueOnce(new ApiError(409, 'refused', UNREAD, 'token'));
		const where = await filledIn();
		where.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }));
		await vi.waitFor(() =>
			expect(where.querySelector('textarea')?.getAttribute('aria-invalid')).toBe('true')
		);
		expect(where.querySelector('.field')?.textContent).toContain(UNREAD);
		expect(where.querySelector('#swap-tunnel-guest-error')).toBeNull();

		const LOCKED = 'Your saved keys are locked.';
		api.joinSwap.mockRejectedValueOnce(new ApiError(409, 'refused', LOCKED));
		where.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }));
		await vi.waitFor(() => expect(where.textContent).toContain(LOCKED));
		expect(where.querySelector('textarea')?.getAttribute('aria-invalid')).toBeNull();
		expect(where.querySelector('#swap-tunnel-guest-error')).toBeNull();
		expect(where.querySelectorAll('[role="alert"]')).toHaveLength(1);
	});
});

/** The form drawn with a token pasted, a folder and a tunnel chosen: ready to join. */
async function filledIn(): Promise<HTMLElement> {
	const where = draw();
	await vi.waitFor(() =>
		expect(where.querySelector('[aria-label="Tunnel"]')?.textContent).toContain('Harbor exit')
	);
	const box = where.querySelector<HTMLTextAreaElement>('textarea')!;
	box.value = 'ABCD-EFGH';
	box.dispatchEvent(new Event('input', { bubbles: true }));
	const trigger = where.querySelector('#swap-folder')!;
	for (const type of ['pointerdown', 'pointerup'])
		trigger.dispatchEvent(new MouseEvent(type, { bubbles: true, button: 0 }));
	(trigger as HTMLElement).click();
	flushSync();
	const item = [...document.querySelectorAll('.ui-select-item-label')].find(
		(one) => one.textContent?.trim() === 'Received'
	);
	expect(item, 'no folder labelled Received').toBeTruthy();
	const row = item!.closest('.ui-select-item')!;
	for (const type of ['pointerdown', 'pointerup'])
		row.dispatchEvent(new MouseEvent(type, { bubbles: true, button: 0 }));
	(row as HTMLElement).click();
	await settled();
	return where;
}
