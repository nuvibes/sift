/*
 * Signing in as somebody else throws away the last account's preferences: the desktop page never
 * reloads, so what matters is that asking again really asks the server again.
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

/** Named here too, so a new store has to be added twice. */
const STORES = [
	{ name: 'theme', store: theme },
	{ name: 'the star scale', store: ratingScale },
	{ name: 'the tile marks', store: tileMarks },
	{ name: 'the units a measurement is read in', store: appearance }
];

beforeEach(() => {
	/* Module singletons: a satisfied memo left by one test would make the next `load()` a no-op. */
	forgetAccountScopedPreferences();
	asked.mockClear();
	asked.mockResolvedValue(new Map<string, unknown>());
});

describe('reading what belongs to an account', () => {
	/*
	 * Every store forgotten is also read on the way in, asserted per store so a missing one is
	 * named.
	 */
	it.each(STORES)('asks the server for $name', async ({ store }) => {
		loadAccountScopedPreferences();
		await store.load();

		expect(asked).toHaveBeenCalled();
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
		/* The one store with nothing else loading it: nothing asks here but the module. */
		asked.mockResolvedValue(new Map<string, unknown>([['appearance.units', 'imperial']]));

		loadAccountScopedPreferences();

		await vi.waitFor(() => expect(appearance.units).toBe('imperial'));
	});
});

describe('forgetting what belongs to an account', () => {
	it.each(STORES)('makes $name ask the server again', async ({ store }) => {
		await store.load();
		expect(asked).toHaveBeenCalledTimes(1);

		await store.load();
		expect(asked).toHaveBeenCalledTimes(1);

		forgetAccountScopedPreferences();
		await store.load();

		expect(asked).toHaveBeenCalledTimes(2);
	});

	it('leaves nothing of the last account on the screen while the next read is in flight', async () => {
		// The gap before the new answer lands shows a fresh install, not the last person's choices.
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
		// A read in the air when the account changed must not land.
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
		tileMarks.forget();
		appearance.forget();

		expect(tileMarks.loaded).toBe(false);
		expect(appearance.loaded).toBe(false);
	});
});
