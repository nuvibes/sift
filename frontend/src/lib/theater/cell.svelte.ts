/* One cell of the wall: handed a query, not a file, it keeps finding itself something to play.
 * Repeating is decided here on `ended`, since only the cell knows what "again" means. */

import { api, ApiError } from '$lib/api/client';
import { Loop } from '$lib/player/loop.svelte';
import { loudness } from '$lib/player/loudness.svelte';
import { planFor, type PlaybackPlan, type Quality } from '$lib/player/playback';
import type { FileFacts } from '$lib/player/facts';
import { isLoopMode, type LoopMode } from '$lib/player/loop-modes';

const SIMILARITY = 'similarity';

export const PLAN_UNASKED = "Couldn't ask how to play this. Press it again to try.";
import { savedQuery } from '$lib/search/search.svelte';
import { newSittingId } from '$lib/player/sitting.svelte';
import { mintSeed, RANDOM } from '$lib/grid/sort-state.svelte';
import { continuable } from '$lib/grid/grid.svelte';
import { DEFAULT_ASPECT, isAspect, ratioOf, type AspectId } from './aspects';
import { anotherRound, showingOrder } from './orders';
import type { SpriteSheet } from '$lib/player/trickplay';
import { keptFile } from './kept';
import type { components } from '$lib/api/schema';

/* LIVE: followed by routes/theater/+page.svelte (the wall opened again on the settings bell) */

/** One file as a cell knows it: the grid's tile; the scrub strip is fetched on screen. */
export type Playable = Pick<
	components['schemas']['AssetSummary'],
	| 'id'
	| 'media_type'
	| 'duration_ms'
	| 'thumb'
	| 'art'
	| 'original_filename'
	| 'width'
	| 'height'
	/* What a verb reads to word itself; `concealed` because Save reads it. */
	| 'favorite'
	| 'rating'
	| 'concealed'
>;

/** A cell as written down, never what it was playing, in the wire's read-side spelling. */
export type SavedCell = components['schemas']['CellBody-Output'];

/** One cell's sitting with one file, with how much the server has been told so far. */
export interface CellSitting {
	readonly file: string;
	readonly id: string;
	reported: number | null;
}

export const MEDIA_KINDS = ['video_gif', 'all'] as const;
export type MediaKind = (typeof MEDIA_KINDS)[number];

export type Ordering = 'in_order' | 'shuffle';

const NOTHING_ELSEWHERE: ReadonlySet<string> = new Set();

/* The server keeps these as free text; a guard narrows them so the fallback type-checks. */
function isMediaKind(value: string): value is MediaKind {
	return (MEDIA_KINDS as readonly string[]).includes(value);
}

const PAGE = 60;

/** Refill once the run has this few left, so a cell never waits on a request to move on. */
const REFILL_AT = 8;

const HISTORY = 40;

export const PAGE_TIMEOUT_MS = 15_000;

/** Files asked about before a converted one is taken: the install has one converter. */
const DIRECT_TRIES = 4;

const GIVE_UP_AFTER = 3;

type CellState = 'empty' | 'loading' | 'ready' | 'waiting' | 'stopped' | 'nothing_here' | 'failed';

type Choice = { file: Playable; plan: PlaybackPlan };

let counter = 0;

export interface CellPicture {
	video: HTMLVideoElement | null;
	still: HTMLImageElement | HTMLCanvasElement | null;
	stage: HTMLElement | null;
}

export class Cell {
	/** Stable for the life of the wall, so a cell keeps its identity across a layout change. */
	readonly key = `cell-${++counter}`;

	source = $state('');
	mediaKind = $state<MediaKind>('video_gif');

	/** The drawer's word for the Random order, read off `sort` so the two never disagree. */
	get ordering(): Ordering {
		return this.sort === RANDOM ? 'shuffle' : 'in_order';
	}

	/** Files on screen or next in the wall's other cells, stepped over while others can play. */
	elsewhere: () => ReadonlySet<string> = () => NOTHING_ELSEWHERE;

	get nextUp(): string | null {
		return this.#ahead?.file.id ?? null;
	}
	/** The shape this cell is held to; on the cell, so it survives a rearranged wall. */
	aspect = $state<AspectId>(DEFAULT_ASPECT);
	endBehaviour = $state<LoopMode>('loop_all');
	timerSeconds = $state<number | null>(null);

	/** The application's one level (`$lib/player/loudness`); a cell owns only `muted`. */
	get volume(): number {
		return loudness.level;
	}

	set volume(level: number) {
		loudness.set(level);
	}

	/** Every cell starts muted: no browser begins several videos with sound unasked. */
	muted = $state(true);

	/* Kept here because both bars draw them; `seek` is installed by the view while mounted. */
	position = $state(0);

	/** Where the next file should start, so a promoted preview does not restart. */
	resumeAt = $state<number | null>(null);
	problem = $state<string | null>(null);
	duration = $state(0);
	/** Sift's, as the element is rebuilt per file. Not saved: a rate is a key held down. */
	rate = $state(1);
	seek: ((seconds: number) => void) | null = $state(null);
	/** Start this file again for `loop_one`: an ended element is paused, so a seek alone stalls. */
	replay: (() => void) | null = $state(null);
	shot: (() => CellPicture) | null = $state(null);
	/** Installed by the view, which can swap the stream and carry the playhead across. */
	changeQuality: ((quality: Quality) => void) | null = $state(null);
	quality = $state<Quality | null>(null);

	/* On the cell because the bar and the panel over the picture must read one answer. */
	factsOpen = $state(false);
	timing = $state(false);
	qualityOpen = $state(false);

	/** This one cell held, apart from the wall's hold: either saying stop is enough. */
	paused = $state(false);

	state = $state<CellState>('empty');
	playing = $state<Playable | null>(null);
	plan = $state<PlaybackPlan | null>(null);

	/** Whether this file's sound is being repaired: `pending`, `repaired`, `off`, or null. */
	repair = $state<string | null>(null);
	sheet = $state<SpriteSheet | null>(null);
	facts = $state<FileFacts | null>(null);

	/** One A-B loop per cell: two cells may hold the same file. */
	readonly loop = new Loop();

	#queue: Playable[] = [];
	#history: Playable[] = [];
	#historyLength = $state(0);
	/** The next file and its plan, found while this one plays; keyed by era. */
	#ahead: { era: number; file: Playable; plan: PlaybackPlan } | null = null;
	#looking: { era: number; done: Promise<void> } | null = null;
	/** An era, since a stale flag would stop the next source's fill. */
	#filling: number | null = null;
	#sitting: CellSitting | null = null;
	/** Bumped when the source changes or the cell empties, so a stale answer cannot attach. */
	#era = 0;
	#offset = 0;
	/** The last file filled; a seekable order continues after it, so no file repeats. */
	#last: string | null = null;
	#more = true;
	/** The round's order where one page held the whole source, so the next round differs. */
	#round: string[] | null = null;
	/** Files of a one-page round stepped over, so the next round is judged on what was shown. */
	#passed = new Set<string>();
	/** Whether each file of a one-page round played direct, for `showingOrder`. */
	#direct = new Map<string, boolean>();
	#shownRound: string[] = [];

	/** Failures in a row: a wall is unattended, so a few are stepped over before it stops. */
	#failures = 0;

	/** The order this cell draws in; a fresh cell is Random with its own seed. */
	sort = $state<string | null>(RANDOM);

	/** Which shuffle while Random; not saved, so a wall put away is shuffled afresh. */
	seed = $state<number | null>(mintSeed());

	/** Put this cell in an order, minting a shuffle when it is one. The caller rebuilds the run. */
	orderBy(next: string | null): void {
		this.sort = next;
		this.seed = next === RANDOM ? mintSeed() : null;
	}

	get query(): Record<string, string> {
		const asked: Record<string, string> = {};
		if (this.source.trim()) asked.q = this.source.trim();
		if (this.sort) asked.sort = this.sort;
		// A seed only beside Random: the server ignores it under any other order.
		if (this.sort === RANDOM && this.seed !== null) asked.seed = String(this.seed);
		if (this.mediaKind === 'video_gif') asked.media = 'video|gif';
		return asked;
	}

	/** What the wall asks the random route for, or null under any order but Random. */
	get drawQuery(): Record<string, string> | null {
		if (this.sort !== null && this.sort !== RANDOM) return null;
		const narrowing = { ...this.query };
		delete narrowing.sort;
		delete narrowing.seed;
		return narrowing;
	}

	get hasBack(): boolean {
		return this.#historyLength > 1;
	}

	/** One file on screen in one cell is one view however often it repeats or is redrawn. */
	sittingWith(file: string): CellSitting {
		if (this.#sitting?.file !== file) this.#sitting = { file, id: newSittingId(), reported: null };
		return this.#sitting;
	}

	/** The size the player drew a file at, which beats the stored one. */
	measured = $state<{ file: string; width: number; height: number } | null>(null);

	unreachable = $state(false);
	#missed: 'none' | 'unreached' | 'other' = 'none';

	posterSize = $state<{ file: string; width: number; height: number } | null>(null);

	coming = $state<Playable | null>(null);

	/** The shape every surface draws this cell at: chosen, measured, stored, then the poster's. */
	get shape(): number | null {
		const asked = ratioOf(this.aspect);
		if (asked !== null) return asked;
		const file = this.playing ?? this.coming;
		if (file === null) return null;
		const seen = this.measured?.file === file.id ? this.measured : null;
		if (file.width && file.height)
			return seen ? seen.width / seen.height : file.width / file.height;
		const poster = this.posterSize?.file === file.id ? this.posterSize : null;
		if (
			poster &&
			(!seen || Math.abs((seen.width / seen.height) * poster.height - poster.width) <= 2)
		)
			return poster.width / poster.height;
		return seen ? seen.width / seen.height : null;
	}

	measure(file: string, width: number, height: number): void {
		if (width > 0 && height > 0) this.measured = { file, width, height };
	}

	measurePoster(file: string, width: number, height: number): void {
		if (width > 0 && height > 0 && this.measured?.file !== file)
			this.posterSize = { file, width, height };
	}

	/* The filter in the bar's named spelling, which the facet columns tick from; believed only
	   while folding it gives back exactly `source`. */
	#written = $state('');

	/** A filter chosen while this cell played, waiting for the file to end. */
	#pending = $state<string | null>(null);

	get narrowingWaits(): boolean {
		return this.#pending !== null;
	}

	get narrowing(): URLSearchParams {
		if (this.#pending !== null) return new URLSearchParams(this.#pending);
		const held = new URLSearchParams(this.#written);
		const same = savedQuery(new URLSearchParams(this.#written).toString()) === this.source;
		const out = same ? held : new URLSearchParams(this.source ? { q: this.source } : {});
		/* The search mode travels with the filter: a smart search is a different set of files. */
		out.delete('sort');
		if (this.sort) out.set('sort', this.sort);
		return out;
	}

	/** Filter this cell to what the bar wrote: at the end of the file, or now if nothing plays. */
	narrowTo(params: URLSearchParams, { playing = true }: { playing?: boolean } = {}): void {
		if (playing && !this.paused && this.playing !== null) {
			this.#pending = params.toString();
			return;
		}
		this.#pending = null;
		void this.#narrow(params);
	}

	#narrow(params: URLSearchParams): Promise<void> {
		this.#written = params.toString();
		this.source = savedQuery(this.#written);
		// Through `orderBy`, so Random gets a seed; a search by meaning goes with its filter.
		const asked = params.get('sort');
		this.orderBy(asked ?? (this.sort === SIMILARITY ? null : this.sort));
		return this.restart();
	}

	async #applyPending(): Promise<void> {
		const params = new URLSearchParams(this.#pending ?? '');
		this.#pending = null;
		await this.#narrow(params);
	}

	get saved(): SavedCell {
		return {
			source: this.source,
			media_kind: this.mediaKind,
			ordering: this.ordering,
			end_behaviour: this.endBehaviour,
			timer_seconds: this.timerSeconds,
			volume: this.volume,
			sort: this.sort,
			aspect: this.aspect
		};
	}

	/* Every stored word is checked, so a later version's wall cannot leave a cell inert. */
	adopt(saved: SavedCell): void {
		this.#take(saved);
		void this.restart();
	}

	/** Take over another cell's source and file, from `at`: a promotion out of the strip. */
	async takeOver(saved: SavedCell, file: Playable | null, at = 0): Promise<void> {
		this.#take(saved);
		if (file === null) {
			await this.restart();
			return;
		}
		await this.startOn(file, at);
	}

	/** Open on one file and find the rest from the top; the era bump keeps old answers out. */
	async startOn(file: Playable, at = 0): Promise<void> {
		const era = ++this.#era;
		this.#queue = [];
		this.#ahead = null;
		this.#history = [];
		this.#historyLength = 0;
		this.#missed = 'none';
		this.#passed.clear();
		this.#direct.clear();
		this.#shownRound = [];
		this.#offset = 0;
		this.#last = null;
		this.#more = true;
		this.#failures = 0;
		this.problem = null;
		this.state = 'loading';
		this.coming = file;
		let plan: PlaybackPlan | null;
		try {
			plan = await this.#planOr(file.id);
		} catch {
			// The ask failed, not the file: the pressed file stays, and a press asks again.
			if (era !== this.#era) return;
			this.problem = PLAN_UNASKED;
			this.state = 'failed';
			return;
		}
		if (era !== this.#era) return;
		if (plan === null) {
			await this.restart();
			return;
		}
		this.#history.push(file);
		this.#historyLength = this.#history.length;
		this.resumeAt = at > 0 ? at : null;
		this.#show(file, plan);
	}

	/** Take up a saved cell on a file known by id, asked of the server so Hidden is obeyed. */
	async resumeOn(saved: SavedCell, id: string, at = 0): Promise<void> {
		this.#take(saved);
		const era = ++this.#era;
		// Out of `empty` now, or the wall's opening draw fills this cell too (`Wall.open`).
		this.state = 'loading';
		const file = await keptFile(id);
		if (era !== this.#era) return;
		await (file === null ? this.restart() : this.startOn(file, at));
	}

	#take(saved: SavedCell): void {
		this.#pending = null;
		this.source = saved.source;
		this.mediaKind = isMediaKind(saved.media_kind) ? saved.media_kind : 'video_gif';
		this.endBehaviour = isLoopMode(saved.end_behaviour) ? saved.end_behaviour : 'loop_all';
		this.timerSeconds = saved.timer_seconds ?? null;
		/* The level is the account's, not the wall's. `sort` passes unchecked for a later version's
		   mode, through `orderBy` so Random gets a fresh shuffle. */
		this.orderBy(typeof saved.sort === 'string' && saved.sort ? saved.sort : null);
		this.aspect = isAspect(saved.aspect) ? saved.aspect : DEFAULT_ASPECT;
	}

	/** Point the cell at something else and start from the top; a new run is a new draw. */
	async restart(avoiding: string | null = null): Promise<void> {
		const era = ++this.#era;
		this.#queue = [];
		this.#ahead = null;
		this.#history = [];
		this.#historyLength = 0;
		this.#missed = 'none';
		this.#passed.clear();
		this.#direct.clear();
		this.#shownRound = [];
		this.#offset = 0;
		this.#last = null;
		this.#more = true;
		this.#failures = 0;
		if (this.sort === RANDOM) this.seed = mintSeed();
		this.plan = null;
		this.playing = this.coming = null;
		this.sheet = null;
		this.facts = null;
		this.repair = null;
		this.state = 'loading';
		await this.#fill();
		if (era !== this.#era) return;
		this.#leaveOut(avoiding);
		await this.advance();
	}

	/** Somewhere else out of this cell's source, never the file on screen: the casino control. */
	async somethingElse(): Promise<void> {
		await this.restart(this.playing?.id ?? null);
	}

	#leaveOut(id: string | null): void {
		if (id === null) return;
		const rest = this.#queue.filter((file) => file.id !== id);
		if (rest.length > 0 || this.#more) this.#queue = rest;
	}

	/** Change what comes next without changing what is on screen. */
	async reorder(): Promise<void> {
		this.#era += 1;
		this.#queue = [];
		this.#ahead = null;
		this.#offset = 0;
		this.#last = null;
		this.#more = true;
		await this.#fill();
	}

	/** Let go and stop; the run goes too, since its ids may be out of this account's reach now. */
	release(state: CellState = 'empty'): void {
		this.#era += 1;
		this.#queue = [];
		this.#ahead = null;
		this.#pending = null;
		this.#history = [];
		this.#historyLength = 0;
		this.#missed = 'none';
		this.#passed.clear();
		this.#direct.clear();
		this.#shownRound = [];
		this.playing = this.coming = null;
		this.plan = null;
		this.sheet = null;
		this.facts = null;
		this.repair = null;
		this.loop.clear();
		this.state = state;
	}

	async advance(): Promise<void> {
		const era = this.#era;
		if (this.#pending !== null) {
			await this.#applyPending();
			return;
		}

		const looking = this.#looking;
		if (looking !== null && looking.era === era) await looking.done;

		/* Taken, not read: a held file is already out of the queue and would show twice. */
		const ready = this.#ahead;
		this.#ahead = null;
		if (ready !== null && ready.era === era) {
			this.#remember(ready.file);
			this.#show(ready.file, ready.plan);
			return;
		}

		if (this.#queue.length <= REFILL_AT && this.#more) await this.#fill();
		if (era !== this.#era) return;

		let next = await this.#nextPlayable();
		if (era !== this.#era) return;

		/* The run has ended and this cell plays through; only after something played. */
		if (next === null && this.endBehaviour === 'loop_all' && this.#history.length > 0) {
			next = await this.#startOver(era);
			if (era !== this.#era) return;
		}

		if (next === null) {
			// Something shown and now spent, or nothing this account may see: two sentences.
			this.state = this.#history.length ? 'stopped' : 'nothing_here';
			this.unreachable = this.#missed === 'unreached';
			this.playing = null;
			this.plan = null;
			return;
		}

		this.#remember(next.file);
		this.#show(next.file, next.plan);
	}

	async #startOver(era: number): Promise<Choice | null> {
		const before = this.#round;
		const shown = this.#shownRound;
		this.#shownRound = [];
		this.#offset = 0;
		this.#last = null;
		this.#more = true;
		if (this.sort === RANDOM) this.seed = mintSeed();
		await this.#fill();
		if (era !== this.#era) return null;
		// A fresh seed over a source of a few files can deal the round just played again.
		if (this.sort === RANDOM && before !== null && !this.#more) {
			const ended = this.#history.at(-1)?.id ?? null;
			this.#queue = anotherRound(shown, this.#queue, ended, this.#passed, (ids) =>
				showingOrder(ids, (id) => this.#direct.get(id) !== false, DIRECT_TRIES, ended)
			);
			this.#round = this.#queue.map((one) => one.id);
		}
		return await this.#nextPlayable();
	}

	#stepOver(id: string): void {
		if (this.#round !== null) this.#passed.add(id);
	}

	#note(unreached: boolean): void {
		if (this.#missed !== 'other') this.#missed = unreached ? 'unreached' : 'other';
	}

	#remember(file: Playable): void {
		if (this.#round !== null) this.#shownRound.push(file.id);
		this.#history.push(file);
		if (this.#history.length > HISTORY) this.#history.shift();
		this.#historyLength = this.#history.length;
	}

	/** Find the next file while there is time, touching nothing on screen. */
	#lookAhead(): void {
		const era = this.#era;
		if (this.#looking !== null || this.#ahead !== null) return;
		const done = this.#find(era);
		this.#looking = { era, done };
		void done.finally(() => {
			if (this.#looking?.done === done) this.#looking = null;
		});
	}

	async #find(era: number): Promise<void> {
		try {
			if (this.#queue.length <= REFILL_AT && this.#more) await this.#fill();
			if (era !== this.#era) return;
			const next = await this.#nextPlayable();
			if (era !== this.#era || next === null) return;
			this.#ahead = { era, file: next.file, plan: next.plan };
		} catch {
			// A failed early lookup must not stop the cell.
		}
	}

	async back(): Promise<void> {
		if (!this.hasBack) return;
		const era = this.#era;
		this.#history.pop();
		this.#historyLength = this.#history.length;
		const previous = this.#history[this.#history.length - 1];
		this.state = 'loading';
		let plan: PlaybackPlan | null;
		try {
			plan = await this.#planOr(previous.id);
		} catch {
			if (era !== this.#era) return;
			this.problem = PLAN_UNASKED;
			this.state = 'failed';
			return;
		}
		if (era !== this.#era) return;
		if (plan === null) {
			this.state = 'failed';
			return;
		}
		this.#show(previous, plan);
	}

	/** The browser could not play this file; a few in a row and the cell holds on the message. */
	async failed(reason: string | null = null): Promise<void> {
		this.#failures += 1;
		if (reason !== null) this.problem = reason;
		this.state = 'failed';
		if (this.endBehaviour === 'once' || this.#failures >= GIVE_UP_AFTER) return;
		await this.advance();
	}

	async ended(): Promise<void> {
		if (this.#pending !== null) {
			await this.#applyPending();
			return;
		}
		if (this.endBehaviour === 'loop_one') {
			// A still has no replay and needs none: a GIF loops on its own.
			this.replay?.();
			return;
		}
		if (this.endBehaviour === 'once') {
			this.state = 'stopped';
			return;
		}
		await this.advance();
	}

	/** Something is playing: the only honest end to a run of failures. */
	started(): void {
		this.#failures = 0;
		/* Found on the element starting, so the opening draw has no nine cells asking together. */
		void this.#lookAhead();
	}

	#show(file: Playable, plan: PlaybackPlan): void {
		this.problem = null;
		if (!this.loop.owns(file.id)) this.loop.clear();
		this.playing = file;
		this.plan = plan;
		this.quality = null;
		this.sheet = null;
		this.facts = null;
		this.repair = null;
		// Not a failure: a cell waiting on the install's one transcoder.
		this.state = plan.streamable ? 'ready' : 'waiting';
		void this.#readDetail(file.id);
	}

	/** Fetch the scrub strip and facts for the file on screen; a failure leaves it playing. */
	async #readDetail(id: string): Promise<void> {
		const era = this.#era;
		try {
			const detail = await api.get<
				{ sprite?: SpriteSheet | null; playback_repair?: string | null } & FileFacts
			>(`/assets/${id}`);
			if (era !== this.#era || this.playing?.id !== id) return;
			this.sheet = detail.sprite ?? null;
			this.repair = detail.playback_repair ?? null;
			this.facts = {
				width: detail.width,
				height: detail.height,
				container: detail.container,
				size_bytes: detail.size_bytes,
				vcodec: detail.vcodec,
				acodec: detail.acodec,
				fps: detail.fps,
				bit_depth: detail.bit_depth
			};
		} catch {
			// No preview above the scrubber and no facts in the panel. Everything else still works.
		}
	}

	/**
	 * The next file this cell can play: direct preferred among a few, and those passed over go back
	 * to the front in order (`showingOrder` predicts it).
	 */
	async #nextPlayable(): Promise<Choice | null> {
		const era = this.#era;
		const taken = new Set(this.elsewhere());
		if (this.playing !== null) taken.add(this.playing.id);
		const found = await this.#gather(era, taken);
		if (found === null) return null;
		const { fallback, held, looked } = found;
		let { chosen } = found;

		chosen ??= fallback;
		if (chosen === null && held !== null) {
			const plan = await this.#planOr(held.id).catch(() => null);
			if (era !== this.#era) return null;
			if (plan !== null && plan.route !== 'unread') chosen = { file: held, plan };
		}
		if (chosen === null) return null;
		const picked = chosen.file;
		this.#queue.unshift(...looked.filter((file) => file !== picked));
		return chosen;
	}

	async #gather(
		era: number,
		taken: Set<string>
	): Promise<{
		chosen: Choice | null;
		fallback: Choice | null;
		held: Playable | null;
		looked: Playable[];
	} | null> {
		let chosen: Choice | null = null;
		let fallback: Choice | null = null;
		let held: Playable | null = null;
		const looked: Playable[] = [];
		let converted = 0;

		while (this.#queue.length) {
			const file = this.#queue.shift() as Playable;
			if (taken.has(file.id)) {
				held ??= file;
				looked.push(file);
				if (this.#queue.length <= REFILL_AT && this.#more) await this.#fill();
				if (era !== this.#era) return null;
				continue;
			}
			let plan: PlaybackPlan | null;
			try {
				plan = await this.#planOr(file.id);
			} catch {
				// An unattended run steps over a file the server would not answer about.
				if (era !== this.#era) return null;
				this.#note(false);
				continue;
			}
			if (era !== this.#era) return null;
			// A file Sift has not read is stepped over; the ask itself queues the read.
			if (plan === null || plan.route === 'unread') {
				this.#note(plan === null);
				this.#stepOver(file.id);
				continue;
			}
			this.#passed.delete(file.id);
			if (this.#round !== null) this.#direct.set(file.id, plan.route === 'direct');
			looked.push(file);
			if (plan.route === 'direct') {
				chosen = { file, plan };
				break;
			}
			fallback ??= { file, plan };
			converted += 1;
			if (converted >= DIRECT_TRIES) break;
			if (this.#queue.length <= REFILL_AT && this.#more) await this.#fill();
			if (era !== this.#era) return null;
		}
		return { chosen, fallback, held, looked };
	}

	/** The server's plan for this file; null only on a 404, anything else asked once more. */
	async #planOr(id: string): Promise<PlaybackPlan | null> {
		for (let attempt = 0; ; attempt += 1) {
			try {
				return await planFor(id);
			} catch (error) {
				if (error instanceof ApiError && error.status === 404) return null;
				if (attempt >= 1) throw error;
			}
		}
	}

	/** Add the source's next page to the run; a shuffle's pages are already shuffled. */
	async #fill(): Promise<void> {
		const era = this.#era;
		if (this.#filling === era || !this.#more) return;
		this.#filling = era;
		const opening = this.#offset === 0 && this.#last === null;
		if (opening) {
			this.#round = null;
			this.#shownRound = [];
		}
		try {
			const asked = this.query;
			const from: Record<string, string> =
				this.#last !== null && continuable(asked)
					? { after: this.#last }
					: { offset: String(this.#offset) };
			const page = await api.get<components['schemas']['AssetPageResponse']>('/assets', {
				query: { ...asked, limit: String(PAGE), ...from },
				signal: AbortSignal.timeout(PAGE_TIMEOUT_MS)
			});
			if (era !== this.#era) return;
			this.#offset += page.items.length;
			this.#last = page.items.at(-1)?.id ?? this.#last;
			this.#more = page.items.length > 0 && this.#offset < page.total;
			this.#queue.push(...page.items);
			if (opening) {
				this.#round = this.#more ? null : page.items.map((one) => one.id);
				const round = new Set(this.#round);
				for (const id of this.#passed) if (!round.has(id)) this.#passed.delete(id);
				for (const id of this.#direct.keys()) if (!round.has(id)) this.#direct.delete(id);
			}
		} catch {
			this.#more = false;
		} finally {
			// Only this era's flag: an older fill must not clear the one that replaced it.
			if (this.#filling === era) this.#filling = null;
		}
	}
}
