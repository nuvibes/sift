/**
 * The marks a tile carries, and following a change made somewhere else.
 *
 * These are read by every wall in the application. Read ONCE per page load, a mark turned off in a
 * browser would stay on in the desktop window (whose page never reloads) until it was
 * restarted, and nothing anywhere would say why the two disagreed. The server announces the change,
 * and this listens for it.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

const fetchSettingValues = vi.fn();
const saveSettings = vi.fn();

vi.mock('$lib/settings-ui/settings', () => ({
	fetchSettingValues: (...args: unknown[]) => fetchSettingValues(...args),
	saveSettings: (...args: unknown[]) => saveSettings(...args),
	// The store registers its watcher at module scope, so a partial mock without this fails the
	// suite at import. Stubbed rather than driven: what is worth pinning down is `follow` itself.
	onSettingsSaved: vi.fn()
}));

const {
	ALWAYS,
	DURATION_MARK,
	FAVORITE_MARK,
	MARK_KEYS,
	NEVER,
	ON_HOVER,
	ON_HOVER_CLASS,
	VIEWS_MARK,
	tileMarks
} = await import('./tile-marks.svelte');

/** The store is a singleton, the same as it is in the app, and there is no sign-out to reset it. */
beforeEach(() => {
	tileMarks.follow({ [VIEWS_MARK]: ALWAYS, [DURATION_MARK]: ALWAYS, [FAVORITE_MARK]: ON_HOVER });
	fetchSettingValues.mockReset();
	saveSettings.mockReset();
});

describe('a mark answered somewhere else', () => {
	it('is taken', () => {
		tileMarks.follow({ [DURATION_MARK]: NEVER });

		expect(tileMarks.answer(DURATION_MARK)).toBe(NEVER);
		expect(tileMarks.shows(DURATION_MARK)).toBe(false);
	});

	it('leaves alone the marks the change did not mention', () => {
		// Away from their defaults first, or the assertion cannot fail: a `follow` that wrongly
		// reset an absent key would reset it to the default, which is what the store already held.
		tileMarks.follow({ [VIEWS_MARK]: NEVER, [FAVORITE_MARK]: ALWAYS });

		tileMarks.follow({ [DURATION_MARK]: ON_HOVER });

		expect(tileMarks.answer(DURATION_MARK)).toBe(ON_HOVER);
		expect(tileMarks.answer(VIEWS_MARK)).toBe(NEVER);
		expect(tileMarks.answer(FAVORITE_MARK)).toBe(ALWAYS);
	});

	/* An answer a later Sift wrote and this one has never heard of. Left where it was rather than
	 * reset, which is the reading `load` takes too: a preference from a newer version must not
	 * strip the marks off a tile. */
	it('ignores an answer this version does not know', () => {
		tileMarks.follow({ [VIEWS_MARK]: NEVER });

		tileMarks.follow({ [VIEWS_MARK]: 'whenever-it-rains' });

		expect(tileMarks.answer(VIEWS_MARK)).toBe(NEVER);
	});

	it('ignores a setting that is not a mark at all', () => {
		tileMarks.follow({ 'playback.volume': 40 });

		for (const key of MARK_KEYS) expect(tileMarks.answer(key)).not.toBe(undefined);
		expect(tileMarks.answer(VIEWS_MARK)).toBe(ALWAYS);
	});

	/* What the tile actually puts in its class attribute. The mark is drawn either way (it is CSS
	 * that reveals it on hover), so `shows` and `revealed` are two different questions. */
	it('reveals on hover rather than hiding, when that is the answer', () => {
		tileMarks.follow({ [DURATION_MARK]: ON_HOVER });

		expect(tileMarks.shows(DURATION_MARK)).toBe(true);
		expect(tileMarks.revealed(DURATION_MARK)).toBe(ON_HOVER_CLASS);
		expect(tileMarks.revealed(VIEWS_MARK)).toBe('');
	});
});
