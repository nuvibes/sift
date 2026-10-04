/*
 * The pace of a held scroll arrow: how long each nudge waits before the next.
 *
 * A list too long for its box grows an arrow at each end (the library's own scroll buttons on a
 * Select and a Combobox, and the app's own on a menu flyout). Held, they scroll a row at a time,
 * and the wait between rows is what this decides: long at first, so a single press moves one row
 * and no more, then shorter and shorter, so a press that is held gets somewhere. Eased with
 * `cubicOut` rather than stepped, so the speeding-up reads as one motion rather than two speeds.
 *
 * One function for every arrow in the app. The library takes it as a `delay` prop; the menu's own
 * arrows call it the same way, so the two cannot drift apart.
 */
import { cubicOut } from 'svelte/easing';

/** The wait before the first nudge and after every early one, in milliseconds. */
export const NUDGE_FIRST_MS = 160;
/** The wait once the hold has been going a while: the fastest it ever scrolls. */
export const NUDGE_LAST_MS = 20;
/** How many nudges it takes to reach the fastest pace. */
export const NUDGE_TICKS = 24;

/** The wait before nudge number `tick` (0 is the first), in milliseconds. */
export function nudgeDelay(tick: number): number {
	const at = Math.min(Math.max(tick, 0), NUDGE_TICKS) / NUDGE_TICKS;
	return Math.round(NUDGE_FIRST_MS - (NUDGE_FIRST_MS - NUDGE_LAST_MS) * cubicOut(at));
}
