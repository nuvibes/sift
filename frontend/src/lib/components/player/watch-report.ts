/**
 * What the player tells the server about a sitting with one file: time watched, the replay curve,
 * moves by hand, speeds, time filling the screen, and plays through. Reported in pieces, each its
 * own share, so the server adds them up; facts only, the conclusions are the server's
 * (`record_view`). A piece that fails loses nothing: what was sent comes off only once it lands.
 */

import type { components } from '$lib/api/schema';
import { newSittingId } from '$lib/player/sitting.svelte';
import { FilledClock, SeekLog, SpeedTimes } from '$lib/player/inside';

/** The most the server will accept for one sitting, from `ViewReport.watch_ms`. */
const MAX_WATCH_MS = 24 * 60 * 60 * 1000;

/** The longest a leaving file's last piece waits for the next file's first frame. */
export const LEAVING_WAIT_MS = 1000;

/** One piece of a sitting, less where it happened, which the player adds. */
export type WatchPiece = Pick<
	components['schemas']['ViewReport'],
	| 'watch_ms'
	| 'already_reported_ms'
	| 'position_ms'
	| 'ended'
	| 'heat'
	| 'sitting'
	| 'seeks'
	| 'start_ms'
	| 'seek_log'
	| 'speeds'
	| 'fullscreen_ms'
	| 'completions'
>;

/** What the report reads off the player, and how it posts a piece. */
export interface Watched {
	/** Where the playhead is, in seconds, as the player last read it. */
	position(): number;
	/** How long the file is, or nought before the element has said. */
	duration(): number;
	/** The rate the element plays at. */
	rate(): number;
	/** Where the element's playhead stands now, or null without an element. */
	playhead(): number | null;
	/** The plan's threshold for a view, in milliseconds; nought where it gave none. */
	viewAt(): number;
	/** Post one piece of the sitting, once `after` settles where one is given. */
	post(piece: WatchPiece, final: boolean, after?: Promise<void>): Promise<unknown>;
}

export class WatchReport {
	/** The sitting's name, repeated on every piece so the server writes one row per sitting. */
	sitting = newSittingId();
	private seeks = 0;
	private startMs: number | null = null;
	private seekLog = new SeekLog();
	private speedTimes = new SpeedTimes();
	private filled = new FilledClock();
	private passes = { count: 0 };
	/* Time that really passed with the video playing, counted from the clock rather than read off
	   the playhead, which somebody who dragged the scrubber to the end has not watched. */
	private watchedMs = 0;
	private lastTick = 0;
	private reported = false;
	/** How much of this sitting the server has been told about; moves once a request lands. */
	private sentMs: number | null = null;
	/** Whether the piece that earns the view has been issued, flagged the moment it is. */
	private announced = false;
	/* Whether the file played to its own end: a clip on repeat is back at zero a frame later, so
	   only the element sees the moment. */
	private reachedTheEnd = false;
	/* How long each slice of the file has been on screen, sparse, sampled from the playhead on the
	   tick that already runs, so a moment watched ten times counts ten times. */
	private heat: Record<number, number> = {};
	/** Where the playhead was when the current stretch of time began. */
	private tickAt: number | null = null;
	private watched: Watched;
	/** How many slices a file is cut into; the server's `HEAT_BUCKETS`. */
	private buckets: number;

	constructor(watched: Watched, buckets: number) {
		this.watched = watched;
		this.buckets = buckets;
	}

	/** The screen-filled clock runs from when the player opens. */
	start(): void {
		this.filled.start();
	}

	stop(): void {
		this.filled.stop();
	}

	/*
	 * A new file is a new sitting, and everything counted about the last one goes with it. A NEW
	 * counter of each kind rather than the old ones emptied: a piece of the last sitting can land
	 * after the next has begun, and what it takes off must come off the sitting it was counted for.
	 */
	restart(): void {
		this.sitting = newSittingId();
		this.seeks = 0;
		this.startMs = null;
		this.seekLog = new SeekLog();
		this.speedTimes = new SpeedTimes();
		this.filled.stop();
		this.filled = new FilledClock();
		this.filled.start();
		this.passes = { count: 0 };
		this.reported = false;
		/* Left standing, these would make the next file's first report continue the last sitting. */
		this.sentMs = null;
		this.announced = false;
		this.watchedMs = 0;
		this.lastTick = 0;
		this.tickAt = null;
		this.heat = {};
		this.reachedTheEnd = false;
	}

	/** Start counting a stretch of watching, from now, at wherever the playhead is. */
	begin(): void {
		this.lastTick = performance.now();
		this.tickAt = this.watched.position();
		/* Where this sitting began: the playhead when it first played, already moved there. */
		const at = this.watched.playhead();
		if (this.startMs === null && at !== null) this.startMs = Math.round(at * 1000);
	}

	/** Fold the stretch since the last tick into the totals. */
	accumulate(): void {
		if (!this.lastTick) return;
		const spent = performance.now() - this.lastTick;
		this.watchedMs += spent;
		this.speedTimes.add(this.watched.rate(), spent);
		this.spreadHeat(spent, this.tickAt, this.watched.position());
		this.lastTick = 0;
		this.tickAt = null;
	}

	/** A tick while playing: fold the time in, start the next stretch, and say if a view is earned. */
	tick(): void {
		this.accumulate();
		this.begin();
		this.countIfEarned();
	}

	/** The file reached its end: counted every time, and never taken back. */
	ended(): void {
		this.reachedTheEnd = true;
		this.passes.count += 1;
	}

	/* Lets a held last piece go (`report`). */
	private letGo: (() => void) | null = null;

	/**
	 * Tell the server what is left, once, on the way out. `held`, the piece is counted now and sent
	 * at `release` (the next file's first frame) or after `LEAVING_WAIT_MS`, so its writes never sit
	 * in the gap between two clips.
	 */
	async report(held = false): Promise<void> {
		if (this.reported) return;
		this.reported = true;
		// The final stretch since the last tick, or it is dropped from every view.
		this.accumulate();
		await this.send(true, held ? this.hold() : undefined);
	}

	/** Send a held last piece now. */
	release(): void {
		this.letGo?.();
	}

	private hold(): Promise<void> {
		this.release();
		return new Promise((resolve) => {
			const go = () => {
				clearTimeout(timer);
				if (this.letGo === go) this.letGo = null;
				resolve();
			};
			const timer = setTimeout(go, LEAVING_WAIT_MS);
			this.letGo = go;
		});
	}

	/* Close the pass that just finished: a view is a playthrough, so a clip on repeat forty-six
	   times is forty-six. Nothing resets unless the request landed. */
	async closePass(): Promise<void> {
		const sitting = this.sitting;
		const recorded = await this.send(false);
		// A reply landing after the next file began belongs to the sitting it was counted for.
		if (recorded === null || this.sitting !== sitting) return;
		// Exactly what went, so time measured while the request was in the air survives.
		this.watchedMs = Math.max(0, this.watchedMs - recorded);
		this.sentMs = null;
		this.announced = false;
	}

	/* Say so once the sitting is watched enough to count, by the server's threshold for this file;
	   flagged before the request, or every tick in flight would send a first piece. */
	private countIfEarned(): void {
		const at = this.watched.viewAt();
		if (this.announced || this.reported || at <= 0 || this.watchedMs < at) return;
		this.announced = true;
		void this.send(false);
	}

	/** Send everything of this sitting not sent yet; answers the total sent, or null. */
	private async send(final: boolean, after?: Promise<void>): Promise<number | null> {
		// Clamped to the server's ceiling: repeat is unbounded, and a 422 would discard the whole view.
		const total = Math.min(Math.round(this.watchedMs), MAX_WATCH_MS);
		const piece = Math.max(0, total - (this.sentMs ?? 0));
		// Every time, zero included: "back at the beginning" is what clears a finished sitting.
		const stoppedAt = Math.min(
			Math.max(0, Math.round(this.watched.position() * 1000)),
			MAX_WATCH_MS
		);
		const map = this.heatToSend();
		const jumps = this.seeks;
		// This sitting's own counters, so what lands comes off them even after the next file began.
		const log = this.seekLog;
		const moves = log.read();
		const times = this.speedTimes;
		const speeds = times.read();
		const clock = this.filled;
		const fullscreen = clock.read();
		const counted = this.passes;
		const completions = counted.count;
		const sitting = this.sitting;
		try {
			await this.watched.post(
				{
					watch_ms: piece,
					already_reported_ms: this.sentMs,
					position_ms: stoppedAt,
					ended: this.reachedTheEnd,
					heat: map,
					sitting: this.sitting,
					seeks: jumps,
					start_ms: this.startMs,
					seek_log: moves,
					speeds,
					fullscreen_ms: fullscreen,
					completions
				},
				// Only the last has to survive the page going: keepalive is a small shared budget.
				final,
				after
			);
			log.spend(moves.length);
			times.spend(speeds);
			clock.spend(fullscreen);
			counted.count = Math.max(0, counted.count - completions);
			/* The fields `restart` empties rather than replaces are the next sitting's once it has
			   begun, so a reply landing late leaves them alone. */
			if (this.sitting !== sitting) return total;
			this.sentMs = total;
			/* Subtracted rather than cleared: a tick can add while the request is in the air. */
			this.seeks = Math.max(0, this.seeks - jumps);
			for (const [bucket, spent] of Object.entries(map)) {
				const key = Number(bucket);
				const left = (this.heat[key] ?? 0) - spent;
				if (left > 0) this.heat[key] = left;
				else delete this.heat[key];
			}
			return total;
		} catch {
			// Nothing is lost: `sentMs` did not move, so this piece goes with the next one.
			return null;
		}
	}

	/*
	 * Charge one stretch of watching to the slices the playhead crossed, weighted by how much fell in
	 * each, since the tick and the slices run at different rates. A jump is not watching: playback
	 * moves the playhead by about the time that passed, and anything else is charged to where it
	 * started, and counted as a seek.
	 */
	private spreadHeat(ms: number, fromSeconds: number | null, toSeconds: number): void {
		const duration = this.watched.duration();
		if (fromSeconds === null || ms <= 0 || !(duration > 0)) return;
		const add = (bucket: number | null, amount: number) => {
			if (bucket !== null && amount > 0) this.heat[bucket] = (this.heat[bucket] ?? 0) + amount;
		};

		const moved = (toSeconds - fromSeconds) * 1000;
		// Generous: the playhead and `performance.now()` are two clocks, a frame apart at times.
		const played = moved > 0 && Math.abs(moved - ms) <= Math.max(500, ms);
		/* A jump is the distance, not `!played`: a paused tick moves nothing over several seconds. */
		if (Math.abs(moved) > Math.max(500, ms)) {
			this.seeks += 1;
			this.seekLog.note(fromSeconds, toSeconds);
		}
		const first = this.bucketAt(fromSeconds);
		if (!played) {
			add(first, ms);
			return;
		}

		const last = this.bucketAt(toSeconds);
		if (first === null || last === null) return;
		if (first === last) {
			add(first, ms);
			return;
		}

		const slice = duration / this.buckets;
		const span = toSeconds - fromSeconds;
		for (let bucket = first; bucket <= last; bucket += 1) {
			// The part of this stretch inside this slice, clamped at both ends.
			const overlap =
				Math.min(toSeconds, (bucket + 1) * slice) - Math.max(fromSeconds, bucket * slice);
			add(bucket, ms * (overlap / span));
		}
	}

	/** The replay map as whole milliseconds by slice, zeroes dropped: nothing happened is no row. */
	private heatToSend(): Record<string, number> {
		const out: Record<string, number> = {};
		for (const [bucket, spent] of Object.entries(this.heat)) {
			const whole = Math.min(Math.round(spent), MAX_WATCH_MS);
			if (whole > 0) out[bucket] = whole;
		}
		return out;
	}

	/*
	 * Which slice a moment falls in, or null until the file's length is known: time watched before
	 * then is counted but cannot be placed, and slice zero would draw a spike on every file.
	 */
	private bucketAt(seconds: number): number | null {
		const duration = this.watched.duration();
		if (!(duration > 0) || !Number.isFinite(seconds) || seconds < 0) return null;
		// Clamped: the element's length and its playhead can disagree by a frame.
		return Math.min(this.buckets - 1, Math.floor((seconds / duration) * this.buckets));
	}
}
