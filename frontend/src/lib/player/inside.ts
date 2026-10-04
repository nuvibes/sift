/*
 * What happens INSIDE a sitting, counted where it happens and carried on the sitting's own report.
 *
 * A sitting keeps more than its time: where the playhead began, each move of it by hand, how long
 * was played at each speed, how long the screen was filled, how many times the file played through
 * and whether a picture was magnified (the player's schema on the server, "Inside a sitting"). None
 * of it is a request of its own: each piece of a sitting's report carries what happened during
 * it, and the server adds the pieces up as it adds the time.
 *
 * So each count here works the way the time and the replay map already do in `Player.svelte`: what
 * is SENT is taken off once the request has landed, never cleared before, so a piece that fails
 * goes again with the next one and anything counted while it was in the air is kept.
 */

import type { components } from '$lib/api/schema';

/** The most seeks one piece carries; the server keeps the first sixty-four of a sitting
 *  (`plays.MOST_SEEKS`) and counts the rest. */
export const MOST_SEEKS = 64;

type ViewReport = components['schemas']['ViewReport'];

/** One move of the playhead by hand, as the report carries it. The generated type. */
export type Seek = components['schemas']['SeekReport'];

/** What one piece of a sitting says about its inside: the report's own fields. */
export type InsidePiece = Pick<
	ViewReport,
	'start_ms' | 'seek_log' | 'speeds' | 'fullscreen_ms' | 'completions' | 'magnified'
>;

/**
 * How long the screen has been filled, counted from the browser's own word for it.
 *
 * `document.fullscreenElement` rather than a frame's flag, because both the player and a picture's
 * sitting need it and only the document knows for both. `filled` and `now` are handed in so a test
 * can stand in for the browser.
 */
export class FilledClock {
	#since: number | null = null;
	#banked = 0;
	#listening = false;
	readonly #filled: () => boolean;
	readonly #now: () => number;
	readonly #changed = () => this.#settle();

	constructor(
		// Truthy rather than "not null": a document with no fullscreen support has no such
		// property at all, and undefined is not a filled screen.
		filled: () => boolean = () =>
			typeof document !== 'undefined' && Boolean(document.fullscreenElement),
		now: () => number = () => performance.now()
	) {
		this.#filled = filled;
		this.#now = now;
	}

	/** Begin counting a new sitting from nothing, and listen for the screen being filled or left. */
	start(): void {
		this.#banked = 0;
		this.#since = this.#filled() ? this.#now() : null;
		if (!this.#listening && typeof document !== 'undefined') {
			document.addEventListener('fullscreenchange', this.#changed);
			this.#listening = true;
		}
	}

	/** Stop listening. What was counted stays to be taken. */
	stop(): void {
		this.#settle();
		this.#since = null;
		if (this.#listening) document.removeEventListener('fullscreenchange', this.#changed);
		this.#listening = false;
	}

	/** Everything counted so far and not yet taken back, in whole milliseconds. */
	read(): number {
		const running = this.#since === null ? 0 : this.#now() - this.#since;
		return Math.max(0, Math.round(this.#banked + running));
	}

	/** Add time another screen counted for the same sitting, when one is handed over. */
	carry(ms: number): void {
		this.#banked += Math.max(0, ms);
	}

	/** Take back what a piece that landed carried. */
	spend(sent: number): void {
		this.#settle();
		this.#banked = Math.max(0, this.#banked - sent);
	}

	/** Fold a stretch in, or start one, as the screen now says. Read by the listener and by tests. */
	settleNow(): void {
		this.#settle();
	}

	#settle(): void {
		const filled = this.#filled();
		const now = this.#now();
		if (this.#since !== null) {
			this.#banked += now - this.#since;
			this.#since = filled ? now : null;
		} else if (filled) {
			this.#since = now;
		}
	}
}

/**
 * The seeks of a sitting not yet reported, oldest first, at most `MOST_SEEKS` waiting.
 *
 * Past the cap a seek is dropped here, and the sitting's `seeks` count (which the player keeps
 * beside this) still says it happened: the server's rule, kept on this side so a page left
 * scrubbing all evening does not grow a list without end.
 */
export class SeekLog {
	#waiting: Seek[] = [];

	note(fromSeconds: number, toSeconds: number): void {
		if (this.#waiting.length >= MOST_SEEKS) return;
		this.#waiting.push({
			from_ms: Math.max(0, Math.round(fromSeconds * 1000)),
			to_ms: Math.max(0, Math.round(toSeconds * 1000))
		});
	}

	read(): Seek[] {
		return [...this.#waiting];
	}

	/** Take back the first `count`, which is what a piece that landed carried. */
	spend(count: number): void {
		this.#waiting.splice(0, count);
	}

	clear(): void {
		this.#waiting = [];
	}
}

/** How long was played at each speed, keyed as the report keys it ("1", "1.5"). */
export class SpeedTimes {
	#spent = new Map<string, number>();

	add(rate: number, ms: number): void {
		if (!(ms > 0) || !(rate > 0)) return;
		const key = String(Number(rate.toFixed(4)));
		this.#spent.set(key, (this.#spent.get(key) ?? 0) + ms);
	}

	/** As whole milliseconds, with a speed holding less than one left out. */
	read(): Record<string, number> {
		const out: Record<string, number> = {};
		for (const [key, ms] of this.#spent) if (Math.round(ms) > 0) out[key] = Math.round(ms);
		return out;
	}

	spend(sent: Record<string, number>): void {
		for (const [key, ms] of Object.entries(sent)) {
			const left = (this.#spent.get(key) ?? 0) - ms;
			if (left > 0) this.#spent.set(key, left);
			else this.#spent.delete(key);
		}
	}

	clear(): void {
		this.#spent.clear();
	}
}
