/* Which key presses have already been acted on.
 *
 * There is more than one player listening on the window at the same time: the small panel in the
 * corner claims the key that brings a clip back to full size, and bringing it back MOUNTS a
 * full-size player, which would then receive the very press that had just opened it and send the
 * clip straight back to the corner: a key that looks like it does nothing at all.
 *
 * A set rather than cancelling the event. Cancelling says "the browser should not do its default
 * thing with this", which several controls legitimately say about the same press for unrelated
 * reasons: reading it as "somebody has handled this" would make a player ignore Escape because the
 * frame around it had already asked the browser to leave that key alone.
 *
 * Weak, so nothing is held: an event lives for one dispatch and is then unreachable.
 */

const claimed = new WeakSet<KeyboardEvent>();

/** Say that this press has been acted on, so nothing else acts on it too. */
export function claim(event: KeyboardEvent): void {
	claimed.add(event);
}

/** Whether something has already acted on this press. */
export function taken(event: KeyboardEvent): boolean {
	return claimed.has(event);
}
