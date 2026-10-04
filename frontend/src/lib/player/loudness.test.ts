/* ONE LEVEL FOR THE WHOLE APPLICATION, AND IT IS REMEMBERED.
 *
 * The half that is worth a test is the WRITING, because it is the half nobody sees fail: a level
 * that applies on screen and is never saved looks exactly like one that was saved, right up until
 * the next sitting. The reading half is one line and is proved where it is used.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { saveSettings } from '$lib/settings-ui/settings';
import { loudness } from './loudness.svelte';

vi.mock('$lib/settings-ui/settings', () => ({
	saveSettings: vi.fn(async () => {}),
	onSettingsSaved: vi.fn()
}));

beforeEach(() => {
	vi.mocked(saveSettings).mockClear();
	vi.useFakeTimers();
	loudness.heard(100);
});

/* Put the clock back. The shared setup has an `afterAll` that waits on a real timer, and a suite
   that leaves fake ones installed hangs it, which reads as this file being slow rather than as
   this file having broken the one after it. */
afterEach(() => {
	vi.useRealTimers();
});

describe('how loud Sift is', () => {
	it('writes a level down once the moving stops, rather than on every step of it', () => {
		loudness.set(40);
		loudness.set(30);
		loudness.set(25);

		expect(
			vi.mocked(saveSettings),
			'a drag is one decision and a hundred events'
		).not.toHaveBeenCalled();

		vi.runAllTimers();

		expect(vi.mocked(saveSettings)).toHaveBeenCalledTimes(1);
		expect(vi.mocked(saveSettings)).toHaveBeenCalledWith({ 'playback.volume': 25 });
	});

	it('writes nothing at all for a level that ends where it started', () => {
		loudness.set(60);
		vi.runAllTimers();
		vi.mocked(saveSettings).mockClear();

		loudness.set(30);
		loudness.set(60);
		vi.runAllTimers();

		expect(vi.mocked(saveSettings)).not.toHaveBeenCalled();
	});

	it('does not write back a level the account just told it', () => {
		// Otherwise every screen that reads the preferences saves them again on the way past, and a
		// slider somebody is holding races a read that was already in the air.
		loudness.heard(35);
		vi.runAllTimers();

		expect(loudness.level).toBe(35);
		expect(vi.mocked(saveSettings)).not.toHaveBeenCalled();
	});

	it('keeps a level inside what an element will take, whatever it is handed', () => {
		loudness.set(140);
		expect(loudness.level).toBe(100);

		loudness.set(-20);
		expect(loudness.level).toBe(0);

		loudness.heard('not a number');
		expect(loudness.level, 'a settings row that is not a number emptied the level').toBe(0);
	});

	it('flushes what is still waiting when a player is on its way out', () => {
		// The commonest moment for a level to be set is the last half second before a player closes,
		// which is exactly the one somebody expects to have stuck.
		loudness.set(45);

		loudness.settle();

		expect(vi.mocked(saveSettings)).toHaveBeenCalledWith({ 'playback.volume': 45 });
	});
});
