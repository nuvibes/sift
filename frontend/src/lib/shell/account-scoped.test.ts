/* Signing in as somebody else has to throw away the last account's preferences.
 *
 * The desktop shell's page never reloads. Signing out and back in is a client-side navigation, so
 * a store that reads once and remembers that it did ("once" meaning once per PAGE) would give
 * the second account the first one's answers until the window is closed, and a desktop window and
 * a browser tab on the same account would show two different themes. Nothing errors and nothing
 * on screen says why.
 *
 * Every store below keeps a `#loading` promise as that memo, so the assertion that matters is not
 * "the value went back to its default" but "asking again actually asks the server again". A store
 * reset to its defaults with the memo left in place is the worse failure of the two: it looks
 * right for a moment and then never corrects.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
	forgetAccountScopedPreferences,
	loadAccountScopedPreferences
} from '$lib/shell/account-scoped';
import { appearance } from '$lib/theme/appearance.svelte';
import { ratingScale } from '$lib/library/rating.svelte';
import { theme } from '$lib/theme/theme.svelte';
import { tileMarks } from '$lib/grid/tile-marks.svelte';
import { fetchSettingValues } from '$lib/settings-ui/settings';

vi.mock('$lib/settings-ui/settings', () => ({
	fetchSettingValues: vi.fn(async () => new Map<string, unknown>()),
	saveSettings: vi.fn(async () => undefined),
	onSettingsSaved: vi.fn()
}));

const asked = vi.mocked(fetchSettingValues);

/** Every store holding an answer that belongs to one account. Named here so a new one has to be
 *  added twice, once to the module and once to its test, which is the whole of what stops the
 *  list going quietly one short. */
const STORES = [
	{ name: 'theme', store: theme },
	{ name: 'the star scale', store: ratingScale },
	{ name: 'the tile marks', store: tileMarks },
	{ name: 'the units a measurement is read in', store: appearance }
];

beforeEach(() => {
	/* These stores are module-scope singletons, so what one test leaves in them is what the next
	   one starts with, including a SATISFIED memo, which would make the next `load()` a no-op and
	   quietly turn the assertion below into a test of nothing. */
	forgetAccountScopedPreferences();
	asked.mockClear();
	asked.mockResolvedValue(new Map<string, unknown>());
});

describe('reading what belongs to an account', () => {
	/* The other half of the same list: every store forgotten when the account changes is also
	 * read on the way in. A store forgotten and not read would be filled by whichever screen
	 * happened to ask. And a wall of people asks for none of them, so its height facet's band
	 * labels would read centimetres under an imperial account.
	 *
	 * Asserted per store rather than by counting the calls, so a shorter list fails on the name
	 * of the store that went missing.
	 */
	it.each(STORES)('asks the server for $name', async ({ store }) => {
		loadAccountScopedPreferences();
		await store.load();

		expect(asked).toHaveBeenCalled();
		// The memo is satisfied, which is what says THIS store's read was the one made above rather
		// than the one this test just made.
		expect(asked).toHaveBeenCalledTimes(STORES.length);
	});

	it('leaves the answers on the stores that hold them', async () => {
		asked.mockResolvedValue(
			new Map<string, unknown>([
				['appearance.theme_base', 'chrome'],
				['ratings.scale', 10],
				['appearance.units', 'imperial']
			])
		);

		loadAccountScopedPreferences();
		await Promise.all(STORES.map(({ store }) => store.load()));

		expect(theme.base).toBe('chrome');
		expect(ratingScale.stars).toBe(10);
		expect(appearance.units).toBe('imperial');
	});

	it('reads which system a measurement is read in, which no wall of people asks for', async () => {
		/* The one store tested on its own, with nothing else loading it. Every other test here
		   loads each store by hand to satisfy its memo, which is what the screens do. And a
		   gap hides behind that: the answer arrives because something on the page happened to
		   ask. Nothing asks here. */
		asked.mockResolvedValue(new Map<string, unknown>([['appearance.units', 'imperial']]));

		loadAccountScopedPreferences();

		await vi.waitFor(() => expect(appearance.units).toBe('imperial'));
	});
});

describe('forgetting what belongs to an account', () => {
	it.each(STORES)('makes $name ask the server again', async ({ store }) => {
		await store.load();
		expect(asked).toHaveBeenCalledTimes(1);

		// Without forgetting, this is the bug: the memo answers and nothing is asked.
		await store.load();
		expect(asked).toHaveBeenCalledTimes(1);

		forgetAccountScopedPreferences();
		await store.load();

		expect(asked).toHaveBeenCalledTimes(2);
	});

	it('leaves nothing of the last account on the screen while the next read is in flight', async () => {
		// The gap between forgetting and the new answer landing is a real moment somebody looks at.
		// It has to show a fresh install rather than the previous person's choices.
		asked.mockResolvedValue(
			new Map<string, unknown>([
				['appearance.theme_base', 'chrome'],
				['ratings.scale', 10],
				['appearance.units', 'metric']
			])
		);
		await Promise.all(STORES.map(({ store }) => store.load()));
		expect(theme.base).toBe('chrome');
		expect(ratingScale.stars).toBe(10);
		expect(appearance.units).toBe('metric');

		forgetAccountScopedPreferences();

		expect(theme.base).toBe('midnight');
		expect(ratingScale.stars).toBe(5);
		expect(appearance.units).toBe('imperial');
	});

	it('does not let the last account read land after the change', async () => {
		// A read that was in the air when the account changed describes the PREVIOUS account. Left
		// to land it writes their answers over a screen that has already moved on, and the only
		// thing that puts it right is the next read finishing.
		let answer: (values: Map<string, unknown>) => void = () => {};
		asked.mockReturnValue(
			new Promise<Map<string, unknown>>((resolve) => {
				answer = resolve;
			})
		);
		const inFlight = ratingScale.load();

		forgetAccountScopedPreferences();
		answer(new Map<string, unknown>([['ratings.scale', 10]]));
		await inFlight;

		expect(ratingScale.stars).toBe(5);
	});

	it('marks the stores that report readiness as not ready', () => {
		// A pane that waits for `loaded` before drawing which answer is on would otherwise draw the
		// defaults as though somebody had chosen them.
		tileMarks.forget();
		appearance.forget();

		expect(tileMarks.loaded).toBe(false);
		expect(appearance.loaded).toBe(false);
	});
});
