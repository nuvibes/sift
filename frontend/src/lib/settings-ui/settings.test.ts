/* Reading preferences out of the shape the server actually sends. */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '$lib/api/client';
import {
	fetchSettingValues,
	fetchSettings,
	onSettingsSaved,
	saveSettings
} from '$lib/settings-ui/settings';
import { settingChanges } from '$lib/library/changes.svelte';
import { session, type Viewer } from '$lib/shell/session.svelte';

vi.mock('$lib/api/client', () => ({
	api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() },
	ApiError: class extends Error {}
}));

const mocked = vi.mocked(api);

/** What `GET /settings` really answers with: sections in a list, entries inside each one. */
const ANSWER = {
	sections: [
		{ name: 'Library', settings: [] },
		{
			name: 'Privacy',
			settings: [
				{ key: 'vault.concealment', value: 'placeholder' },
				{ key: 'vault.lock_on_blur', value: true }
			]
		},
		{ name: 'Playback', settings: [{ key: 'playback.loop_mode', value: 'loop_all' }] }
	]
};

beforeEach(() => {
	vi.resetAllMocks();
});

describe('reading', () => {
	it('finds a value that lives inside a section', async () => {
		mocked.get.mockResolvedValue(ANSWER);

		const values = await fetchSettingValues();

		expect(values.get('vault.concealment')).toBe('placeholder');
		expect(values.get('vault.lock_on_blur')).toBe(true);
	});

	it('reaches across sections rather than stopping at the first', async () => {
		mocked.get.mockResolvedValue(ANSWER);

		const values = await fetchSettingValues();

		expect(values.get('playback.loop_mode')).toBe('loop_all');
	});

	it('has nothing to say about a key nobody registered', async () => {
		mocked.get.mockResolvedValue(ANSWER);

		expect((await fetchSettingValues()).get('nothing.here')).toBeUndefined();
	});

	it('survives a section that carries no list of its own', async () => {
		// An empty section arrives as `{name}` with no `settings` in some answers, and flattening
		// straight through it throws a TypeError that takes the whole settings screen down, from a
		// section that has nothing in it, which is the most ordinary state there is.
		mocked.get.mockResolvedValue({
			sections: [{ name: 'Library' }, { name: 'Privacy', settings: [{ key: 'k', value: 1 }] }]
		});

		expect((await fetchSettingValues()).get('k')).toBe(1);
	});

	it('survives an answer with no sections at all', async () => {
		mocked.get.mockResolvedValue({});

		expect(await fetchSettings()).toEqual([]);
		expect((await fetchSettingValues()).size).toBe(0);
	});

	it('keeps the sections and their order for the screen that draws them', async () => {
		mocked.get.mockResolvedValue(ANSWER);

		expect((await fetchSettings()).map((one) => one.name)).toEqual([
			'Library',
			'Privacy',
			'Playback'
		]);
	});
});

describe('a page load', () => {
	it('asks once, however many stores want a preference at the same moment', async () => {
		mocked.get.mockResolvedValue(ANSWER);

		const answers = await Promise.all([
			fetchSettingValues(),
			fetchSettingValues(),
			fetchSettings(),
			fetchSettingValues(),
			fetchSettingValues(),
			fetchSettingValues()
		]);

		expect(mocked.get).toHaveBeenCalledTimes(1);
		expect((answers[0] as Map<string, unknown>).get('playback.loop_mode')).toBe('loop_all');
		// Each caller has its own copy.
		const [one, other] = await Promise.all([fetchSettings(), fetchSettings()]);
		expect(one).not.toBe(other);
		expect(one).toEqual(other);
	});

	it('asks again once the answer is in, and never shares a read a save began after', async () => {
		mocked.get.mockResolvedValue(ANSWER);
		mocked.put.mockResolvedValue(undefined);

		await fetchSettingValues();
		await fetchSettingValues();
		expect(mocked.get).toHaveBeenCalledTimes(2);

		// The save begins while the first read is still in the air, and the read asked for after it
		// must not be handed the answer from before it.
		const before = fetchSettingValues();
		const saving = saveSettings({ 'playback.loop_mode': 'loop_one' });
		const after = fetchSettingValues();
		await Promise.all([before, saving, after]);
		expect(mocked.get).toHaveBeenCalledTimes(4);
	});
});

describe('the session copy', () => {
	function signedInAs(id: string | null): void {
		session.viewer = id === null ? null : ({ id } as Viewer);
	}

	it('reads once for a signed-in account, and again after a save or for another account', async () => {
		mocked.get.mockResolvedValue(ANSWER);
		mocked.put.mockResolvedValue(undefined);
		try {
			signedInAs('someone');
			await fetchSettingValues();
			const again = await fetchSettingValues();
			expect(mocked.get).toHaveBeenCalledTimes(1);
			expect(again.get('vault.lock_on_blur')).toBe(true);
			// A copy each: a caller that changes what it was handed must not change the next one's.
			again.set('vault.lock_on_blur', false);
			expect((await fetchSettingValues()).get('vault.lock_on_blur')).toBe(true);

			await saveSettings({ 'playback.loop_mode': 'loop_one' });
			await fetchSettingValues();
			expect(mocked.get).toHaveBeenCalledTimes(2);

			signedInAs('somebody else');
			await fetchSettingValues();
			expect(mocked.get).toHaveBeenCalledTimes(3);

			// Nobody signed in: nothing is kept for nobody.
			signedInAs(null);
			await fetchSettingValues();
			await fetchSettingValues();
			expect(mocked.get).toHaveBeenCalledTimes(5);
		} finally {
			session.viewer = undefined;
		}
	});

	it('reads again when a setting moved somewhere else', async () => {
		mocked.get.mockResolvedValue(ANSWER);
		try {
			signedInAs('someone');
			await fetchSettingValues();
			settingChanges.changed();
			await vi.waitFor(() => expect(mocked.get).toHaveBeenCalledTimes(2));
			await fetchSettingValues();
			expect(mocked.get).toHaveBeenCalledTimes(2);
		} finally {
			session.viewer = undefined;
		}
	});
});

describe('writing', () => {
	it('wraps the batch, because a bare map is refused as malformed', async () => {
		mocked.put.mockResolvedValue(undefined);

		await saveSettings({ 'vault.lock_on_blur': true });

		expect(mocked.put).toHaveBeenCalledWith('/settings', {
			body: { values: { 'vault.lock_on_blur': true } }
		});
	});
});

describe('being told a setting moved somewhere else', () => {
	/* The whole point of the mechanism. */
	it('re-reads and hands the values to the watchers', async () => {
		const told = vi.fn();
		onSettingsSaved(told);
		mocked.get.mockResolvedValue(ANSWER);

		settingChanges.changed();
		await vi.waitFor(() => expect(told).toHaveBeenCalled());

		expect(told.mock.calls[0][0]['vault.lock_on_blur']).toBe(true);
	});

	/* A read that was in the air when a save began describes the state BEFORE that save. */
	it('drops a read that a save overtook while it was in the air', async () => {
		const told = vi.fn();
		onSettingsSaved(told);

		let answer: (value: unknown) => void = () => {};
		mocked.get.mockReturnValue(new Promise((resolve) => (answer = resolve)));
		settingChanges.changed();

		mocked.put.mockResolvedValue(undefined);
		await saveSettings({ 'appearance.theme_base': 'chrome' });
		const afterTheSave = told.mock.calls.length;

		answer(ANSWER);
		/* A macrotask, not two microtask ticks: the answer travels through `api.get`, then
		 * `fetchSettings`, then `fetchSettingValues`, then the `.then`, and a count of ticks that
		 * is one short reads exactly like a guard that worked. */
		await new Promise((settled) => setTimeout(settled, 0));

		// The save's own call still counts: that one is not stale. The read behind it does not.
		expect(told.mock.calls.length).toBe(afterTheSave);

		/* THE KNOWN POSITIVE. */
		mocked.get.mockResolvedValue(ANSWER);
		settingChanges.changed();
		await vi.waitFor(() => expect(told.mock.calls.length).toBe(afterTheSave + 1));
	});
});
