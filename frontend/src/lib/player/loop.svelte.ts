/* The A-B loop: two points on a timeline, and the stretch between them played over and over.
 *
 * Held here rather than on the player, because there is more than one player. The small panel in
 * the corner and the full-size view are two instances of the same component, and handing a clip
 * from one to the other builds a new one, so a loop kept on the component would end the moment
 * the clip was sent to the corner, which reads as the loop control having quietly failed.
 *
 * Written down nowhere, deliberately. A loop somebody set once and forgot would be
 * indistinguishable from a fault the next time they opened the file: a video that will not play
 * past a point, with nothing on screen explaining why.
 *
 * It belongs to one clip. Opening something else clears it, for the same reason: carrying two
 * marks from one file onto the next is a loop nobody set.
 *
 * It lasts as long as SOMEBODY is showing the clip, and ends when the last of them goes, so a
 * handover from the full-size view to the corner panel keeps it (the same clip, still on screen,
 * marks and all) and closing everything does not.
 *
 * ## One shared loop, and why something else may need its own
 *
 * `abLoop` below is one object, shared, and that is right for the players: the corner panel and
 * the full-size view are two ways of showing the SAME thing, so marks made on one belong on the
 * other.
 *
 * It is wrong the moment two views can show the same clip and not be the same view. The `owns`
 * check tells clips apart, not views, so with several frames on screen at the same time, marking a
 * loop in one of them enforces it in every other frame that happens to hold that file, which is a
 * video jumping backwards for no reason anybody can see. Anything like that builds its own `Loop`
 * rather than reading this one. Same behaviour, one instance per frame.
 */

export class Loop {
	/** Which clip the two points belong to. */
	owner = $state<string | null>(null);

	/** The start, in seconds, or null when nothing has been marked. */
	a = $state<number | null>(null);

	/** The end. Null while only the start has been marked, which is a loop half set. */
	b = $state<number | null>(null);

	/**
	 * Whether this clip is the one the loop was set on.
	 *
	 * Every player asks before acting on the marks, and that is what keeps two of them apart. The
	 * panel in the corner and the full-size view are separate instances reading one object, so
	 * without this a loop marked on what is on screen would also be enforced on whatever the corner
	 * happened to be playing: a second video jumping backwards for no reason anybody could see.
	 */
	owns(id: string): boolean {
		return this.owner === id;
	}

	/** Whether there is a stretch to play: both ends marked, and the second after the first. */
	get running(): boolean {
		return this.a !== null && this.b !== null && this.b > this.a;
	}

	/** What the one button does next, and what it should say it will do. */
	get nextAction(): string {
		if (this.a === null) return 'Set the loop start';
		return this.b === null ? 'Set the loop end' : 'Clear the loop';
	}

	/**
	 * Mark the next point on a clip, or clear the pair once both are set.
	 *
	 * Marking is what claims the loop. A clip that was not holding it takes it, and the marks made
	 * on whatever held it before are dropped: there is one loop, and it is on the thing somebody
	 * last pointed at.
	 */
	mark(id: string, at: number): void {
		if (this.owner !== id) {
			this.owner = id;
			this.a = at;
			this.b = null;
			return;
		}
		if (this.a === null) {
			this.a = at;
			return;
		}
		if (this.b === null) {
			// A second press before the first point is somebody correcting themselves, not asking for
			// a loop that runs backwards.
			if (at <= this.a) this.a = at;
			else this.b = at;
			return;
		}
		this.a = null;
		this.b = null;
	}

	/**
	 * Set both ends together, from a stretch somebody saved earlier.
	 *
	 * The one way marks arrive from outside a press of the button, and it is what makes opening a
	 * saved loop play the LOOP rather than the video it was cut from, not a mark that opens its
	 * file at the right second and then runs on to the end, a bookmark wearing a duration.
	 *
	 * It does NOT make marks persistent. They are still written down nowhere and still end when the
	 * last player showing the clip goes. See the note at the top of this file, which is about a
	 * loop somebody set once and FORGOT. A saved loop is the opposite: a row opened on purpose,
	 * from a screen that lists them, by somebody who means to watch that stretch.
	 *
	 * Refused rather than clamped when the end is not after the start. A pair that cannot contain
	 * its own playhead never repeats, so arming one would be a video seeking backwards forever.
	 */
	arm(id: string, from: number, to: number): void {
		if (!(to > from)) return;
		this.owner = id;
		this.a = from;
		this.b = to;
	}

	/** Move one end, without letting it cross the other. A loop whose end is before its start is
	 *  not a shorter loop; it is one that can never be inside itself, and so never repeats. */
	moveTo(which: 'a' | 'b', at: number): void {
		if (which === 'a') this.a = this.b === null ? at : Math.min(at, this.b);
		else this.b = this.a === null ? at : Math.max(at, this.a);
	}

	/**
	 * A player is showing this clip.
	 *
	 * Counted rather than flagged, because a clip can be on screen in two places together and the
	 * marks belong to the clip rather than to either player. Handing a video from the full-size view
	 * to the panel in the corner builds a second player and destroys the first, and the two overlap:
	 * without a count, whichever one happens to leave second decides whether the loop lives.
	 */
	watch(id: string): void {
		this.#watchers.set(id, (this.#watchers.get(id) ?? 0) + 1);
	}

	/**
	 * A player has stopped showing this clip: it moved to another one, or it went away.
	 *
	 * The marks are dropped only when the LAST player on the clip has gone. Marks nobody can see are
	 * a video that will not play past a point with nothing on screen explaining why, and that is the
	 * whole reason a loop is not written down anywhere.
	 *
	 * The check is deferred by a turn, and that is the load-bearing part. A handover destroys one
	 * player and builds another, and the order is not guaranteed: if the old one goes first the count
	 * touches zero for an instant, and clearing on the spot would end a loop that is about to be
	 * picked straight back up. A turn later the arriving player has counted itself in and the check
	 * finds it.
	 */
	unwatch(id: string): void {
		const left = (this.#watchers.get(id) ?? 0) - 1;
		if (left > 0) this.#watchers.set(id, left);
		else this.#watchers.delete(id);
		if (this.owner === null) return;
		if (this.#pending !== null) clearTimeout(this.#pending);
		this.#pending = setTimeout(() => {
			this.#pending = null;
			if (this.owner !== null && !this.#watchers.has(this.owner)) this.clear();
		}, 0);
	}

	clear(): void {
		if (this.#pending !== null) {
			clearTimeout(this.#pending);
			this.#pending = null;
		}
		this.owner = null;
		this.a = null;
		this.b = null;
	}

	/** For tests: forget every watcher as well as the marks. */
	reset(): void {
		this.clear();
		this.#watchers.clear();
	}

	/** How many players are showing a clip. Not state: nothing on screen is drawn from it. */
	#watchers = new Map<string, number>();

	/** The one outstanding "has everybody gone?" check, so repeated departures do not stack up. */
	#pending: ReturnType<typeof setTimeout> | null = null;
}

export const abLoop = new Loop();
