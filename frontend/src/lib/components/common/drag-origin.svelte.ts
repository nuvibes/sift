/*
 * Whether the drag in flight began INSIDE this application, and the answer is not "what is it
 * carrying".
 *
 * A browser fills any drag of a picture or a link with `text/uri-list` of its own accord, the very
 * types an import arms on, while a drag from outside fires no `dragstart` in this document. That
 * asymmetry is the signal. Named once for the window overlay and the drop on an entity, and armed
 * by itself, since a listener not yet installed would read the app's own drags as from outside.
 */

/** Whether a drag that began in this document is still in flight. */
let inside = false;

/**
 * Latched only for a drag that ACTUALLY BEGINS, which is not the same as a `dragstart`.
 *
 * This runs in the bubble phase, after a picture in the desktop client may have prevented the
 * drag to hand it to Windows; a prevented drag never sends a `dragend`, so latching it would leave
 * the window deaf to every later drop into it.
 */
function began(event: DragEvent): void {
	if (event.defaultPrevented) return;
	inside = true;
}

/* `dragend` fires wherever the drag finished, outside the window too; `drop` because a drop
   handled in the page can be a drag's last event. */
function ended(): void {
	inside = false;
}

if (typeof window !== 'undefined') {
	window.addEventListener('dragstart', began);
	window.addEventListener('dragend', ended);
	window.addEventListener('drop', ended);
}

/** Whether what is being dragged right now started in this application. */
export function dragBeganInside(): boolean {
	return inside;
}

/** Forget any drag in flight. For tests, and for a surface that has taken the drop itself. */
export function forgetDragOrigin(): void {
	inside = false;
}
