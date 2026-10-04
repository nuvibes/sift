/* The vault's preferences, and the one of them that must never be guessed.
 *
 * `vault.lock_after_idle_minutes` is the only setting here where a wrong reading is a privacy
 * change rather than an annoyance: 0 means "never lock on its own". So an empty box (somebody
 * midway through typing a new number) must not be read as 0, and that is what this pins.
 */

import { describe, expect, it } from 'vitest';

import { MAX_IDLE_MINUTES, idleMinutesFrom } from '$lib/shell/vault-preferences';

describe('reading a typed idle timeout', () => {
	it('leaves an empty box alone rather than reading it as never-lock', () => {
		// `valueAsNumber` is NaN for an empty number input, and `NaN || 0` is 0, which is the least
		// private setting there is, arrived at by somebody who was about to type 5.
		expect(idleMinutesFrom(Number.NaN)).toBeNull();
	});

	it('keeps zero when it is actually typed, because zero is a real choice', () => {
		expect(idleMinutesFrom(0)).toBe(0);
	});

	it('clamps to what the server will accept at both ends', () => {
		expect(idleMinutesFrom(-5)).toBe(0);
		expect(idleMinutesFrom(MAX_IDLE_MINUTES + 1000)).toBe(MAX_IDLE_MINUTES);
	});

	it('takes whole minutes', () => {
		expect(idleMinutesFrom(12.9)).toBe(12);
	});
});
