/* One cell of the wall: what it is drawing from, what it is playing, and what it does next.
 *
 * A cell is handed a QUERY, not a file, and keeps finding itself something to play from it without
 * asking anybody: filling a run, moving through it, stepping back, and stopping honestly when
 * nothing is left. Sift owns every value and the element enforces it: repeating is decided here on
 * `ended` (the element's `loop` is explicitly off) because only the cell knows whether "again"
 * means this file or the next; seeking, decoding and the network are the element's. Every value is
 * passed to the element on every render (`Cell.svelte`), and a gate refuses one left out.
 */

import { api, ApiError } from '$lib/api/client';
import { Loop } from '$lib/player/loop.svelte';
import { loudness } from '$lib/player/loudness.svelte';
import { planFor, type PlaybackPlan, type Quality } from '$lib/player/playback';
import type { FileFacts } from '$lib/player/facts';
import { isLoopMode, type LoopMode } from '$lib/player/loop-modes';

/** Closest match by meaning: a search mode rather than an order, so it goes with its filter. */
const SIMILARITY = 'similarity';

/** What a cell says when the server would not say how to play the file that was pressed. */
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

/**
 * One file, as a cell knows it: the grid's own tile, so the poster, length and kind arrive with
 * the page that found it. The scrub strip is fetched only when a file reaches the screen.
 */
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
	/* What a verb reads to word itself, already on every `AssetSummary` the run is filled from.
	 * `concealed` because `Actionable` names all three and Save reads it. */
	| 'favorite'
	| 'rating'
	| 'concealed'
>;

/**
 * A cell as it is written down: what it draws from and how it behaves, never what it was playing.
 *
 * The wire's spelling, so loading, changing and saving a wall sends back what it was given. The
 * read side (`-Output`) because it has every field, and a whole cell is always accepted.
 */
export type SavedCell = components['schemas']['CellBody-Output'];

/**
 * One cell's sitting with one file: which file, what the sitting is called, and how much of it the
 * server has already been told: the report's `already_reported_ms`, which is what makes a sitting
 * delivered in pieces one view rather than one per piece. Mutable, and owned by the cell.
 */
export interface CellSitting {
	readonly file: string;
	readonly id: string;
	reported: number | null;
}

/** What a cell will draw. Either things that move, or those and photographs as well. */
export const MEDIA_KINDS = ['video_gif', 'all'] as const;
export type MediaKind = (typeof MEDIA_KINDS)[number];

/** Whether a cell walks its source in order or shuffles the whole of it. The drawer's word. */
export type Ordering = 'in_order' | 'shuffle';

/** What a cell holds elsewhere when the wall has not said: nothing. */
const NOTHING_ELSEWHERE: ReadonlySet<string> = new Set();

/* The server keeps these words as free text so one service judges both save and load; a guard
 * narrows them here so the fallback is reached because the type agrees the word is unknown. */
function isMediaKind(value: string): value is MediaKind {
	return (MEDIA_KINDS as readonly string[]).includes(value);
}

/** How much of a cell's source is asked for at a time. A page, not the library. */
const PAGE = 60;

/** Refill once the run has this few left, so a cell never waits on a request to move on. */
const REFILL_AT = 8;

/** How far back a cell remembers, for the control that steps back through what it has shown. */
const HISTORY = 40;

/** How long a page of files may take before the ask is given up, as `PLAN_TIMEOUT_MS`. */
export const PAGE_TIMEOUT_MS = 15_000;

/** How many files are asked about before a converted one is taken: the install has one converter. */
const DIRECT_TRIES = 4;

/** How many files in a row may fail before the cell stops: three is a fact about the library. */
const GIVE_UP_AFTER = 3;

/** What a cell is doing, in the one word its overlay needs. */
type CellState = 'empty' | 'loading' | 'ready' | 'waiting' | 'stopped' | 'nothing_here' | 'failed';

let counter = 0;

/** The elements a screenshot of a cell is taken from. See `Cell.shot`. */
export interface CellPicture {
	video: HTMLVideoElement | null;
	still: HTMLImageElement | HTMLCanvasElement | null;
	stage: HTMLElement | null;
}

export class Cell {
	/** Stable for the life of the wall, so a cell keeps its identity across a layout change. */
	readonly key = `cell-${++counter}`;

	/* --- what it draws from ------------------------------------------------------------------ */

	/** The query, spelled the way it would be typed. Empty means the whole library. */
	source = $state('');
	mediaKind = $state<MediaKind>('video_gif');

	/**
	 * Whether this cell shuffles: the drawer's word for the Random order, read off `sort` so the
	 * drawer and the bar's Sort can never disagree.
	 */
	get ordering(): Ordering {
		return this.sort === RANDOM ? 'shuffle' : 'in_order';
	}

	/**
	 * The files on screen, or lined up next, in the wall's OTHER cells. Installed by the wall; a
	 * cell steps over these while it has anything else to play, so two cells do not show one file.
	 */
	elsewhere: () => ReadonlySet<string> = () => NOTHING_ELSEWHERE;

	/** The file this cell has lined up to play next, when it has found one. See `elsewhere`. */
	get nextUp(): string | null {
		return this.#ahead?.file.id ?? null;
	}
	/**
	 * What shape this cell is held to, or `dynamic` for the shape of whatever it is playing. On the
	 * cell, not the layout, so a cell keeps its shape when the wall is rearranged.
	 */
	aspect = $state<AspectId>(DEFAULT_ASPECT);
	endBehaviour = $state<LoopMode>('loop_all');
	/** Seconds, or null to wait for the file to end. A cell of photographs always carries one. */
	timerSeconds = $state<number | null>(null);

	/* --- how it sounds ----------------------------------------------------------------------- */

	/**
	 * How loud: the application's one level (`$lib/player/loudness`), not this cell's own, so a level
	 * set anywhere reaches every picture. What a cell owns is `muted`: which picture is heard.
	 */
	get volume(): number {
		return loudness.level;
	}

	set volume(level: number) {
		loudness.set(level);
	}

	/** Every cell starts muted. No browser will begin several videos with sound unasked. */
	muted = $state(true);

	/* --- where the playhead is ---------------------------------------------------------------- */

	/*
	 * The playhead and the length, in seconds, kept here because both bars draw them. `seek` is
	 * installed by the view while mounted, so a stale seek never reaches a released element.
	 */
	position = $state(0);

	/**
	 * Where the NEXT file this cell shows should start from, or null for the beginning of it.
	 *
	 * A promotion rebuilds both elements, so without this a preview brought up into the wall would
	 * restart. On the cell because only the cell survives the swap; the view clears it once used.
	 */
	resumeAt = $state<number | null>(null);
	/** Why the cell is failed, when it is failed for a reason the default sentence does not cover. */
	problem = $state<string | null>(null);
	duration = $state(0);
	/**
	 * How fast this cell plays, as a multiple of ordinary speed.
	 *
	 * Sift's value because the element is rebuilt on every file, promotion and quality change. Not
	 * saved: a rate is a key held down, and one that survived a restart would be a cell stuck slow.
	 */
	rate = $state(1);
	seek: ((seconds: number) => void) | null = $state(null);
	/**
	 * Start this file again from the top, for `loop_one`. Installed by the view, as `seek` is.
	 *
	 * A separate seam because an ended element is paused, so a seek to zero alone would leave a
	 * still frame at the beginning.
	 */
	replay: (() => void) | null = $state(null);
	/** What a screenshot of this cell is taken from (`takeShot`). Installed by the view. */
	shot: (() => CellPicture) | null = $state(null);
	/**
	 * Watch this cell at a different size. Installed by the view, because swapping the stream under
	 * a running element and carrying the playhead across needs the element.
	 */
	changeQuality: ((quality: Quality) => void) | null = $state(null);
	/** Which rung is being watched, or null for whatever the plan opened on. */
	quality = $state<Quality | null>(null);

	/* --- what is open over this cell's picture ------------------------------------------------ */

	/*
	 * Whether the panel of facts and the hold-time field are open.
	 *
	 * On the cell because the control is on the wall's bar and the panel over the cell's picture,
	 * two components that must read one answer. Not saved: what is open is a thing done now.
	 */
	factsOpen = $state(false);
	timing = $state(false);
	/** Whether the list of sizes is open under the bar. One answer, as for the two above. */
	qualityOpen = $state(false);

	/* --- what it is playing ------------------------------------------------------------------ */

	/**
	 * This one cell, held. Separate from the wall's hold and both obeyed, so a cell somebody stopped
	 * stays stopped when the wall starts: either saying stop is enough.
	 */
	paused = $state(false);

	state = $state<CellState>('empty');
	/** The file on screen, or null when there is none. */
	playing = $state<Playable | null>(null);
	plan = $state<PlaybackPlan | null>(null);

	/**
	 * Whether this file's sound is stored too far from its picture, and what is happening about it:
	 * `pending`, `repaired`, `off`, or null for nearly every file. Read off the facts' own detail.
	 */
	repair = $state<string | null>(null);
	/** How the file on screen has its scrub strip cut up, when it has one. */
	sheet = $state<SpriteSheet | null>(null);
	/**
	 * What the library knows about the file on screen, for the facts panel. It arrives on the same
	 * detail response as the scrub strip, so keeping it costs no request.
	 */
	facts = $state<FileFacts | null>(null);

	/**
	 * The A-B loop, one per cell rather than the players' shared one, which tells clips apart: two
	 * cells may hold the same file, and marks on one must not drag the other.
	 */
	readonly loop = new Loop();

	/** What is lined up, and what has already been shown. */
	#queue: Playable[] = [];
	#history: Playable[] = [];
	#historyLength = $state(0);
	/**
	 * The next file and its plan, found while the current one plays so only the element loads in the
	 * gap. Keyed by era, so a source changed meanwhile cannot attach the old source's file.
	 */
	#ahead: { era: number; file: Playable; plan: PlaybackPlan } | null = null;
	/** The lookahead on the wire, which an advance waits for rather than searching twice. */
	#looking: { era: number; done: Promise<void> } | null = null;
	/** Which era's fill is in flight: an era, since a stale flag would stop the next source's fill. */
	#filling: number | null = null;
	/** The sitting this cell is in with the file it is showing. See `sittingWith`. */
	#sitting: CellSitting | null = null;
	/**
	 * Which fill and which advance this is. Bumped whenever the source changes or the cell is
	 * emptied, so an answer for a source the cell has already left cannot attach itself.
	 */
	#era = 0;
	/** How many files the run has been filled with from this source, which is its offset. */
	#offset = 0;
	/**
	 * The last file the run was filled with. An order the server can seek continues after this row
	 * rather than from an offset: one seek at any depth, and no file repeats across pages.
	 */
	#last: string | null = null;
	/** Whether the source has any more to give beyond what has been asked for. */
	#more = true;
	/**
	 * The order of the round being played, where one page held the whole source; null where it
	 * did not. Read at the wrap, so a small source comes round in a different order (`anotherRound`).
	 */
	#round: string[] | null = null;
	/**
	 * The files of a one-page round this cell stepped over rather than played (gone, or not read
	 * yet). Read at the wrap with `#round`, so the next round is judged on what was shown. Never
	 * more than a page; emptied with the history.
	 */
	#passed = new Set<string>();
	/**
	 * Whether each file of a one-page round played as it is (true) or was converted (false). Read at
	 * the wrap so the next round is judged on the order it is SHOWN in (`showingOrder`).
	 */
	#direct = new Map<string, boolean>();
	/** The files of a one-page round in the order they were shown, for the next round's judgement. */
	#shownRound: string[] = [];

	/**
	 * How many files in a row this cell has failed to play.
	 *
	 * A wall is unattended, so a few failures are stepped over and a run of them stops the cell with
	 * a sentence. Reset the moment anything plays.
	 */
	#failures = 0;

	/**
	 * WHAT ORDER this cell draws in, or null for the ordinary answer; saved as the server's `sort`.
	 * A fresh cell is Random with its own seed, so two cells walk different runs.
	 */
	sort = $state<string | null>(RANDOM);

	/**
	 * WHICH shuffle while the order is Random, else null: one permutation across a run's pages.
	 * Not saved, so a wall put away in Random is shuffled afresh (`#take`).
	 */
	seed = $state<number | null>(mintSeed());

	/** Put this cell in an order, minting a shuffle when it is one. The caller rebuilds the run. */
	orderBy(next: string | null): void {
		this.sort = next;
		this.seed = next === RANDOM ? mintSeed() : null;
	}

	/** The named parameters this cell's source becomes. */
	get query(): Record<string, string> {
		const asked: Record<string, string> = {};
		if (this.source.trim()) asked.q = this.source.trim();
		if (this.sort) asked.sort = this.sort;
		// A seed only beside Random: the server ignores it under any other order.
		if (this.sort === RANDOM && this.seed !== null) asked.seed = String(this.seed);
		// Only the cell's own control writes this, so the picker offers no media column.
		if (this.mediaKind === 'video_gif') asked.media = 'video|gif';
		return asked;
	}

	/**
	 * What the wall asks the random route for, filtered as this cell's run is, or null under any
	 * order but Random (a draw ignores an order).
	 */
	get drawQuery(): Record<string, string> | null {
		if (this.sort !== null && this.sort !== RANDOM) return null;
		// The draw brings a seed of its own; this cell's seed is for the run it continues with.
		const narrowing = { ...this.query };
		delete narrowing.sort;
		delete narrowing.seed;
		return narrowing;
	}

	/** Whether there is anything behind this one to step back to. */
	get hasBack(): boolean {
		return this.#historyLength > 1;
	}

	/**
	 * The sitting this cell is in with a file: the running one for the same file, else a new one.
	 * One file on screen in one cell is one view however often it repeats or is redrawn.
	 */
	sittingWith(file: string): CellSitting {
		if (this.#sitting?.file !== file) this.#sitting = { file, id: newSittingId(), reported: null };
		return this.#sitting;
	}

	/** The size the player drew a file at, which beats the stored one (non-square pixels, no probe). */
	measured = $state<{ file: string; width: number; height: number } | null>(null);

	/** Read under `nothing_here`: whether every file this run stepped over was out of Sift's reach. */
	unreachable = $state(false);
	#missed: 'none' | 'unreached' | 'other' = 'none';

	/** The poster's own size, for a file with no stored size that the player has not measured yet. */
	posterSize = $state<{ file: string; width: number; height: number } | null>(null);

	/** The file being started, whose shape the cell holds while its plan is asked. */
	coming = $state<Playable | null>(null);

	/**
	 * The shape the cell, the wall and the corner panel all draw this cell at, or null: the chosen
	 * shape, the measured size, the stored size, then the poster's, kept through its even rounding.
	 */
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

	/** Note the size the player drew this file at; a zero size is no answer. */
	measure(file: string, width: number, height: number): void {
		if (width > 0 && height > 0) this.measured = { file, width, height };
	}

	/** Note the poster's size, unless the player has already measured the file. */
	measurePoster(file: string, width: number, height: number): void {
		if (width > 0 && height > 0 && this.measured?.file !== file)
			this.posterSize = { file, width, height };
	}

	/*
	 * The filter as the shared bar last wrote it, in the bar's own NAMED spelling (`?tags=beach`).
	 *
	 * The facet columns tick from the named form while the cell stores and saves the typed one, so
	 * folding a click into the typed form would untick the column. Not saved; and believed only
	 * while folding it gives back exactly `source`, so any other route that sets the source retires
	 * it rather than leaving a stale tick.
	 */
	#written = $state('');

	/**
	 * THE FILTER SOMEBODY CHOSE WHILE THIS CELL WAS PLAYING, waiting for the file to end.
	 *
	 * Facets are ticked one at a time, and each throwing the run away would cut off the file being
	 * watched. The panel is told immediately (`narrowing`), so the tick holds; only the run waits.
	 */
	#pending = $state<string | null>(null);

	/** Whether a filter is waiting for this file to end. What the cell draws its small mark from. */
	get narrowingWaits(): boolean {
		return this.#pending !== null;
	}

	/** This cell's filter, in the spelling the shared bar reads and writes. See `#written`. */
	get narrowing(): URLSearchParams {
		/* Already in the panel's spelling, so it needs neither the check nor the sort below. */
		if (this.#pending !== null) return new URLSearchParams(this.#pending);
		const held = new URLSearchParams(this.#written);
		const same = savedQuery(new URLSearchParams(this.#written).toString()) === this.source;
		const out = same ? held : new URLSearchParams(this.source ? { q: this.source } : {});
		/* The search MODE travels with the filter: a smart search is a different set of files. */
		out.delete('sort');
		if (this.sort) out.set('sort', this.sort);
		return out;
	}

	/**
	 * Filter this cell to what the bar wrote: at the end of the file, or now if nothing is playing.
	 * `playing` is the wall's hold, which only the caller knows.
	 */
	narrowTo(params: URLSearchParams, { playing = true }: { playing?: boolean } = {}): void {
		if (playing && !this.paused && this.playing !== null) {
			this.#pending = params.toString();
			return;
		}
		this.#pending = null;
		void this.#narrow(params);
	}

	/** Take a filter and start again from the top of it. The one door both ways in use. */
	#narrow(params: URLSearchParams): Promise<void> {
		this.#written = params.toString();
		this.source = savedQuery(this.#written);
		// Through `orderBy`, so `sort=random` gets a seed. A write naming no order keeps the cell's,
		// except a search by meaning, which goes with the filter that carried it.
		const asked = params.get('sort');
		this.orderBy(asked ?? (this.sort === SIMILARITY ? null : this.sort));
		return this.restart();
	}

	/**
	 * The waiting filter takes effect. Called at the end of the file that was playing when it was
	 * chosen, and by an advance, because both are this cell leaving that file.
	 */
	async #applyPending(): Promise<void> {
		const params = new URLSearchParams(this.#pending ?? '');
		this.#pending = null;
		await this.#narrow(params);
	}

	/** Everything about a cell that is saved under a name. Never what it was playing. */
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

	/*
	 * Take up a saved cell. Every stored word is checked, and anything this version does not know
	 * falls back to a fresh cell's value, so a later version's wall cannot leave a cell inert.
	 */
	adopt(saved: SavedCell): void {
		this.#take(saved);
		void this.restart();
	}

	/**
	 * Take over another cell's source AND the file it had up, from `at`: a promotion out of the
	 * strip, where adopting would pick a different clip and a preview means that thing.
	 */
	async takeOver(saved: SavedCell, file: Playable | null, at = 0): Promise<void> {
		this.#take(saved);
		if (file === null) {
			await this.restart();
			return;
		}
		await this.startOn(file, at);
	}

	/**
	 * OPEN ON ONE PARTICULAR FILE, and find the rest from the top of the source. The run is emptied
	 * and the era bumped so nothing in flight lands on it; the file goes into the history.
	 */
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
			// The ask failed, not the file: the pressed file stays, a sentence says so, and a press
			// asks again, rather than the wall seeming to ignore the choice.
			if (era !== this.#era) return;
			this.problem = PLAN_UNASKED;
			this.state = 'failed';
			return;
		}
		if (era !== this.#era) return;
		if (plan === null) {
			// Not playable from here after all (a disk gone, a vault shut): the cell finds its own.
			await this.restart();
			return;
		}
		this.#history.push(file);
		this.#historyLength = this.#history.length;
		// Set unconditionally, so a clip that had only just begun is not a special case.
		this.resumeAt = at > 0 ? at : null;
		this.#show(file, plan);
	}

	/**
	 * Take up a saved cell on a file known only by its id, asked of the server so the name is
	 * today's and Hidden is obeyed. A file gone or hidden leaves the cell to find its own.
	 */
	async resumeOn(saved: SavedCell, id: string, at = 0): Promise<void> {
		this.#take(saved);
		const era = ++this.#era;
		// Out of `empty` now, or the wall's opening draw fills this cell too (`Wall.open`).
		this.state = 'loading';
		const file = await keptFile(id);
		if (era !== this.#era) return;
		await (file === null ? this.restart() : this.startOn(file, at));
	}

	/** The fields a saved cell carries, checked and taken. What starts playing is the caller's. */
	#take(saved: SavedCell): void {
		// Whatever was waiting was about the source this cell is being pointed away from.
		this.#pending = null;
		this.source = saved.source;
		this.mediaKind = isMediaKind(saved.media_kind) ? saved.media_kind : 'video_gif';
		// `ordering` is not read: it says what `sort` says, and `sort` is the one that is taken.
		this.endBehaviour = isLoopMode(saved.end_behaviour) ? saved.end_behaviour : 'loop_all';
		this.timerSeconds = saved.timer_seconds ?? null;
		/* The level is not taken from a saved wall (see `volume`), though the field is still sent:
		   it is one number on the account. `sort` is passed through, since the server validates it
		   and a later version's mode is better kept than dropped, and goes through `orderBy` so a
		   wall put away in Random gets a fresh shuffle. */
		this.orderBy(typeof saved.sort === 'string' && saved.sort ? saved.sort : null);
		/* Checked, unlike `sort`, because a shape is drawn only here and an unknown one falls back
		   to the dynamic shape. */
		this.aspect = isAspect(saved.aspect) ? saved.aspect : DEFAULT_ASPECT;
	}

	/**
	 * Point the cell at something else, and start again from the beginning of it.
	 *
	 * The run is thrown away, so no file from the old source plays as if the filter had failed. A
	 * new run is a new draw, so a Random cell's seed is minted again; otherwise every restart would
	 * open the same permutation on the same file. `orderBy` mints too, for a reorder without a
	 * restart. `avoiding` is a file not to open on, a preference as `/assets/random` reads it.
	 */
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

	/** SOMEWHERE ELSE out of this cell's source, never the file on screen: the casino control. */
	async somethingElse(): Promise<void> {
		await this.restart(this.playing?.id ?? null);
	}

	/** Take one file out of a fresh run, if anything else is left: a seeded shuffle keeps it out. */
	#leaveOut(id: string | null): void {
		if (id === null) return;
		const rest = this.#queue.filter((file) => file.id !== id);
		if (rest.length > 0 || this.#more) this.#queue = rest;
	}

	/**
	 * CHANGE WHAT COMES NEXT, WITHOUT CHANGING WHAT IS ON SCREEN.
	 *
	 * The order is a fact about the run, so only the fetched remainder is rebuilt; the file on
	 * screen, its marks and its history stay, and the change shows at the next advance. `casino`
	 * is the control that changes the file. The era is bumped so an advance in flight cannot land.
	 */
	async reorder(): Promise<void> {
		this.#era += 1;
		this.#queue = [];
		/* The held answer came out of the old run, so it goes with it. */
		this.#ahead = null;
		this.#offset = 0;
		this.#last = null;
		this.#more = true;
		await this.#fill();
	}

	/**
	 * Let go of whatever is on screen, and stop: when the wall is emptied and when the vault shuts.
	 * The run goes too, because its ids may be ones this account can no longer ask for.
	 */
	release(state: CellState = 'empty'): void {
		this.#era += 1;
		this.#queue = [];
		this.#ahead = null;
		/* A filter waiting on a file no longer on screen was a choice about something else. */
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

	/** Step to the next file, filling the run first if it is running low. */
	async advance(): Promise<void> {
		const era = this.#era;
		/* A filter chosen while this played is a new run, applied instead of stepping. */
		if (this.#pending !== null) {
			await this.#applyPending();
			return;
		}

		/* A lookahead still on the wire is this advance's answer, so it is awaited (`#looking`). */
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

		/*
		 * The run has ended and this cell PLAYS THROUGH, so it starts again from the top: a fresh
		 * seed when shuffled, a different order for a one-page source (`anotherRound`). Only when
		 * something has played, so a query matching nothing does not ask for ever.
		 */
		if (next === null && this.endBehaviour === 'loop_all' && this.#history.length > 0) {
			const before = this.#round;
			const shown = this.#shownRound;
			this.#shownRound = [];
			this.#offset = 0;
			this.#last = null;
			this.#more = true;
			if (this.sort === RANDOM) this.seed = mintSeed();
			await this.#fill();
			if (era !== this.#era) return;
			// A fresh seed over a source of a few files can deal the round just played again.
			if (this.sort === RANDOM && before !== null && !this.#more) {
				const ended = this.#history.at(-1)?.id ?? null;
				this.#queue = anotherRound(shown, this.#queue, ended, this.#passed, (ids) =>
					showingOrder(ids, (id) => this.#direct.get(id) !== false, DIRECT_TRIES, ended)
				);
				this.#round = this.#queue.map((one) => one.id);
			}
			next = await this.#nextPlayable();
			if (era !== this.#era) return;
		}

		if (next === null) {
			// A cell that showed something reached the end of its source; one that never did
			// matches nothing this account may see. Two different sentences.
			this.state = this.#history.length ? 'stopped' : 'nothing_here';
			this.unreachable = this.#missed === 'unreached';
			this.playing = null;
			this.plan = null;
			return;
		}

		this.#remember(next.file);
		this.#show(next.file, next.plan);
	}

	/** Note a file stepped over, for the next round's order (`#passed`); only on a one-page round. */
	#stepOver(id: string): void {
		if (this.#round !== null) this.#passed.add(id);
	}

	/** Note a file stepped over: out of Sift's reach, or for any other reason. */
	#note(unreached: boolean): void {
		if (this.#missed !== 'other') this.#missed = unreached ? 'unreached' : 'other';
	}

	/** Put a file at the end of what this cell has shown, keeping the last few of them. */
	#remember(file: Playable): void {
		if (this.#round !== null) this.#shownRound.push(file.id);
		this.#history.push(file);
		if (this.#history.length > HISTORY) this.#history.shift();
		this.#historyLength = this.#history.length;
	}

	/**
	 * FIND THE NEXT FILE NOW, WHILE THERE IS TIME (`#ahead`). Touches nothing on screen: a failure
	 * or a source change leaves the advance to do the work, so nothing has to await it.
	 */
	#lookAhead(): void {
		const era = this.#era;
		if (this.#looking !== null || this.#ahead !== null) return;
		const done = this.#find(era);
		this.#looking = { era, done };
		void done.finally(() => {
			if (this.#looking?.done === done) this.#looking = null;
		});
	}

	/** The search itself. Everything it can go wrong on leaves the cell exactly as it was. */
	async #find(era: number): Promise<void> {
		try {
			if (this.#queue.length <= REFILL_AT && this.#more) await this.#fill();
			if (era !== this.#era) return;
			const next = await this.#nextPlayable();
			if (era !== this.#era || next === null) return;
			this.#ahead = { era, file: next.file, plan: next.plan };
		} catch {
			// The advance asks for itself; a failed early lookup must not stop the cell.
		}
	}

	/** Step back to what was on screen before this. */
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

	/**
	 * The browser could not play what this cell handed it: the end-behaviour follows, as for a
	 * file ending, and after a few in a row the cell holds still on the message.
	 */
	async failed(reason: string | null = null): Promise<void> {
		this.#failures += 1;
		/* A reason better than "could not be played" (a stall, `$lib/theater/stall`), shown if this
		   is the failure the cell stops on. */
		if (reason !== null) this.problem = reason;
		this.state = 'failed';
		if (this.endBehaviour === 'once' || this.#failures >= GIVE_UP_AFTER) return;
		await this.advance();
	}

	/** The file has ended. What happens next is this cell's answer, not the element's. */
	async ended(): Promise<void> {
		/* A filter chosen while this played wins over all three end behaviours. */
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

	/**
	 * Something is actually playing here: the only honest end to a run of failures, since a file
	 * shown and then failing to decode is one of them.
	 */
	started(): void {
		this.#failures = 0;
		/* What comes after this one, found on the element starting, not the handover, so the
		   opening draw (`Wall.open`) has no nine cells asking together. Repeats return immediately. */
		void this.#lookAhead();
	}

	#show(file: Playable, plan: PlaybackPlan): void {
		this.problem = null;
		// The marks, the rung and the facts belong to the file they were set on.
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

	/**
	 * Fetch what only the detail knows about this file: its scrub strip and what it is made of.
	 * Only for the file that arrived, never for the whole run; a failure leaves the file playing.
	 */
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
	 * The next file this cell can actually play, with the plan for it.
	 *
	 * Direct play is preferred, not required: the install converts one file at a time, so a few
	 * candidates are asked about and then the first of them is taken. Candidates passed over go
	 * back to the front of the run in order, so a preference reorders a run and never shortens it
	 * (`showingOrder` predicts it). The file on screen is held back like one another cell shows.
	 */
	async #nextPlayable(): Promise<{ file: Playable; plan: PlaybackPlan } | null> {
		const era = this.#era;
		let chosen: { file: Playable; plan: PlaybackPlan } | null = null;
		let fallback: { file: Playable; plan: PlaybackPlan } | null = null;
		const taken = new Set(this.elsewhere());
		if (this.playing !== null) taken.add(this.playing.id);
		/* The first file another cell already holds, played only when nothing else can be. */
		let held: Playable | null = null;
		/* Every file looked at that could have been shown, in run order: what is not chosen goes back. */
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

		chosen ??= fallback;
		if (chosen === null && held !== null) {
			const plan = await this.#planOr(held.id).catch(() => null);
			if (era !== this.#era) return null;
			if (plan !== null && plan.route !== 'unread') chosen = { file: held, plan };
		}
		// Nothing to show: the run is spent, and the caller's wrap needs to find it empty.
		if (chosen === null) return null;
		const picked = chosen.file;
		this.#queue.unshift(...looked.filter((file) => file !== picked));
		return chosen;
	}

	/**
	 * What the server says about playing this file here; null when the file is gone.
	 *
	 * Only a 404 is "gone" (left the library, or not this account's). Any other failure is the ask
	 * failing, so it is made once more and then thrown for the caller to judge.
	 */
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

	/**
	 * Ask the source for another page, and add what comes back to the run, in the order it came.
	 *
	 * A shuffled cell's page is the next page of its seeded shuffle of the WHOLE source, so nothing
	 * is shuffled here. A seekable order continues after `#last`, as the wall does (`continuable`).
	 */
	async #fill(): Promise<void> {
		const era = this.#era;
		if (this.#filling === era || !this.#more) return;
		this.#filling = era;
		const opening = this.#offset === 0 && this.#last === null;
		// Unknown until this page says otherwise, so a failed page cannot leave an older round here.
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
			// The whole round is known only when its first page was all of it.
			if (opening) {
				this.#round = this.#more ? null : page.items.map((one) => one.id);
				// What was stepped over is kept only for a round that is this whole page.
				const round = new Set(this.#round);
				for (const id of this.#passed) if (!round.has(id)) this.#passed.delete(id);
				for (const id of this.#direct.keys()) if (!round.has(id)) this.#direct.delete(id);
			}
		} catch {
			// The run keeps what it had and asks again on the next advance.
			this.#more = false;
		} finally {
			// Only this era's flag: an older fill must not clear the one that replaced it.
			if (this.#filling === era) this.#filling = null;
		}
	}
}
