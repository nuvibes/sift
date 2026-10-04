/* The timer that shuts Sift itself, and the one thing that has to be true before it may.
 *
 * The other triggers in this file shut Hidden, which is always reopenable: the worst a wrong one
 * costs is a PIN entry. This one shuts the session, and the only thing that reopens it is the PIN.
 * Fired against an account that has not set one, it is not a lock but a lockout: every attempt is
 * refused, and after a few of them the session is destroyed and the password is what comes back.
 *
 * So this file is about the guard rather than about the timer. The shortcut takes the same reading
 * and signs out instead, because a panic control has to do something; a quiet spell does not.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import LockTriggers from './LockTriggers.svelte';
import { watching } from '$lib/player/watching.svelte';
import {
	APP_LOCK_AFTER_IDLE_KEY,
	APP_LOCK_ENABLED_KEY,
	LOCK_AFTER_IDLE_KEY,
	LOCK_ON_BLUR_KEY,
	LOCK_ON_CLOSE_KEY,
	LOCK_ON_LAUNCH_KEY,
	CONCEALMENT_KEY,
	type VaultPreferences
} from '$lib/shell/vault-preferences';

const mocks = vi.hoisted(() => ({
	post: vi.fn(async () => undefined),
	prefs: null as unknown as VaultPreferences,
	vault: {
		unlocked: false,
		pinSet: false,
		lock: vi.fn(async () => true),
		load: vi.fn(async () => {}),
		sessionLocked: vi.fn()
	}
}));

vi.mock('$app/navigation', () => ({ goto: () => Promise.resolve() }));
vi.mock('$lib/api/client', () => ({ api: { post: mocks.post } }));
vi.mock('$lib/shell/vault.svelte', () => ({ vault: mocks.vault }));
vi.mock('$lib/shell/toasts.svelte', () => ({ toasts: { show: () => {} } }));
vi.mock('$lib/shell/vault-preferences', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/shell/vault-preferences')>()),
	readVaultPreferences: async () => mocks.prefs
}));

/* Everything else off, so the only thing that can fire in a test here is the Sift lock. The two
 * settings under test are written by each test on top of this. */
function quiet(): VaultPreferences {
	return {
		[CONCEALMENT_KEY]: 'fully_gone',
		[LOCK_AFTER_IDLE_KEY]: 0,
		[LOCK_ON_BLUR_KEY]: false,
		[LOCK_ON_LAUNCH_KEY]: false,
		[LOCK_ON_CLOSE_KEY]: false,
		[APP_LOCK_ENABLED_KEY]: true,
		[APP_LOCK_AFTER_IDLE_KEY]: 5
	};
}

let host: HTMLElement;
let component: Record<string, unknown> | undefined;

beforeEach(() => {
	vi.useFakeTimers();
	mocks.post.mockClear();
	mocks.prefs = quiet();
	mocks.vault.unlocked = false;
	mocks.vault.pinSet = true;
	mocks.vault.lock.mockClear();
});

afterEach(() => {
	if (component) unmount(component);
	component = undefined;
	host?.remove();
	vi.useRealTimers();
});

/** Mount, and let the async read of the preferences inside `onMount` settle before the clock runs. */
async function render(): Promise<void> {
	host = document.createElement('div');
	document.body.append(host);
	component = mount(LockTriggers, { target: host }) as Record<string, unknown>;
	flushSync();
	await vi.advanceTimersByTimeAsync(0);
	flushSync();
}

describe('the timer that locks Sift', () => {
	it('asks the server to lock once the quiet spell is up', async () => {
		await render();

		await vi.advanceTimersByTimeAsync(5 * 60_000);

		expect(mocks.post).toHaveBeenCalledWith('/auth/lock');
	});

	it('does nothing at all for an account with no PIN', async () => {
		/* The one this file exists for. Locking an account that has nothing to unlock with leaves it
		 * at a door with no key, and a quiet spell is not a thing that should do that on its own. */
		mocks.vault.pinSet = false;

		await render();
		await vi.advanceTimersByTimeAsync(5 * 60_000);

		expect(mocks.post).not.toHaveBeenCalled();
	});

	it('does nothing while the setting is off, PIN or no PIN', async () => {
		/* The other half of the guard, and the reason the test above is not enough on its own: a
		 * timer that never fired would also pass it. */
		mocks.prefs[APP_LOCK_ENABLED_KEY] = false;

		await render();
		await vi.advanceTimersByTimeAsync(5 * 60_000);

		expect(mocks.post).not.toHaveBeenCalled();
	});

	it('treats zero minutes as never', async () => {
		mocks.prefs[APP_LOCK_AFTER_IDLE_KEY] = 0;

		await render();
		await vi.advanceTimersByTimeAsync(24 * 60 * 60_000);

		expect(mocks.post).not.toHaveBeenCalled();
	});
});

/* --- watching a video is using Sift ------------------------------------------------------------ */

describe('a quiet spell while something is playing', () => {
	/* The fault this is for: the triggers in this file are keys, scrolling and the pointer, and
	 * somebody watching a forty-minute film touches none of them. By every measure the timers have,
	 * they have left, so without this the timer would fire, the shell would go to the lock screen,
	 * and the film would go with it. A media application whose idle clock ignores playback is measuring the wrong thing. */

	afterEach(() => {
		while (watching.anything) watching.stopped();
	});

	it('does not lock Sift while a video is playing', async () => {
		watching.started();

		await render();
		await vi.advanceTimersByTimeAsync(30 * 60_000);

		expect(mocks.post).not.toHaveBeenCalled();
	});

	it('does not shut Hidden while a video is playing', async () => {
		mocks.prefs = { ...quiet(), [LOCK_AFTER_IDLE_KEY]: 5 };
		mocks.vault.unlocked = true;
		watching.started();

		await render();
		await vi.advanceTimersByTimeAsync(30 * 60_000);

		expect(mocks.vault.lock).not.toHaveBeenCalled();
	});

	it('starts the clock from the moment it stops, not from before it started', async () => {
		/* The half that a veto alone would get wrong, and the more dangerous half: a film that
		 * ENDED must not leave both timers disarmed until somebody touches a key. */
		watching.started();
		await render();
		await vi.advanceTimersByTimeAsync(30 * 60_000);
		expect(mocks.post).not.toHaveBeenCalled();

		watching.stopped();
		flushSync();
		await vi.advanceTimersByTimeAsync(5 * 60_000);

		expect(mocks.post).toHaveBeenCalledWith('/auth/lock');
	});

	it('goes on counting while two players are handing a clip between them', async () => {
		/* A count, not a flag. The panel and the full-size view are both alive for an instant during
		 * a handover, and a flag cleared by the first to stop reads as "nothing is playing" in
		 * exactly that instant. */
		watching.started();
		watching.started();
		await render();

		watching.stopped();
		flushSync();
		await vi.advanceTimersByTimeAsync(30 * 60_000);

		expect(mocks.post).not.toHaveBeenCalled();
	});
});

/* --- the triggers that shut Hidden, each read from `Settings > Privacy` ----------------------- */

describe('the triggers that shut Hidden', () => {
	beforeEach(() => {
		// The Sift lock off, so only Hidden's triggers can act.
		mocks.prefs = { ...quiet(), [APP_LOCK_ENABLED_KEY]: false };
		mocks.vault.unlocked = true;
	});

	it('shuts Hidden once the quiet spell the setting names is up, and not before', async () => {
		mocks.prefs[LOCK_AFTER_IDLE_KEY] = 5;

		await render();
		await vi.advanceTimersByTimeAsync(4 * 60_000);
		expect(mocks.vault.lock).not.toHaveBeenCalled();
		await vi.advanceTimersByTimeAsync(60_000);

		expect(mocks.vault.lock).toHaveBeenCalledTimes(1);
	});

	it('shuts Hidden when the window is left, only while the setting asks', async () => {
		await render();
		window.dispatchEvent(new Event('blur'));
		await vi.advanceTimersByTimeAsync(0);
		expect(mocks.vault.lock, 'left while the setting was off').not.toHaveBeenCalled();
		unmount(component as Record<string, unknown>);
		component = undefined;

		mocks.prefs[LOCK_ON_BLUR_KEY] = true;
		await render();
		window.dispatchEvent(new Event('blur'));
		await vi.advanceTimersByTimeAsync(0);

		expect(mocks.vault.lock).toHaveBeenCalledTimes(1);
	});

	it('shuts Hidden as the tab closes, only while the setting asks', async () => {
		await render();
		window.dispatchEvent(new Event('pagehide'));
		expect(mocks.vault.lock, 'closed while the setting was off').not.toHaveBeenCalled();
		unmount(component as Record<string, unknown>);
		component = undefined;

		mocks.prefs[LOCK_ON_CLOSE_KEY] = true;
		await render();
		window.dispatchEvent(new Event('pagehide'));

		expect(mocks.vault.lock).toHaveBeenCalledWith({ keepalive: true });
	});
});
