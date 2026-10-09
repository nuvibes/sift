/* The wall: its shape, the controls that reach every cell, which cell has the focus, and the one
 * media session a page is given. A cell runs itself. */

import { api } from '$lib/api/client';
import { TheaterSession, type WallFacts } from './session';
/* WHY NOT FOLLOWED: the Theater screen re-reads on `settingChanges` by calling `open` again, since
   only it knows which wall is its own. */
import { fetchSettingValues, onSettingsSaved } from '$lib/settings-ui/settings';
import { vault } from '$lib/shell/vault.svelte';
import { session as signedIn } from '$lib/shell/session.svelte';
import { storeWall, takeStoredWall, type KeptWall } from './kept';
import { loudness } from '$lib/player/loudness.svelte';
import type { InsidePiece } from '$lib/player/inside';
import {
	Cell,
	PAGE_TIMEOUT_MS,
	type CellSitting,
	type MediaKind,
	type Ordering,
	type Playable,
	type SavedCell
} from './cell.svelte';
import { mintSeed, RANDOM } from '$lib/grid/sort-state.svelte';
import type { components } from '$lib/api/schema';
import {
	layout as layoutNamed,
	named,
	OPENING,
	readShape,
	writeShape,
	type StoredShape,
	remove as removeSlot,
	grown,
	MOST_CELLS,
	MOST_IN_FOCUS,
	type LayoutId,
	type Shape
} from './layouts';

const LAYOUT_KEY = 'theater.layout';
const AUTOPLAY_KEY = 'theater.autoplay';
const TIMER_KEY = 'theater.timer_seconds';
/* Center Stage's two modes: `pick`, a preview comes up on a double press, or `newest`. */
const CENTER_STAGE_KEY = 'theater.center_stage';
const RESUME_KEY = 'theater.resume';

/* The ordinary player's volume, which a cell opens at: one preference, not a second. */
const PLAYER_VOLUME_KEY = 'playback.volume';

export const AUDIBLE_MARK_MS = 4000;

export class Wall {
	/** A shape rather than a layout's name: custom and healed shapes match no layout. */
	shape = $state<Shape>(OPENING);

	/** Previews in the strip under the wall, ordinary cells at the end of the one list. */
	strip = $state(0);

	get centerStage(): boolean {
		return this.strip > 0;
	}

	/** Every cell ever needed, kept past a layout change so sources survive shrinking. */
	readonly all: Cell[] = Array.from({ length: MOST_CELLS }, () => new Cell());

	constructor() {
		for (const cell of this.all) cell.elsewhere = () => this.#heldElsewhere(cell);
	}

	/** What the other drawn cells show or have next, so a file is not on screen twice. */
	#heldElsewhere(asking: Cell): Set<string> {
		const held = new Set<string>();
		for (const cell of this.cells) {
			if (cell === asking) continue;
			if (cell.playing !== null) held.add(cell.playing.id);
			if (cell.nextUp !== null) held.add(cell.nextUp);
		}
		return held;
	}

	/** Silence everything, without forgetting what each cell was set to on its own. */
	masterMuted = $state(true);
	paused = $state(true);
	focused = $state(0);

	/** Every cell addressed together (the backtick), so every verb reaches the whole wall. */
	everyCell = $state(false);

	/** A counter the wall flashes on, so a second press of the same key flashes again. */
	chosenTimes = $state(0);

	/** The cell pointed at from the bar, so its rectangle gets the aimed-at wash. */
	aiming = $state<number | 'every' | null>(null);

	aimedAt(index: number): boolean {
		return this.aiming === 'every' || this.aiming === index;
	}
	/* The `:focus-visible` question asked by hand, since the keys are bound on the window. */
	keyed = $state(false);
	autoplay = $state(false);

	/** Still choosing what to put in its cells, so a cell does not say `Nothing chosen yet`. */
	opening = $state(false);

	/** Every cell drawn: the shape's places, then the strip, numbered as one list. */
	get cells(): Cell[] {
		return [...this.inFocus, ...this.previews];
	}

	get drawn(): number {
		return this.shape.slots.length + this.strip;
	}

	get inFocus(): Cell[] {
		return this.all.slice(0, this.shape.slots.length);
	}

	/** The strip's cells, from the END of the list, so a growing shape does not eat a preview. */
	get previews(): Cell[] {
		return this.strip === 0 ? [] : this.all.slice(MOST_CELLS - this.strip);
	}

	/** The cell a number means: a position in `cells`, not a place in the list of nine. */
	at(position: number): Cell | undefined {
		return this.cells[position];
	}

	isPreview(position: number): boolean {
		return position >= this.shape.slots.length && position < this.drawn;
	}

	get layout(): LayoutId | null {
		return named(this.shape, this.strip);
	}

	/** The cell the sound last moved to, or null while everything is silent. */
	audible = $state<number | null>(null);

	soundMovedAt = $state(0);

	/** Silence everything, or let it back; only from a press, or the browser pauses it. */
	toggleMaster(): void {
		this.masterMuted = !this.masterMuted;
		// Letting it back unmutes every cell, since each starts muted; silencing writes nothing.
		if (!this.masterMuted) {
			this.audible ??= this.focused;
			this.all.forEach((cell) => (cell.muted = false));
		}
		this.#soundMoved();
	}

	/** Stop or start everything; starting clears each cell's own hold, stopping keeps them. */
	togglePause(): void {
		this.paused = !this.paused;
		if (!this.paused) this.all.forEach((cell) => (cell.paused = false));
	}

	/** Unmute one cell and silence the rest. Must be called from the press itself. */
	solo(index: number): void {
		this.masterMuted = false;
		const heard = this.at(index);
		this.all.forEach((cell) => (cell.muted = cell !== heard));
		this.audible = index;
		this.#soundMoved();
	}

	toggleMute(index: number): void {
		const cell = this.at(index);
		if (cell === undefined) return;
		this.setMuted([index], !cell.muted);
	}

	/** Mute or unmute several cells to one answer, from the press itself. */
	setMuted(indexes: number[], muted: boolean): void {
		if (!muted) indexes = this.#lettingBack(indexes);
		let last: number | null = null;
		for (const index of indexes) {
			const cell = this.at(index);
			if (cell === undefined) continue;
			cell.muted = muted;
			last = index;
		}
		if (last === null) return;
		if (!muted) this.masterMuted = false;
		// Silencing hands the sound to whatever is left unmuted.
		this.audible = muted ? this.#stillAudible(last) : last;
		this.#soundMoved();
	}

	/** Which cells an unmute lets back; on a silenced wall the others are muted first. */
	#lettingBack(indexes: number[]): number[] {
		const named = indexes.filter((index) => this.at(index) !== undefined);
		if (named.length === 0) return named;
		if (this.masterMuted) {
			const kept = new Set(named.map((index) => this.at(index)));
			this.all.forEach((cell) => {
				if (!kept.has(cell)) cell.muted = true;
			});
		}
		return named;
	}

	/** Bring a cell forward with the sound, from a press, which allows the unmute. */
	promote(index: number): void {
		this.focused = index;
		this.everyCell = false;
		this.solo(index);
	}

	#lastPlace = 0;

	focus(index: number): void {
		if (index < 0 || index >= this.cells.length) return;
		this.focused = index;
		if (index < this.shape.slots.length) this.#lastPlace = index;
		this.everyCell = false;
		this.keyed = true;
	}

	/** Choose a cell by its number; the flash ties a digit, which points at nothing, to it. */
	chooseByNumber(index: number): void {
		if (index < 0 || index >= this.cells.length) return;
		this.focus(index);
		this.chosenTimes += 1;
	}

	focusEvery(): void {
		this.everyCell = true;
		this.keyed = true;
		this.chosenTimes += 1;
	}

	/** The cells a control acts on: the chosen one, or all of them. */
	get addressedAt(): number[] {
		if (this.everyCell) return this.cells.map((_, at) => at);
		const one = Math.min(this.focused, this.cells.length - 1);
		return one < 0 ? [] : [one];
	}

	get addressed(): Cell[] {
		return this.addressedAt.flatMap((at) => this.at(at) ?? []);
	}

	unkey(): void {
		this.keyed = false;
	}

	isAudible(index: number): boolean {
		return !this.masterMuted && this.at(index)?.muted === false;
	}

	/** The cell really heard: unlike `audible`, null under a master mute, for the lock screen. */
	get heard(): number | null {
		return this.audible !== null && this.isAudible(this.audible) ? this.audible : null;
	}

	#stillAudible(besides: number): number | null {
		// Over what is drawn, and by position: a cell nobody can see is not where the sound went.
		const found = this.cells.findIndex((cell, at) => at !== besides && !cell.muted);
		return found === -1 ? null : found;
	}

	#soundMoved(): void {
		this.soundMovedAt = Date.now();
	}

	/** Change shape; a cell that falls off is released so it holds no run of ids. */
	setLayout(next: LayoutId): void {
		const chosen = layoutNamed(next);
		/* The layout decides the strip, including none. Set before `reshape`, which reads it. */
		this.strip = chosen.strip ?? 0;
		this.reshape(chosen.shape);
	}

	/** The one door every shape change goes through, releasing what has no place. */
	reshape(next: Shape): void {
		this.#shaped = true;
		/* A smaller shape releases the middle: the focus half is the front, the strip the back. */
		const wanted = next.slots.length;
		this.all.slice(wanted, MOST_CELLS - this.strip).forEach((cell) => cell.release());
		this.shape = next;
		if (this.focused >= this.drawn) this.focused = this.drawn - 1;
		if (this.audible !== null && this.audible >= this.drawn) this.audible = null;
		void this.#startMissing();
	}

	/** Another feed: the next shape up the ladder, or a preview once at the top. */
	spawn(): void {
		const next = this.canGrow ? grown(this.shape) : null;
		if (next === null) {
			this.addPreview();
			return;
		}
		this.reshape(next);
	}

	/**
	 * Another feed like this one, just after it, on the same clip (`takeOver`); at the top of the
	 * ladder it goes into the strip.
	 */
	duplicate(index: number): void {
		const source = this.all[index];
		if (source === undefined || index >= this.shape.slots.length) return;

		const next = this.canGrow ? grown(this.shape) : null;
		if (next === null) {
			/* The strip grows at its front (`previews`), so the first preview is the new one. */
			if (!this.canPreview) return;
			this.addPreview();
			const preview = this.previews[0];
			if (preview === undefined) return;
			void preview.takeOver(source.saved, source.playing);
			return;
		}

		/* The cells themselves move along, keeping each element; the spare goes beside. */
		const spare = this.all[next.slots.length - 1];
		this.all.splice(next.slots.length - 1, 1);
		this.all.splice(index + 1, 0, spare);
		void spare.takeOver(source.saved, source.playing);
		this.reshape(next);
	}

	/** Take one feed off; the cells after it move up, so sources follow their pictures. */
	remove(index: number): void {
		const next = removeSlot(this.shape, index);
		if (next === this.shape) return;
		/* Moved, not copied, so no later cell restarts; the one taken off is released last. */
		const going = this.all[index];
		const last = this.shape.slots.length - 1;
		this.all.splice(index, 1);
		this.all.splice(last, 0, going);
		going.release();
		this.reshape(next);
	}

	/** Whether a preview starting a new file takes the focus by itself: Center Stage's `newest`. */
	newestTakesFocus = $state(false);

	/* Cells just handed a source, by key, so `newest` does not ping-pong a demoted feed back up. */
	#settling = new Set<string>();

	/* Each cell's last file, by key: an element announces a start on a seek and an unpause too. */
	#showing = new Map<string, string | null>();

	/** Whether another preview fits: the wall's nine, not the five a layout opens with. */
	get canPreview(): boolean {
		return this.drawn < MOST_CELLS;
	}

	closeStrip(): void {
		this.previews.forEach((cell) => cell.release());
		this.strip = 0;
		if (this.focused >= this.drawn) this.focused = this.drawn - 1;
	}

	addPreview(): void {
		if (!this.canPreview) return;
		this.strip += 1;
		void this.#startMissing();
	}

	/** Take one preview out of the strip; later ones slide up and keep their sources. */
	dropPreview(position: number): void {
		if (!this.isPreview(position)) return;
		/* The shorter strip takes out its front cell, so that is the one released. */
		const previews = this.previews;
		const at = position - this.shape.slots.length;
		for (let step = at; step > 0; step -= 1) previews[step].adopt(previews[step - 1].saved);
		previews[0].release();
		this.strip -= 1;
		if (this.focused >= this.drawn) this.focused = this.drawn - 1;
	}

	/** Copy what this cell plays down into the strip, settled first so `newest` leaves it there. */
	sendToStrip(index: number): void {
		if (this.isPreview(index) || !this.canPreview) return;
		const source = this.at(index);
		if (source === undefined) return;
		this.addPreview();
		const preview = this.previews[0];
		if (preview === undefined) return;
		this.#settling.add(preview.key);
		void preview.takeOver(source.saved, source.playing);
	}

	/** Swap a preview's source with the last focus place's: the halves have different parents. */
	sendToFocus(index: number): void {
		if (!this.isPreview(index)) return;
		const places = this.shape.slots.length;
		const last = this.focused < places ? this.focused : this.#lastPlace;
		const place = !this.everyCell && last < places ? last : 0;
		const preview = this.at(index);
		const big = this.at(place);
		if (preview === undefined || big === undefined) return;
		const wasInFocus = big.saved;
		const wasShowing = big.playing;
		const brought = preview.playing;
		/* Only the one going down can promote itself back under `newest`. */
		this.#settling.add(preview.key);
		// And where each had reached: the swap rebuilds both elements.
		void big.takeOver(preview.saved, brought, preview.position);
		void preview.takeOver(wasInFocus, wasShowing, big.position);
		this.focus(place);
		this.chosenTimes += 1;
	}

	/** A preview started something; under `newest` it takes the focus. Told on a real start. */
	previewStarted(index: number, file: string | null): void {
		const cell = this.at(index);
		if (cell === undefined) return;
		const seen = this.#showing.get(cell.key);
		this.#showing.set(cell.key, file);
		if (this.#settling.delete(cell.key)) return;
		if (!this.newestTakesFocus || !this.isPreview(index)) return;
		/* A new file, not merely a start: an element announces every unpause, seek and stall. */
		if (file === null || file === seen) return;
		this.sendToFocus(index);
	}

	/** Whether the wall itself can grow: four with a strip, nine without. */
	get canGrow(): boolean {
		const most = this.centerStage ? MOST_IN_FOCUS : MOST_CELLS;
		return this.shape.slots.length < most && this.drawn < MOST_CELLS;
	}

	get canShrink(): boolean {
		return this.shape.slots.length > 1;
	}

	/* So `open` does not lay the stored layout over a shape built meanwhile. */
	#shaped = false;

	/* The wall outlives the screen: a second open re-reads preferences, not the arriving state. */
	#opened = false;

	resumes = false;

	get kept(): KeptWall {
		return {
			preset: this.preset,
			playing: this.cells.map((cell) =>
				cell.playing === null
					? null
					: { id: cell.playing.id, file: cell.playing, at: cell.position, seed: cell.seed }
			),
			focused: this.focused,
			vaultOpen: vault.unlocked
		};
	}

	/** Take up a wall put away: the same shape, each cell on its file from where it reached. */
	resume(kept: KeptWall): void {
		const shape = readShape(kept.preset.shape) ?? layoutNamed(kept.preset.layout).shape;
		const strip = Math.max(0, Math.min(kept.preset.strip, MOST_CELLS - shape.slots.length));
		this.#shaped = true;
		this.shape = shape;
		this.strip = strip;
		const wanted = this.cells;
		this.focused = Math.min(Math.max(kept.focused, 0), wanted.length - 1);
		kept.preset.cells.slice(0, wanted.length).forEach((saved, at) => {
			const was = kept.playing[at] ?? null;
			const cell = wanted[at];
			// A wall read back from the tab's storage has only ids, so each cell asks for its file.
			if (was !== null && was.file === undefined) void cell.resumeOn(saved, was.id, was.at);
			else void cell.takeOver(saved, was?.file ?? null, was?.at ?? 0);
			if (was !== null && was.seed !== null && cell.sort === RANDOM) cell.seed = was.seed;
		});
	}

	async open(): Promise<void> {
		const arriving = !this.#opened;
		this.#opened = true;
		// Before the preferences round trip, so no cell says "nothing chosen yet" through it.
		this.opening = true;
		try {
			const values = await fetchSettingValues();
			const stored = values.get(LAYOUT_KEY);
			// Only if nobody built a shape meanwhile; the strip comes with it.
			if (typeof stored === 'string' && !this.#shaped) {
				const opening = layoutNamed(stored);
				this.shape = opening.shape;
				this.strip = opening.strip ?? 0;
			}
			this.autoplay = values.get(AUTOPLAY_KEY) === true;
			this.resumes = values.get(RESUME_KEY) === true;
			this.newestTakesFocus = values.get(CENTER_STAGE_KEY) === 'newest';

			/* The level goes to `$lib/player/loudness` before anything unmutes, never copied. */
			loudness.heard(values.get(PLAYER_VOLUME_KEY));
			const timer = Number(values.get(TIMER_KEY));
			if (Number.isFinite(timer) && timer > 0) {
				for (const cell of this.all) cell.timerSeconds = Math.round(timer);
			}
		} catch {
			// The wall opens on its shipped defaults.
		}
		// Muted either way: no browser grants sound to a page nobody has touched.
		if (arriving) this.paused = !this.autoplay;
		try {
			await (arriving ? this.#openSomewhere() : this.#startMissing());
		} finally {
			this.opening = false;
		}
	}

	session: TheaterSession | null = null;
	#savedWall: string | null = null;

	get facts(): WallFacts {
		return {
			layout: this.layout ?? 'custom',
			cells: this.cells.length,
			arrangement: this.#savedWall,
			sources: this.cells.map((cell) => cell.saved.source)
		};
	}

	/** Begin a session, closing any before it; also when a page put away comes back. */
	beginSession(): void {
		this.endSession();
		this.session = new TheaterSession();
		this.session.open();
	}

	endSession(): void {
		this.session?.close(this.facts);
		this.session = null;
	}

	close(): void {
		this.all.forEach((cell) => cell.release());
		this.audible = null;
	}

	/** The vault locked or unlocked: on a lock every drawn cell empties now, then refills. */
	vaultChanged(locked: boolean): void {
		for (const cell of this.cells) {
			if (locked) cell.release('loading');
			void cell.restart();
		}
	}

	async #startMissing(): Promise<void> {
		await Promise.all(this.cells.filter((cell) => cell.state === 'empty').map((c) => c.restart()));
	}

	/* Arriving from nothing, each empty cell opens on one page of a fresh shuffle per filter. */
	async #openSomewhere(): Promise<void> {
		const waiting = this.cells.filter((cell) => cell.state === 'empty');
		const drawn = await this.#drawOneEach(waiting);
		await Promise.all(
			waiting.map((cell, at) => {
				const file = drawn[at];
				return file === null ? cell.restart() : cell.startOn(file);
			})
		);
	}

	/** One file per cell, asked once per distinct filter; null in a slot means it fills itself. */
	async #drawOneEach(cells: readonly Cell[]): Promise<(Playable | null)[]> {
		const out: (Playable | null)[] = cells.map(() => null);
		const groups = new Map<string, number[]>();
		cells.forEach((cell, at) => {
			const narrowing = cell.drawQuery;
			if (narrowing === null) return;
			const key = JSON.stringify(Object.entries(narrowing).sort());
			const already = groups.get(key);
			if (already) already.push(at);
			else groups.set(key, [at]);
		});
		await Promise.all(
			[...groups.values()].map(async (places) => {
				const narrowing = cells[places[0]].drawQuery;
				if (narrowing === null) return;
				const files = await this.#someAtRandom(narrowing, places.length);
				places.forEach((at, nth) => (out[at] = files[nth] ?? null));
			})
		);
		return out;
	}

	/** Different files, the first rows of a fresh seeded shuffle; [] on failure. */
	async #someAtRandom(narrowing: Record<string, string>, count: number): Promise<Playable[]> {
		try {
			const page = await api.get<components['schemas']['AssetPageResponse']>('/assets', {
				query: {
					...narrowing,
					sort: RANDOM,
					seed: String(mintSeed()),
					limit: String(count)
				},
				signal: AbortSignal.timeout(PAGE_TIMEOUT_MS)
			});
			return page.items;
		} catch {
			return [];
		}
	}

	/* This wall as a saved preset; a hand-built one is stored under `custom`. */
	get preset(): { layout: string; shape: StoredShape; strip: number; cells: SavedCell[] } {
		return {
			layout: this.layout ?? 'custom',
			shape: writeShape(this.shape),
			strip: this.strip,
			cells: this.cells.map((cell) => cell.saved)
		};
	}

	adopt(saved: {
		id?: string;
		layout: string;
		shape?: unknown;
		strip?: number;
		cells: SavedCell[];
	}): void {
		this.#savedWall = saved.id ?? null;
		// The stored shape when it can be drawn, otherwise the layout an older wall was saved as.
		const shape = readShape(saved.shape) ?? layoutNamed(saved.layout).shape;
		/* Clamped: a row another version wrote may hold a strip too big for the wall. */
		const strip = Math.max(0, Math.min(saved.strip ?? 0, MOST_CELLS - shape.slots.length));
		this.all.forEach((cell) => cell.release());
		this.shape = shape;
		this.strip = strip;
		this.focused = 0;
		this.everyCell = false;
		this.audible = null;
		const wanted = this.cells;
		saved.cells.slice(0, wanted.length).forEach((one, at) => wanted[at].adopt(one));
	}
}

/** Report what a cell gave one file: no position, and a sitting sent in pieces is one view. */
export async function countView(
	sitting: CellSitting,
	watchMs: number,
	session: string | null,
	inside: InsidePiece = {}
): Promise<void> {
	const before = sitting.reported;
	/* Moved before the request, since the next piece may be sent first; put back on failure. */
	sitting.reported = (before ?? 0) + watchMs;
	try {
		await api.post(`/assets/${sitting.file}/view`, {
			// `ended` is false: a wall cuts things short by design.
			body: {
				watch_ms: watchMs,
				already_reported_ms: before,
				position_ms: null,
				ended: false,
				sitting: sitting.id,
				screen: 'theater',
				theater_session: session,
				...inside
			},
			keepalive: true
		});
	} catch {
		// A view that could not be counted is not worth interrupting a wall for.
		if (sitting.reported === (before ?? 0) + watchMs) sitting.reported = before;
	}
}

export const MEDIA_KIND_LABELS: Record<MediaKind, string> = {
	video_gif: 'Video and GIF',
	all: 'Everything'
};

/* The wall on screen, reachable from the bar the layout draws. */
class Showing {
	/** Outlives the screen so the corner panel can keep it running. */
	wall = $state<Wall | null>(null);

	#kept: KeptWall | null = null;

	/** The wall, made if missing or taken up again from memory or the tab's storage. */
	ensure(): Wall {
		if (this.wall === null) {
			this.wall = new Wall();
			const account = signedIn.viewer?.id ?? null;
			const stored = account === null ? null : takeStoredWall(account);
			const kept = this.#kept ?? stored;
			this.#kept = null;
			if (kept !== null && (vault.unlocked || !kept.vaultOpen)) this.wall.resume(kept);
			this.wall.beginSession();
			this.#listen();
		}
		return this.wall;
	}

	/** Let the wall go, unless the screen or the panel still draws it. */
	drop(stillDrawn: boolean): void {
		if (stillDrawn || this.wall === null) return;
		this.#kept = this.wall.resumes ? this.wall.kept : null;
		this.#store(this.#kept);
		this.wall.endSession();
		this.wall.close();
		this.wall = null;
	}

	resumeChanged(on: boolean): void {
		if (this.wall !== null) this.wall.resumes = on;
		if (on) return;
		this.#kept = null;
		this.#store(null);
	}

	#store(kept: KeptWall | null): void {
		const account = signedIn.viewer?.id;
		if (account) storeWall(account, kept);
	}

	#listening = false;

	/* The page going away ends the session; one back from the back-forward cache begins one. */
	#listen(): void {
		if (this.#listening || typeof window === 'undefined') return;
		this.#listening = true;
		window.addEventListener('pagehide', () => {
			// A reload never runs the screen's teardown, so the wall is written down here.
			if (this.wall?.resumes) this.#store(this.wall.kept);
			this.wall?.endSession();
		});
		window.addEventListener('pageshow', (event) => {
			if (event.persisted) this.wall?.beginSession();
		});
	}
}

export const showing = new Showing();

onSettingsSaved((saved) => {
	if (RESUME_KEY in saved) showing.resumeChanged(saved[RESUME_KEY] === true);
});
