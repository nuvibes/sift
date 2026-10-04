/*
 * Whether anything is playing, anywhere in the window.
 *
 * WATCHING A VIDEO IS USING SIFT. The idle timers that shut Hidden and lock Sift are reset by key
 * presses, scrolling and pointer movement: every sign of a person except the one this application
 * is FOR. Somebody watching a forty-minute film in the corner touches nothing, so by those measures
 * alone they have left: the timer fires, the shell goes, and the film goes with it. This is the
 * sign they have not.
 *
 * A COUNT, not a flag. Two players can be mounted at once (the panel in the corner and a
 * full-size view behind it), and the moment one is handed to the other they are both alive for an
 * instant. A flag set by the first to start and cleared by the first to stop reads as "nothing is
 * playing" right in the middle of the handover, which is the one moment a timer must not believe.
 *
 * It is deliberately NOT a record of what is playing. What is on screen belongs to the player and
 * to the mini panel's own store; the only question here is whether the person is still there.
 */

class Watching {
	/** How many players are running right now. */
	#running = $state(0);

	/** Whether anything at all is playing. */
	get anything(): boolean {
		return this.#running > 0;
	}

	/** A player started. Balanced by `stopped`, including when its component goes away. */
	started(): void {
		this.#running += 1;
	}

	/* Never below zero. A player that is torn down while paused calls this from its cleanup without
	   ever having called `started`, and a count that went negative would then take a real play with
	   it and leave the timers believing nothing is on. */
	stopped(): void {
		if (this.#running > 0) this.#running -= 1;
	}
}

export const watching = new Watching();
