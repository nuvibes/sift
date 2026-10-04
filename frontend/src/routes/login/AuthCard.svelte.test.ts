/* The signed-out card's words, and where the caution about the one admin account sits.
 *
 * On first run the card creates the only account there is, and it is an admin, so the caution
 * about keeping its password to yourself is read where the password is being chosen: under the
 * two password fields, not above the username before anything has been typed.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

vi.mock('$app/navigation', () => ({ goto: vi.fn() }));

import AuthCard from './AuthCard.svelte';
import { api, ApiError } from '$lib/api/client';

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

afterEach(() => {
	vi.restoreAllMocks();
	if (mounted) void unmount(mounted);
	mounted = null;
	host?.remove();
});

function render(mode: 'setup' | 'login'): HTMLElement {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(AuthCard, { target: host, props: { mode } });
	flushSync();
	return host;
}

function caution(where: HTMLElement): Element | undefined {
	return [...where.querySelectorAll('p')].find((one) =>
		one.textContent?.includes('Keep this username and password to yourself')
	);
}

describe('creating the first account', () => {
	it('says what is being created, in the heading and on the button', () => {
		const where = render('setup');

		expect(where.querySelector('h1')?.textContent?.trim()).toBe('Create your Sift admin account');
		const submit = where.querySelector('button[type="submit"]');
		expect(submit?.textContent?.trim()).toBe('Create account');
	});

	it('puts the caution under the password fields, where the password is chosen', () => {
		const where = render('setup');
		const note = caution(where);
		const again = where.querySelector('input[name="confirmation"]');
		const username = where.querySelector('input[name="username"]');

		expect(note).toBeDefined();
		expect(again).not.toBeNull();
		expect(again!.compareDocumentPosition(note!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
		expect(
			username!.compareDocumentPosition(note!) & Node.DOCUMENT_POSITION_FOLLOWING
		).toBeTruthy();
	});
});

describe('signing in', () => {
	it('has no caution about choosing a password, and a plain Sign in', () => {
		const where = render('login');

		expect(caution(where)).toBeUndefined();
		expect(where.querySelector('button[type="submit"]')?.textContent?.trim()).toBe('Sign in');
	});

	it('draws Sign in as wide as the fields, the shape Unlock has on the other door', () => {
		const where = render('login');

		expect(where.querySelector('button[type="submit"]')?.classList.contains('full')).toBe(true);
	});
});

describe('the card follows the button and error rules every form does', () => {
	it('says a refusal in the shared problem line, announced', async () => {
		const where = render('setup');
		const type = (name: string, value: string) => {
			const box = where.querySelector<HTMLInputElement>(`input[name="${name}"]`);
			if (!box) throw new Error(`no ${name} box`);
			box.value = value;
			box.dispatchEvent(new Event('input', { bubbles: true }));
		};
		type('username', 'kate');
		type('password', 'one long password');
		type('confirmation', 'another long password');
		where
			.querySelector('form')
			?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
		flushSync();

		const said = where.querySelector('.problem[role="alert"]');
		expect(said?.textContent).toContain("Those two passwords aren't the same.");
	});

	it('keeps its words while busy and wears the spinner, rather than swapping in a second label', async () => {
		const source = (await import('./AuthCard.svelte?raw')).default;
		expect(source).toMatch(
			/<Button tone="primary" type="submit" full \{busy\}>\{action\}<\/Button>/
		);
		expect(source).not.toMatch(/Working/);
	});
});

describe('a sign-in the server refuses', () => {
	/* One sentence for a wrong username and a wrong password alike, so the screen never says which
	   half was right. */
	it('says Incorrect username or password, whichever half was wrong', async () => {
		vi.spyOn(api, 'post').mockRejectedValue(new ApiError(401, 'Incorrect username or password.'));
		const where = render('login');
		for (const [name, value] of [
			['username', 'kate'],
			['password', 'not the password']
		]) {
			const box = where.querySelector<HTMLInputElement>(`input[name="${name}"]`);
			if (!box) throw new Error(`no ${name} box`);
			box.value = value;
			box.dispatchEvent(new Event('input', { bubbles: true }));
		}
		where
			.querySelector('form')
			?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));

		await vi.waitFor(() => {
			flushSync();
			expect(where.querySelector('.problem[role="alert"]')?.textContent).toContain(
				'Incorrect username or password.'
			);
		});
	});
});
