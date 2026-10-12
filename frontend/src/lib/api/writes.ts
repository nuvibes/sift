/* The writes somebody pressed for in this tab, on their way, and when the last one was answered:
   a live bell that comes in step with one is that write's echo, not somebody else's change. A
   write a timer sends (a heartbeat) is nobody's press and is not counted. */

let writing = 0;
let answeredAt = Number.NEGATIVE_INFINITY;

/** Whether the page was just used: the browser's own transient activation, a few seconds long. */
function pressed(): boolean {
	return (navigator as { userActivation?: { isActive: boolean } }).userActivation?.isActive ?? true;
}

/** A write of this tab has gone out; the answer to hand back to `writeAnswered`. */
export function writeSent(): boolean {
	if (!pressed()) return false;
	writing += 1;
	return true;
}

/** ...and has been answered, or failed. */
export function writeAnswered(counted: boolean): void {
	if (!counted) return;
	writing = Math.max(0, writing - 1);
	answeredAt = Date.now();
}

/** Whether this tab has a write on its way, or had one answered within `withinMs`. */
export function wroteJustNow(withinMs: number): boolean {
	const since = Date.now() - answeredAt;
	// A clock stepped back is no echo.
	return writing > 0 || (since >= 0 && since < withinMs);
}
