/* The rating scale: what is shown against what is stored, and a typed query moved between them. */

import { beforeEach, expect, it, vi } from 'vitest';

vi.mock('$lib/settings-ui/settings', () => ({
	fetchSettingValues: vi.fn(async () => ({})),
	onSettingsSaved: vi.fn(() => () => {})
}));

import { ratingScale } from './rating.svelte';

beforeEach(() => {
	ratingScale.stars = 5;
});

it('moves a typed rating from the stars on screen to the units the rows hold', () => {
	/* Unconverted, at five stars `rating:5` would answer nothing (nothing is stored above ten), and
	 * `rating:4+` would mean seven stored, which shows as four stars and is three and a half. */
	expect(ratingScale.storedQuery('rating:5')).toBe('rating:10');
	expect(ratingScale.storedQuery('rating:4+')).toBe('rating:8+');
	expect(ratingScale.storedQuery('rating:2-4 tags:beach')).toBe('rating:4-8 tags:beach');
	expect(ratingScale.storedQuery('tags:beach RATING:3')).toBe('tags:beach rating:6');
});

it('leaves everything that is not a rating alone', () => {
	expect(ratingScale.storedQuery('tags:beach type:video rating:none')).toBe(
		'tags:beach type:video rating:none'
	);
	expect(ratingScale.storedQuery('')).toBe('');
});

it('moves nothing at ten stars, where the shown scale is the stored one', () => {
	ratingScale.stars = 10;
	expect(ratingScale.storedQuery('rating:7+')).toBe('rating:7+');
});
