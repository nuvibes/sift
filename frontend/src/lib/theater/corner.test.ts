/* Sending the wall to the corner, and the two edge cases that make it four lines rather than one.
 *
 * Both stores it touches are real: what is stood in for is the window and the wall's own cells,
 * which is what a jsdom run has none of. The question here is the ORDER: full screen is left before
 * the panel opens, and a wall that is already in the panel closes instead of opening a second time.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '$lib/api/client';
import { takePlace } from '$lib/components/player/motion';
import { stage } from '$lib/components/shell/stage.svelte';
import { mini } from '$lib/player/mini.svelte';
import { toCorner } from './corner';
import { Wall, showing } from './wall.svelte';

beforeEach(() => {
	showing.wall = null;
	stage.filling = false;
	mini.close();
	vi.restoreAllMocks();
	// The wall's cells ask for something to play; nothing here is about what they play.
	vi.spyOn(api, 'get').mockImplementation(async () => ({ items: [], total: 0 }));
});

describe('sending the wall to the corner', () => {
	it('leaves full screen BEFORE the panel opens, not after', () => {
		/* The whole reason this is not one line. A browser filling the screen composites the
		   fullscreen element and its subtree and nothing else, so a panel opened while the wall is
		   filling the screen is drawn behind the thing filling the screen. It is running, it has the
		   sound, and nothing on screen says where it went. */
		const order: string[] = [];
		stage.filling = true;
		vi.spyOn(stage, 'toggle').mockImplementation(() => {
			order.push('left full screen');
			stage.filling = false;
		});
		vi.spyOn(mini, 'openWall').mockImplementation(() => void order.push('opened the panel'));

		toCorner();

		expect(order).toEqual(['left full screen', 'opened the panel']);
	});

	it('does not leave full screen when it was not filling it', () => {
		const left = vi.spyOn(stage, 'toggle');
		vi.spyOn(mini, 'openWall').mockImplementation(() => {});

		toCorner();

		expect(left).not.toHaveBeenCalled();
	});

	it('brings the wall back rather than opening a second panel', () => {
		const opened = vi.spyOn(mini, 'openWall');
		const closed = vi.spyOn(mini, 'close');
		mini.openWall({ width: 1400, height: 900 }, null);
		opened.mockClear();

		toCorner();

		expect(closed).toHaveBeenCalled();
		expect(opened, 'the control is one control, and it reads as a toggle').not.toHaveBeenCalled();
	});

	it('hands the panel the shape of the whole wall, so it fits it the way it fits a clip', () => {
		/* A wall has one shape: the rows share the height and each column is as wide as the
		   widest thing in it, so it passes that shape, not none. */
		const wall = new Wall();
		wall.setLayout('side_by_side');
		showing.wall = wall;
		let handed: number | null | undefined;
		vi.spyOn(mini, 'openWall').mockImplementation((_within, shape) => void (handed = shape));

		toCorner();

		// Nothing is playing, so every cell answers null and the wall has nothing to be the shape of.
		expect(handed).toBeNull();
	});

	it('hands the panel the box the wall stands in, so it grows from there', () => {
		const wall = document.createElement('div');
		wall.setAttribute('data-theater-wall', '');
		const box = new DOMRect(40, 60, 900, 500);
		wall.getBoundingClientRect = () => box;
		document.body.append(wall);
		vi.spyOn(mini, 'openWall').mockImplementation(() => {});

		toCorner();
		wall.remove();

		expect(takePlace()).toBe(box);
	});

	it('asks for no shape at all when there is no wall to take', () => {
		let handed: number | null | undefined = 0;
		vi.spyOn(mini, 'openWall').mockImplementation((_within, shape) => void (handed = shape));

		toCorner();

		expect(handed).toBeNull();
	});
});
