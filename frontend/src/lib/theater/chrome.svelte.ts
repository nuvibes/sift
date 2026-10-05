/*
 * WHETHER THE WALL'S CHROME IS UP: one answer, read by the two rows that follow it.
 *
 * The wall works it out, because the wall is where the pointer is and where the rule lives: the top
 * and bottom edges of the screen, the bar itself and anything it has opened, and an idle clock. What
 * it decides has to reach two places that cannot see it (the shell's bar at the top of the window,
 * and the screen's own row under it), so it is written here rather than passed down two component
 * trees that do not meet. One boolean, so the rule stays with the pointer it is about.
 */
class WallChrome {
	/** True when the wall's controls are up. True by default, so a screen with no wall is normal. */
	up = $state(true);
}

export const wallChrome = new WallChrome();

/** Whether the screen's row fades with the wall's chrome: never while it holds the menus in a window. */
export function rowQuiet(up: boolean, filling: boolean, menusOnTopBar: boolean): boolean {
	return !up && (filling || menusOnTopBar);
}
