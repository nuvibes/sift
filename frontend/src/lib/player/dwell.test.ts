/* How long a run rests on something with no end of its own.
 *
 * The table this implements is short and every row of it is a different reason, which is exactly
 * the shape that gets one row wrong and nobody notices: a run that quietly stops on photographs,
 * or one that races past a GIF before it has been round once.
 */

import { describe, expect, it } from 'vitest';

import { ANIMATION_LOOPS, PICTURE_SECONDS, restFor } from './dwell.svelte';

const ON = { pictures: true };
const OFF = { pictures: false };

describe('what a run does with each kind of file', () => {
	it('leaves a video alone, because a video ends by itself', () => {
		expect(restFor('video', 30_000, ON)).toBeNull();
		expect(restFor('video', 30_000, OFF)).toBeNull();
	});

	it('plays a GIF through once and then moves on', () => {
		// Its own length, whatever the setting says: a GIF has an end, it just goes round again
		// rather than stopping at it.
		expect(ANIMATION_LOOPS).toBe(1);
		expect(restFor('gif', 4_000, OFF)).toBe(4_000);
		expect(restFor('gif', 4_000, ON)).toBe(4_000);
	});

	it('holds a photograph for two seconds while the account includes photos', () => {
		expect(PICTURE_SECONDS).toBe(2);
		expect(restFor('image', null, OFF)).toBeNull();
		expect(restFor('image', null, ON)).toBe(2_000);
	});

	it('treats a GIF of unknown length as a photograph', () => {
		/* A browser reports nothing about where a GIF is, so the length recorded at import is
		 * the only thing there is. Without one the alternatives are the picture rule or holding it
		 * forever. */
		expect(restFor('gif', null, OFF)).toBeNull();
		expect(restFor('gif', 0, ON)).toBe(PICTURE_SECONDS * 1000);
	});
});
