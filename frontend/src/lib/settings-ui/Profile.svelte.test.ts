/*
 * The PIN form on Profile: exactly six digits, and the vault's one PIN write, the same one the
 * create-a-PIN dialog on Hidden uses, so the two forms cannot disagree about what a PIN is.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

vi.mock('$lib/api/client', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/client')>()),
	api: { get: vi.fn(async () => ({ pin_set: false })), post: vi.fn(), put: vi.fn() }
}));
vi.mock('$app/navigation', () => ({ goto: vi.fn() }));
vi.mock('$lib/shell/toasts.svelte', () => ({ toasts: { show: vi.fn() } }));

import Profile from './Profile.svelte';
import { api, ApiError } from '$lib/api/client';
import { session } from '$lib/shell/session.svelte';
import { vault } from '$lib/shell/vault.svelte';

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

beforeEach(() => {
	vi.spyOn(vault, 'load').mockResolvedValue(undefined as never);
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(Profile, { target: host }) as Record<string, unknown>;
	flushSync();
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host.remove();
	session.viewer = undefined;
	vi.restoreAllMocks();
});

function pinForm(): HTMLFormElement {
	const form = host.querySelector('.pin-box')?.closest('form');
	if (!form) throw new Error('the PIN form is not on screen');
	return form as HTMLFormElement;
}

function fill(password: string, pin: string): void {
	const form = pinForm();
	const secret = form.querySelector('input[type="password"]') as HTMLInputElement;
	secret.value = password;
	secret.dispatchEvent(new Event('input', { bubbles: true }));
	const box = form.querySelector('.pin-box input') as HTMLInputElement;
	box.value = pin;
	box.dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();
}

function submit(): HTMLButtonElement {
	return pinForm().querySelector('button[type="submit"]') as HTMLButtonElement;
}

describe('the PIN form', () => {
	it('says a PIN is six digits, and will not submit five', () => {
		expect(pinForm().textContent).toContain('Six digits.');
		fill('a password', '12345');
		expect(submit().disabled).toBe(true);
		fill('a password', '123456');
		expect(submit().disabled).toBe(false);
	});

	it("saves through the vault's one PIN write, and says a refused PIN is six digits", async () => {
		const setPin = vi.spyOn(vault, 'setPin').mockResolvedValue('not-a-pin');
		fill('a password', '123456');

		pinForm().dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
		await vi.waitFor(() => expect(setPin).toHaveBeenCalledWith('123456', 'a password'));
		await Promise.resolve();
		flushSync();

		expect(pinForm().querySelector('[role="alert"]')?.textContent).toBe('A PIN is 6 digits.');
	});
});

/* One lit primary at a time: a form's press is lit only while it has something to save. */
describe('the presses', () => {
	it('light only the form that has something to save', () => {
		const presses = [...host.querySelectorAll<HTMLButtonElement>('button[type="submit"]')];
		expect(presses.length).toBe(3);
		expect(presses.every((one) => one.disabled)).toBe(true);
	});

	it('draws who is signed in as a row, not a card', () => {
		expect(host.querySelector('.who')).toBeNull();
		expect(host.querySelector('form.card')?.closest('.group')).not.toBeNull();
	});
});

describe('renaming yourself', () => {
	async function refusedWith(failure: Error): Promise<string | null | undefined> {
		session.viewer = { id: 'u-guest', username: 'visitor', role: 'guest' } as never;
		flushSync();
		vi.mocked(api.post).mockRejectedValueOnce(failure);
		const box = host.querySelector('input[autocomplete="username"]') as HTMLInputElement;
		box.value = 'Wren Halloway';
		box.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		const form = box.closest('form') as HTMLFormElement;
		form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
		await vi.waitFor(() => expect(form.querySelector('[role="alert"]')).not.toBeNull());
		return form.querySelector('[role="alert"]')?.textContent;
	}

	it("says the server's own words for a name that can't be used", async () => {
		const said = "That name can't be used.";
		expect(await refusedWith(new ApiError(409, 'Conflict', said))).toBe(said);
	});

	it("says the server's own words once the day's changes are spent", async () => {
		const said = "That's as many name changes as one day allows. Try again tomorrow.";
		expect(await refusedWith(new ApiError(429, 'Too Many Requests', said))).toBe(said);
	});

	it('says only that it could not be saved for any other failure', async () => {
		const failure = new ApiError(500, 'Internal Server Error', 'a trace nobody should read');
		expect(await refusedWith(failure)).toBe("That couldn't be saved.");
	});
});
