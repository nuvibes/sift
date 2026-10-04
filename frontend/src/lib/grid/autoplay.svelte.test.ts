/*
 * What the preview control on the top bar says.
 *
 * The tooltip names what pressing would do, never the state the tiles are in, which would read
 * backwards on every press. And while everything visible is previewing it counts the tiles that
 * play, read off the walls as they scroll, never the ceiling ("Up to 48 play at a time" on a screen
 * of a dozen tiles reads as the count).
 */
import { describe, expect, test } from 'vitest';
import { flushSync } from 'svelte';
import { gridAutoplay, previewWords } from './grid.svelte';

describe('what pressing the preview control will do', () => {
	test('from previewing on hover, it offers everything visible', () => {
		expect(previewWords('hover', 0, 48)).toEqual({
			label: 'Preview everything visible',
			hint: 'Preview everything visible'
		});
	});

	test('from everything visible, it offers hover back, and says how many are moving now', () => {
		const words = previewWords('visible', 12, 48);

		expect(words.label).toBe('Preview on hover');
		expect(words.hint).toBe('Preview on hover. 12 previews are playing now.');
		expect(words.hint, 'the ceiling was said as if it were the count').not.toContain('48');
	});

	test('says the ceiling only once it is what holds the rest still', () => {
		expect(previewWords('visible', 48, 48).hint).toBe(
			'Preview on hover. 48 previews are playing now. Up to 48 play at a time, to keep the app responsive.'
		);
	});

	test('reads as a sentence at one and at none', () => {
		expect(previewWords('visible', 1, 48).hint).toBe('Preview on hover. 1 preview is playing now.');
		expect(previewWords('visible', 0, 48).hint).toBe(
			'Preview on hover. No previews are playing now.'
		);
	});
});

describe('how many previews are playing now', () => {
	test('is the sum of every wall on screen, and follows each as it changes', () => {
		let first = $state(3);
		const second = 4;
		const stopFirst = gridAutoplay.counts(() => first);
		const stopSecond = gridAutoplay.counts(() => second);
		try {
			expect(gridAutoplay.playing).toBe(7);

			// A scroll takes two tiles out of view on the first wall.
			first = 1;
			flushSync();
			expect(gridAutoplay.playing, 'the count did not follow the scroll').toBe(5);
		} finally {
			stopFirst();
			stopSecond();
		}
	});

	test('a wall that goes away takes its share with it', () => {
		const stop = gridAutoplay.counts(() => 6);
		stop();

		expect(gridAutoplay.playing).toBe(0);
	});
});
