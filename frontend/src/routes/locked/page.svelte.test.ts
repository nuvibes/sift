/* The lock screen follows the server about the PIN. */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

vi.mock('$app/navigation', () => ({ goto: vi.fn() }));
vi.mock('$lib/api/client', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/client')>()),
	api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() }
}));

import Page from './+page.svelte';
import { api, ApiError } from '$lib/api/client';

const SENTENCE = 'Your PIN only unlocks Sift on your local network. From here, use your password.';

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

beforeEach(() => {
	vi.mocked(api.get).mockResolvedValue({ pin_unlock_offered: true });
	// jsdom draws no animation; the refusal's shake is not what is tested here.
	Element.prototype.animate ??= vi.fn() as unknown as Element['animate'];
	// The PIN control looks for a password manager's badge a moment after it draws; jsdom has no
	// layout to answer from.
	document.elementFromPoint ??= () => null;
});

afterEach(() => {
	if (mounted) void unmount(mounted);
	mounted = null;
	host?.remove();
	vi.clearAllMocks();
});

async function render(): Promise<HTMLElement> {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(Page, { target: host });
	await vi.waitFor(() => {
		flushSync();
		expect(host.querySelector('input[inputmode="numeric"]')).not.toBeNull();
	});
	return host;
}

function typePin(where: HTMLElement, pin: string): void {
	const input = where.querySelector('input[inputmode="numeric"]') as HTMLInputElement;
	const paste = new Event('paste', { bubbles: true, cancelable: true });
	Object.defineProperty(paste, 'clipboardData', { value: { getData: () => pin } });
	input.dispatchEvent(paste);
	flushSync();
}

function submit(where: HTMLElement): void {
	where
		.querySelector('form')
		?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
}

describe('opening the lock screen', () => {
	it('puts the keyboard in the PIN box when the PIN is on offer', async () => {
		const where = await render();

		await vi.waitFor(() => {
			flushSync();
			expect(document.activeElement).toBe(where.querySelector('input[inputmode="numeric"]'));
		});
	});

	it('puts the keyboard in the password box when it is not', async () => {
		vi.mocked(api.get).mockResolvedValue({ pin_unlock_offered: false });
		host = document.createElement('div');
		document.body.append(host);
		mounted = mount(Page, { target: host });

		await vi.waitFor(() => {
			flushSync();
			expect(document.activeElement).toBe(host.querySelector('input[type="password"]'));
		});
	});
});

describe('a PIN from outside the local network', () => {
	it('shows the server sentence and draws the password form', async () => {
		vi.mocked(api.post).mockRejectedValue(new ApiError(403, 'refused', SENTENCE));
		const where = await render();

		typePin(where, '246810');
		submit(where);

		await vi.waitFor(() => {
			flushSync();
			expect(where.querySelector('[role="alert"]')?.textContent).toBe(SENTENCE);
		});
		expect(vi.mocked(api.post)).toHaveBeenCalledWith('/auth/unlock', { body: { pin: '246810' } });
		expect(where.querySelector('input[type="password"]')).not.toBeNull();
		expect(where.querySelector('input[inputmode="numeric"]')).toBeNull();
		// No way back to a PIN the server will not take from here.
		expect(where.textContent).not.toContain('Use your PIN instead');
	});

	it('keeps the PIN box for a wrong PIN on the local network', async () => {
		vi.mocked(api.post).mockRejectedValue(new ApiError(401, 'incorrect PIN'));
		const where = await render();

		typePin(where, '000000');
		submit(where);

		await vi.waitFor(() => {
			flushSync();
			expect(where.querySelector('[role="alert"]')?.textContent).toBe('Incorrect PIN.');
		});
		expect(where.querySelector('input[inputmode="numeric"]')).not.toBeNull();
	});
});
