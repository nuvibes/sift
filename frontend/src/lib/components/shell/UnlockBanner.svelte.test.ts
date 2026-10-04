import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { session, type Viewer } from '$lib/shell/session.svelte';
import { imports } from '$lib/library/imports.svelte';
import { REFUSED, unlock } from '$lib/shell/unlock.svelte';
import UnlockBanner from './UnlockBanner.svelte';

/* A restart keeps the session signed in and seals every saved key, so work that needs one parks.
 * A Blocked tab alone never says what it needs. These hold the bar that says so: there the moment the keys are locked, with the field in
 * it, put aside by unlocking or by Not now, and back when work stops for the key.
 */

const VIEWER: Viewer = {
	id: 'u1',
	username: 'wren',
	role: 'admin',
	csrf_token: 't',
	can_save_to_device: false,
	secrets_locked: true,
	pin_unlock_offered: false,
	locked: false,
	zone: null,
	boot: 'run-1'
};

/* Where the bar remembers a Not now in this browser (`unlock.svelte.ts`). */
const KEPT = 'sift.unlock.not-now';

let host: HTMLElement;
let banner: Record<string, unknown> | null = null;
let posted: unknown[] = [];
/* What `/auth/me` says the keys are now, and whether the password is the right one. */
let lockedOnServer = true;
let rightPassword = true;
/* Which run of the server `/auth/me` says answered. */
let bootOnServer = 'run-1';

function render(viewer: Partial<Viewer> = {}) {
	session.viewer = { ...VIEWER, ...viewer };
	host = document.createElement('div');
	document.body.append(host);
	banner = mount(UnlockBanner, { target: host });
	flushSync();
}

const bar = () => host.querySelector('[data-testid="unlock-banner"]');

async function typeAndUnlock(password: string) {
	const box = host.querySelector('input') as HTMLInputElement;
	box.value = password;
	box.dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();
	(host.querySelector('form') as HTMLFormElement).requestSubmit();
}

beforeEach(() => {
	posted = [];
	lockedOnServer = true;
	rightPassword = true;
	bootOnServer = 'run-1';
	imports.passwordWanted = 0;
	// The queue has been read: the count is a count, not "nothing known yet".
	imports.page = {} as unknown as typeof imports.page;
	// Forget a Not now from an earlier test: the aside is forgotten once the keys are unlocked.
	session.viewer = { ...VIEWER, secrets_locked: false };
	unlock.follow(0);
	localStorage.removeItem(KEPT);
	vi.stubGlobal(
		'fetch',
		vi.fn(async (url: URL, init?: RequestInit) => {
			const address = url.toString();
			if (address.endsWith('/api/auth/unlock-secrets')) {
				posted.push(JSON.parse(String(init?.body)));
				if (!rightPassword) {
					return { ok: false, status: 401, json: async () => ({ detail: 'no' }) } as Response;
				}
				lockedOnServer = false;
				return { ok: true, status: 204, json: async () => undefined } as unknown as Response;
			}
			if (address.endsWith('/api/auth/me')) {
				return {
					ok: true,
					status: 200,
					json: async () => ({ ...VIEWER, secrets_locked: lockedOnServer, boot: bootOnServer })
				} as Response;
			}
			throw new Error(`unexpected ${address}`);
		})
	);
});

afterEach(() => {
	if (banner) void unmount(banner, { outro: false });
	banner = null;
	host?.remove();
	vi.unstubAllGlobals();
	session.viewer = undefined;
	imports.page = null;
});

describe('the unlock bar', () => {
	it('asks for the password, with the field in it, while the keys are locked', () => {
		render();

		expect(bar()?.textContent?.replace(/\s+/g, ' ')).toContain(
			'Sift restarted, so your saved keys, cookies and tunnels are locked. Enter your password to unlock them.'
		);
		expect(host.querySelector('input[type="password"]')).not.toBeNull();
		const presses = [...host.querySelectorAll('button')].map((one) => one.textContent?.trim());
		expect(presses).toContain('Unlock');
		expect(presses).toContain('Not now');
	});

	it('says nothing while the keys are unlocked, or to a guest, who holds no key', () => {
		render({ secrets_locked: false });
		expect(bar()).toBeNull();
		session.viewer = { ...VIEWER, role: 'guest' };
		flushSync();
		expect(bar()).toBeNull();
	});

	it('stands aside for Not now and comes back when work stops for the key', () => {
		render();
		imports.passwordWanted = 1;
		flushSync();
		const notNow = [...host.querySelectorAll('button')].find(
			(one) => one.textContent?.trim() === 'Not now'
		)!;
		notNow.click();
		flushSync();
		expect(bar(), 'Not now left the bar up').toBeNull();

		imports.passwordWanted = 2;
		flushSync();
		expect(bar(), 'a task parked for the key did not bring the bar back').not.toBeNull();
	});

	it('comes back for the next task parked after some went', () => {
		render();
		imports.passwordWanted = 3;
		flushSync();
		unlock.notNow(3);
		flushSync();
		imports.passwordWanted = 0;
		flushSync();
		expect(bar()).toBeNull();
		imports.passwordWanted = 1;
		flushSync();
		expect(bar()).not.toBeNull();
	});

	it('unlocks with the password and goes', async () => {
		render();
		await typeAndUnlock('correct horse');

		await vi.waitFor(() => expect(bar()).toBeNull());
		expect(posted).toEqual([{ password: 'correct horse' }]);
		expect(session.secretsLocked).toBe(false);
	});

	it('says so when the password is refused, and stays', async () => {
		rightPassword = false;
		render();
		await typeAndUnlock('wrong');

		await vi.waitFor(() => expect(host.querySelector('[role="alert"]')?.textContent).toBe(REFUSED));
		expect(bar()).not.toBeNull();
	});
});

describe('a window open across a restart', () => {
	it('learns its keys are locked when it asks again, and nothing else of the session moves', async () => {
		render({ secrets_locked: false });
		const before = session.viewer;
		expect(bar()).toBeNull();

		await session.recheck();
		flushSync();

		expect(bar(), 'the bar never appeared for a window open across a restart').not.toBeNull();
		expect(session.viewer, 'the whole session was replaced for one flag').toBe(before);
	});
});

describe('a Not now across a reload', () => {
	/* A page loaded again: what the page held is gone and what the browser keeps is not. */
	function reload() {
		const kept = localStorage.getItem(KEPT);
		if (banner) void unmount(banner, { outro: false });
		banner = null;
		host.remove();
		session.viewer = { ...VIEWER, secrets_locked: false };
		unlock.follow(0);
		if (kept !== null) localStorage.setItem(KEPT, kept);
		imports.page = null;
		imports.passwordWanted = 0;
		render();
	}

	it('holds until the next restart, and a page that has counted nothing yet does not undo it', () => {
		render();
		imports.passwordWanted = 2;
		flushSync();
		[...host.querySelectorAll('button')]
			.find((one) => one.textContent?.trim() === 'Not now')!
			.click();
		flushSync();
		expect(JSON.parse(localStorage.getItem(KEPT)!)).toEqual({ user: 'u1', boot: 'run-1', at: 2 });

		reload();
		expect(bar(), 'a reload asked again').toBeNull();
		// The queue arrives with the same two parked: still aside. A third brings it back.
		imports.page = {} as unknown as typeof imports.page;
		imports.passwordWanted = 2;
		flushSync();
		expect(bar(), 'the count arriving after the reload brought the bar back').toBeNull();
		imports.passwordWanted = 3;
		flushSync();
		expect(bar()).not.toBeNull();
	});

	it('asks again after a restart, which is a new run of the server', async () => {
		render();
		unlock.notNow(0);
		flushSync();
		expect(bar()).toBeNull();

		reload();
		expect(bar()).toBeNull();
		bootOnServer = 'run-2';
		await session.recheck();
		flushSync();
		expect(session.viewer?.boot).toBe('run-2');
		expect(bar(), 'a Not now from before the restart held after it').not.toBeNull();
	});

	it('works without the browser keeping anything', () => {
		const refuse = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
			throw new Error('blocked');
		});
		try {
			render();
			unlock.notNow(0);
			flushSync();
			expect(bar(), 'Not now did nothing where the browser keeps nothing').toBeNull();
		} finally {
			refuse.mockRestore();
		}
	});
});
