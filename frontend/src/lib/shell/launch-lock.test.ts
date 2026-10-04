/* The launch lock: Hidden is shut before anything is read back, where the preferences ask for it.
 * The layout awaits this before it draws a screen, so a screen's first list cannot race it. */

import { beforeEach, describe, expect, it, vi } from 'vitest';

const settings = vi.hoisted(() => ({ values: new Map<string, unknown>() }));
vi.mock('$lib/settings-ui/settings', () => ({ fetchSettingValues: async () => settings.values }));

import { LOCK_ON_LAUNCH_KEY, lockOnLaunch } from '$lib/shell/vault-preferences';

beforeEach(() => settings.values.clear());

describe('lockOnLaunch', () => {
	it('shuts Hidden, and has shut it before it answers, when the preference asks', async () => {
		settings.values.set(LOCK_ON_LAUNCH_KEY, true);
		let landed = false;
		// A lock that takes a moment, as a request does: answered before it lands would be too soon.
		const lock = vi.fn(async () => {
			await new Promise((done) => setTimeout(done, 5));
			landed = true;
		});

		const loaded = await lockOnLaunch(lock);

		expect(lock).toHaveBeenCalledOnce();
		expect(landed).toBe(true);
		expect(loaded[LOCK_ON_LAUNCH_KEY]).toBe(true);
	});

	it('leaves Hidden as it is when the preference is off', async () => {
		settings.values.set(LOCK_ON_LAUNCH_KEY, false);
		const lock = vi.fn(async () => undefined);

		await lockOnLaunch(lock);

		expect(lock).not.toHaveBeenCalled();
	});
});
