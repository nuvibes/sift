/*
 * What happens INSIDE a sitting (seeks, speeds, the screen filled), carried on its report pieces.
 * Each count is taken off once a piece has LANDED, never before, as the time and the replay map
 * are.
 */

import type { components } from '$lib/api/schema';

/** The server keeps the first sixty-four (`plays.MOST_SEEKS`) and counts the rest. */
export const MOST_SEEKS = 64;

type ViewReport = components['schemas']['ViewReport'];

export type Seek = components['schemas']['SeekReport'];

export type InsidePiece = Pick<
	ViewReport,
	'start_ms' | 'seek_log' | 'speeds' | 'fullscreen_ms' | 'completions' | 'magnified'
>;

/** From `document.fullscreenElement`, which both the player and a picture's sitting need. */
export class FilledClock {
	#since: number | null = null;
	#banked = 0;
	#listening = false;
	readonly #filled: () => boolean;
	readonly #now: () => number;
	readonly #changed = () => this.#settle();

	constructor(
		// Truthy: a document with no fullscreen support has no such property.
		filled: () => boolean = () =>
			typeof document !== 'undefined' && Boolean(document.fullscreenElement),
		now: () => number = () => performance.now()
	) {
		this.#filled = filled;
		this.#now = now;
	}

	start(): void {
		this.#banked = 0;
		this.#since = this.#filled() ? this.#now() : null;
		if (!this.#listening && typeof document !== 'undefined') {
			document.addEventListener('fullscreenchange', this.#changed);
			this.#listening = true;
		}
	}

	stop(): void {
		this.#settle();
		this.#since = null;
		if (this.#listening) document.removeEventListener('fullscreenchange', this.#changed);
		this.#listening = false;
	}

	read(): number {
		const running = this.#since === null ? 0 : this.#now() - this.#since;
		return Math.max(0, Math.round(this.#banked + running));
	}

	carry(ms: number): void {
		this.#banked += Math.max(0, ms);
	}

	spend(sent: number): void {
		this.#settle();
		this.#banked = Math.max(0, this.#banked - sent);
	}

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

/** At most `MOST_SEEKS` waiting; past it a seek is dropped and the `seeks` count still says it. */
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

	spend(count: number): void {
		this.#waiting.splice(0, count);
	}

	clear(): void {
		this.#waiting = [];
	}
}

/** Keyed as the report keys it ("1", "1.5"). */
export class SpeedTimes {
	#spent = new Map<string, number>();

	add(rate: number, ms: number): void {
		if (!(ms > 0) || !(rate > 0)) return;
		const key = String(Number(rate.toFixed(4)));
		this.#spent.set(key, (this.#spent.get(key) ?? 0) + ms);
	}

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
