/*
 * Sending the wall to the corner panel, and bringing it back.
 *
 * Its own module because two things ask for it and neither can reach the other: the control on the
 * bar, and the keyboard shortcut declared by the screen. In the toolbar component the key would
 * need a second copy of a four-line function, and the four lines are all edge cases, so a copy of
 * them would be a copy that drifts.
 */

import { handPlace } from '$lib/components/player/motion';
import { stage } from '$lib/components/shell/stage.svelte';
import { mini } from '$lib/player/mini.svelte';
import { wallAspect } from './fit';
import { showing } from './wall.svelte';

/**
 * Put the wall in the corner, or take it out, and leave full screen on the way in.
 *
 * The exit comes FIRST, and it is the whole reason this is not one line. The panel is drawn by the
 * shell, and a browser filling the screen composites the fullscreen element and its subtree and
 * nothing else, so a panel opened while the wall is filling the screen is drawn behind the thing
 * filling the screen. It is running, it has the sound, and there is nothing on screen to say where
 * it went: the wall simply stops, and leaving full screen by hand is the only way to find out it
 * worked.
 *
 * Leaving first puts the panel down during the transition the browser is already in the middle of.
 */
export function toCorner(): void {
	if (mini.wall) {
		mini.close();
		return;
	}
	/* The panel grows out of the box the wall stands in, as the Player's picture does when it
	   docks: measured before full screen is left, because that box is where the picture is. */
	handPlace(document.querySelector('[data-theater-wall]')?.getBoundingClientRect() ?? null);
	if (stage.filling) stage.toggle();
	/* WITH ITS SHAPE, so the panel fits the wall the way it fits a clip. A wall has one answer
	   even though it is several clips each a different shape: the rows share the height and each
	   column is as wide as the widest thing in it. */
	const wall = showing.wall;
	const shape =
		wall === null
			? null
			: wallAspect(
					wall.shape,
					wall.cells.map((one) => one.shape)
				);
	mini.openWall({ width: window.innerWidth, height: window.innerHeight }, shape);
}
