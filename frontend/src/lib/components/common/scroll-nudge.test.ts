import { describe, expect, it } from 'vitest';
import { NUDGE_FIRST_MS, NUDGE_LAST_MS, NUDGE_TICKS, nudgeDelay } from './scroll-nudge';

describe('the pace of a held scroll arrow', () => {
	it('waits longest before the first nudge and shortest once the hold has been going', () => {
		expect(nudgeDelay(0)).toBe(NUDGE_FIRST_MS);
		expect(nudgeDelay(NUDGE_TICKS)).toBe(NUDGE_LAST_MS);
		expect(nudgeDelay(NUDGE_TICKS * 4)).toBe(NUDGE_LAST_MS);
	});

	it('only ever gets faster, and eases rather than steps', () => {
		const waits = Array.from({ length: NUDGE_TICKS + 1 }, (_, tick) => nudgeDelay(tick));
		for (let at = 1; at < waits.length; at += 1)
			expect(waits[at]).toBeLessThanOrEqual(waits[at - 1]);
		// cubicOut: the first step is the biggest drop, the last the smallest.
		expect(waits[0] - waits[1]).toBeGreaterThan(waits[NUDGE_TICKS - 1] - waits[NUDGE_TICKS]);
	});

	it('never answers a negative wait for a nonsense tick', () => {
		expect(nudgeDelay(-3)).toBe(NUDGE_FIRST_MS);
	});
});
