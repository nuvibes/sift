/*
 * The counts of what happens inside a sitting: each is taken off by exactly what a landed piece
 * carried, so a piece that fails goes again and a count made while it was in the air is kept.
 */

import { describe, expect, it } from 'vitest';

import { FilledClock, MOST_SEEKS, SeekLog, SpeedTimes } from './inside';

describe('the time the screen was filled', () => {
	it('counts only while the screen is filled, and takes off what was sent', () => {
		let now = 1_000;
		let full = false;
		const clock = new FilledClock(
			() => full,
			() => now
		);
		clock.start();
		now += 5_000;
		full = true;
		clock.settleNow();
		now += 3_000;
		expect(clock.read()).toBe(3_000);

		clock.spend(2_000);
		now += 1_000;
		full = false;
		clock.settleNow();
		now += 9_000;
		expect(clock.read()).toBe(2_000);
	});

	it('starts already counting when the sitting begins on a filled screen', () => {
		let now = 0;
		const clock = new FilledClock(
			() => true,
			() => now
		);
		clock.start();
		now += 400;
		clock.stop();
		now += 400;
		expect(clock.read()).toBe(400);
	});
});

describe('the seeks', () => {
	it('keeps each as a pair of positions, oldest first, and stops at the cap', () => {
		const log = new SeekLog();
		for (let n = 0; n < MOST_SEEKS + 5; n++) log.note(n, n + 0.5);

		const sent = log.read();
		expect(sent).toHaveLength(MOST_SEEKS);
		expect(sent[1]).toEqual({ from_ms: 1_000, to_ms: 1_500 });

		log.spend(2);
		log.note(99, 1);
		expect(log.read()[0]).toEqual({ from_ms: 2_000, to_ms: 2_500 });
	});
});

describe('the time at each speed', () => {
	it('keys the speed as the report does and takes off exactly what was sent', () => {
		const times = new SpeedTimes();
		times.add(1, 1_000);
		times.add(1.5, 400.4);
		times.add(0, 9_000);

		const sent = times.read();
		expect(sent).toEqual({ '1': 1_000, '1.5': 400 });

		times.add(1, 250);
		times.spend(sent);
		expect(times.read()).toEqual({ '1': 250 });
	});
});
