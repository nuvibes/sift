/* The wall: which shape it is in, what is in each cell, and the few things that are true of all of
 * them at once.
 *
 * A cell runs itself. The wall owns the shape, the two controls that reach every cell (silence,
 * stop), which cell has the focus, and the one media session a page is given.
 */

import { api } from '$lib/api/client';
import { TheaterSession, type WallFacts } from './session';
/* WHY NOT FOLLOWED: the Theater screen re-reads on `settingChanges` by calling `open` again, since
   only it knows which wall is its own. */
import { fetchSettingValues, onSettingsSaved } from '$lib/settings-ui/settings';
import { vault } from '$lib/shell/vault.svelte';
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
/* Which of Center Stage's two modes this account is in: `pick`, where a preview comes up when it is
   pressed, or `newest`, where a preview that starts something new comes up on its own. */
const CENTER_STAGE_KEY = 'theater.center_stage';
/* Whether leaving Theater keeps the wall for the way back. See `KeptWall`. */
const RESUME_KEY = 'theater.resume';

/*
 * How loud the ordinary player is set to, which is what a cell opens at: the same preference, not
 * a second one. Read with the wall's one preferences round trip and handed to `loudness`, and
 * applied while a cell is still muted, since it is what the sound comes back at.
 */
const PLAYER_VOLUME_KEY = 'playback.volume';

/** How long the marks on the audible cell stay lit after the sound last moved. */
export const AUDIBLE_MARK_MS = 4000;

/**
 * A wall put away when Theater was left, taken up again on the way back. In memory only: a restart
 * or a reload begins a new wall. The saved-wall shape (`preset`), plus each cell's file, place and
 * shuffle.
 */
interface KeptWall {
	preset: { layout: string; shape: StoredShape; strip: number; cells: SavedCell[] };
	playing: ({ file: Playable; at: number; seed: number | null } | null)[];
	focused: number;
	/* Whether the vault was open. A wall kept over an open vault is not taken up once it has shut. */
	vaultOpen: boolean;
}

export class Wall {
	/**
	 * The wall's shape: a grid, and where each cell sits in it. A shape rather than a layout's name,
	 * because a custom saved shape and the shapes `remove` heals into match no layout (`layout`).
	 */
	shape = $state<Shape>(OPENING);

	/**
	 * How many PREVIEWS are in the strip under the wall. Zero means there is no strip.
	 *
	 * Center Stage is a wall with one. Beside the shape, not in the grid, whose columns are sized by
	 * their pictures. The strip's cells are ordinary cells at the end of the one list, so every verb
	 * reaches them.
	 */
	strip = $state(0);

	/** Whether this wall has a strip at all, which is what Center Stage IS. */
	get centerStage(): boolean {
		return this.strip > 0;
	}

	/**
	 * Every cell the wall has ever needed, kept past a layout change, so shrinking and growing back
	 * keeps the sources somebody set. A cell not drawn holds no element and no socket.
	 */
	readonly all: Cell[] = Array.from({ length: MOST_CELLS }, () => new Cell());

	constructor() {
		for (const cell of this.all) cell.elsewhere = () => this.#heldElsewhere(cell);
	}

	/**
	 * What the drawn cells other than this one are showing, or have lined up next: only the wall
	 * knows which cells are drawn. Read as a cell chooses, so a file is not on screen twice.
	 */
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
	/** Stop everything, including every timer. */
	paused = $state(true);
	/** Which cell the keyboard is talking to. */
	focused = $state(0);

	/**
	 * Whether every cell is being talked to at once: the backtick beside the number keys.
	 *
	 * A SELECTION, not a second set of shortcuts, so every verb reaches the whole wall. `focused`
	 * stays underneath, since the bar still draws one timeline. Choosing one cell clears it.
	 */
	everyCell = $state(false);

	/**
	 * How many times a cell has been chosen from the keyboard, which is what the wall FLASHES on:
	 * an outline alone is too faint on a wall of nine. A counter, so a second press of the same key
	 * flashes again.
	 */
	chosenTimes = $state(0);

	/**
	 * Which cell somebody is pointing at from the bar, or none, so a number on the bar marks its
	 * rectangle with the aimed-at wash. On the wall because the bar and the cell are two components.
	 */
	aiming = $state<number | 'every' | null>(null);

	/** Whether this cell is the one being aimed at, or one of all of them. See `aiming`. */
	aimedAt(index: number): boolean {
		return this.aiming === 'every' || this.aiming === index;
	}
	/*
	 * Whether anybody is actually driving this from the keyboard.
	 *
	 * `focused` starts at the first cell, and its mark drawn on a wall nobody keyed reads as a
	 * scrollbar. The `:focus-visible` question asked by hand, since the keys are bound on the
	 * window: true once a key moves the focus, false at the next press.
	 */
	keyed = $state(false);
	/** Whether a wall starts playing when it is opened, from the account's own preference. */
	autoplay = $state(false);

	/**
	 * WHETHER THE WALL IS STILL WORKING OUT WHAT TO PUT IN ITS CELLS.
	 *
	 * So an opening cell does not say `Nothing chosen yet`, which is for a cell with genuinely
	 * nothing. On the wall, not the cells, because `#openSomewhere` and `#startMissing` find their
	 * work by which cells are still `empty`. Read only by `CellView`.
	 */
	opening = $state(false);

	/**
	 * Every cell the wall is drawing: the shape's places, and then the strip. One list, so a preview
	 * is numbered and addressed like any cell; `inFocus` and `previews` tell the halves apart.
	 */
	get cells(): Cell[] {
		return [...this.inFocus, ...this.previews];
	}

	/** How many cells are on screen at once: the shape's places plus the strip. */
	get drawn(): number {
		return this.shape.slots.length + this.strip;
	}

	/** The cells in the shape's places: the big ones, from the front of the list. */
	get inFocus(): Cell[] {
		return this.all.slice(0, this.shape.slots.length);
	}

	/**
	 * The cells in the strip, small, under the wall: taken from the END of the list.
	 *
	 * The focus half grows from the front, so previews just behind it would be eaten by a bigger
	 * shape; from the end, a preview keeps its source, sound and run. Four and five is nine, the list.
	 */
	get previews(): Cell[] {
		return this.strip === 0 ? [] : this.all.slice(MOST_CELLS - this.strip);
	}

	/**
	 * The cell a NUMBER means: the wall's places first, then the strip. Everything a person
	 * addresses is a position in `cells`, not a place in the list of nine.
	 */
	at(position: number): Cell | undefined {
		return this.cells[position];
	}

	/** Whether this position is one of the previews rather than one of the feeds in focus. */
	isPreview(position: number): boolean {
		return position >= this.shape.slots.length && position < this.drawn;
	}

	/** The name of the shape the wall is in, when it is one Sift ships, and null when it is built. */
	get layout(): LayoutId | null {
		return named(this.shape, this.strip);
	}

	/**
	 * The cell whose sound is the one being heard, or null while everything is silent: the one the
	 * sound last MOVED to, which the marks and the operating system follow.
	 */
	audible = $state<number | null>(null);

	/** When the sound last changed, so the marks on the audible cell can fade after a while. */
	soundMovedAt = $state(0);

	/* --- the two controls that reach every cell ----------------------------------------------- */

	/**
	 * Silence everything, or let it back. Only ever from a press: a browser pauses an element
	 * unmuted without an interaction.
	 */
	toggleMaster(): void {
		this.masterMuted = !this.masterMuted;
		/*
		 * LETTING IT BACK UNMUTES EVERY CELL, which is what "unmute all" means.
		 *
		 * Every cell starts muted on its own, so touching only the wall's flag would leave silence.
		 * Keeping some cells' own mutes would make one control do two things on state nobody sees.
		 * Silencing writes nothing into the cells, as `togglePause` does not.
		 */
		if (!this.masterMuted) {
			// The operating system is told about one: the cell the keyboard is on.
			this.audible ??= this.focused;
			this.all.forEach((cell) => (cell.muted = false));
		}
		this.#soundMoved();
	}

	/**
	 * Stop everything, or start everything.
	 *
	 * Starting clears each cell's own hold, or a cell stopped by hand would stay still under "Pause
	 * everything". Stopping does not set them, so which cell somebody stopped survives.
	 */
	togglePause(): void {
		this.paused = !this.paused;
		if (!this.paused) this.all.forEach((cell) => (cell.paused = false));
	}

	/** Unmute one cell and silence the rest. Must be called from the press itself. */
	solo(index: number): void {
		this.masterMuted = false;
		/* By the cell, not its place in the list of nine (`at`). */
		const heard = this.at(index);
		this.all.forEach((cell) => (cell.muted = cell !== heard));
		this.audible = index;
		this.#soundMoved();
	}

	/** Mute or unmute one cell, leaving the others where they are. From the press itself. */
	toggleMute(index: number): void {
		const cell = this.at(index);
		if (cell === undefined) return;
		this.setMuted([index], !cell.muted);
	}

	/**
	 * Mute or unmute several cells, all to the same answer. From the press itself. One answer, since
	 * flipping each would leave a mixed wall mixed.
	 */
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
		// Silencing hands the sound to whatever is left unmuted; letting it back points it at the
		// last cell named, which is certainly audible.
		this.audible = muted ? this.#stillAudible(last) : last;
		this.#soundMoved();
	}

	/**
	 * WHICH CELLS AN UNMUTE LETS BACK, silencing every other cell where it has to.
	 *
	 * On a silenced wall, lifting the wall's silence for the named cells would also let back every
	 * cell whose own sound was on underneath, so the others are muted on their own first.
	 */
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

	/**
	 * Bring a cell forward, and take the sound with it, so somebody never looks at one cell while
	 * hearing another. A press, which is what allows the unmute.
	 */
	promote(index: number): void {
		this.focused = index;
		// One cell, named, so the wall comes off "every cell" as anywhere else that names one.
		this.everyCell = false;
		this.solo(index);
	}

	/** Move the keyboard to a cell without touching the sound. */
	focus(index: number): void {
		if (index < 0 || index >= this.cells.length) return;
		this.focused = index;
		this.everyCell = false;
		this.keyed = true;
	}

	/**
	 * Choose a cell BY ITS NUMBER: the number keys, and the numbers on the bar. The flash is here,
	 * not in `focus`, because a digit, unlike a pointed press, has nothing linking it to the wall.
	 */
	chooseByNumber(index: number): void {
		if (index < 0 || index >= this.cells.length) return;
		this.focus(index);
		this.chosenTimes += 1;
	}

	/** Talk to every cell at once. See `everyCell`. */
	focusEvery(): void {
		this.everyCell = true;
		this.keyed = true;
		this.chosenTimes += 1;
	}

	/**
	 * The cells a control acts on: the chosen one, or all of them, given once for every verb.
	 * Anything that DRAWS one cell's state reads `focused`, since a wall has no single playhead.
	 */
	get addressedAt(): number[] {
		if (this.everyCell) return this.cells.map((_, at) => at);
		const one = Math.min(this.focused, this.cells.length - 1);
		return one < 0 ? [] : [one];
	}

	/** The same, as the cells themselves. */
	get addressed(): Cell[] {
		return this.addressedAt.flatMap((at) => this.at(at) ?? []);
	}

	/** Something was pressed, so the keyboard is not what is driving any more. */
	unkey(): void {
		this.keyed = false;
	}

	/** Whether this cell's sound is actually reaching anybody. */
	isAudible(index: number): boolean {
		return !this.masterMuted && this.at(index)?.muted === false;
	}

	/**
	 * The cell whose sound is really being heard, or null when none is. Unlike `audible`, which
	 * stays put through a master mute so the sound returns to it, this is what the operating system
	 * is told, so the lock screen never names a clip on a silenced wall.
	 */
	get heard(): number | null {
		return this.audible !== null && this.isAudible(this.audible) ? this.audible : null;
	}

	#stillAudible(besides: number): number | null {
		// Over what is DRAWN, and by position: a cell nobody can see is not where the sound went.
		const found = this.cells.findIndex((cell, at) => at !== besides && !cell.muted);
		return found === -1 ? null : found;
	}

	#soundMoved(): void {
		this.soundMovedAt = Date.now();
	}

	/* --- the shape ---------------------------------------------------------------------------- */

	/**
	 * Change shape, keeping every cell that survives it. One that falls off is released, so it
	 * holds no run of ids, which after a vault shuts it should not.
	 */
	setLayout(next: LayoutId): void {
		const chosen = layoutNamed(next);
		/* The layout decides the strip, including none, or Grid chosen from Center stage would keep
		   five previews. Set before `reshape`, which reads it. */
		this.strip = chosen.strip ?? 0;
		this.reshape(chosen.shape);
	}

	/**
	 * Take a shape, releasing whatever no longer has a place: the one door every shape change goes
	 * through, so no hidden cell keeps a run of ids.
	 */
	reshape(next: Shape): void {
		this.#shaped = true;
		/* The focus half is the front and the strip the back, so a smaller shape releases the
		   middle; releasing everything past the shape would let go of every preview. */
		const wanted = next.slots.length;
		this.all.slice(wanted, MOST_CELLS - this.strip).forEach((cell) => cell.release());
		this.shape = next;
		if (this.focused >= this.drawn) this.focused = this.drawn - 1;
		if (this.audible !== null && this.audible >= this.drawn) this.audible = null;
		void this.#startMissing();
	}

	/**
	 * Another feed on the wall: the next shape up the ladder, or a preview once it is at the top.
	 *
	 * The four predefined shapes in order, because free-form growth reaches shapes nobody chose with
	 * no way out. No arguments: the new cell goes at the end whichever cell was pressed.
	 */
	spawn(): void {
		const next = this.canGrow ? grown(this.shape) : null;
		if (next === null) {
			this.addPreview();
			return;
		}
		this.reshape(next);
	}

	/**
	 * Another feed exactly like this one, in the place immediately after it.
	 *
	 * The copy is handed `saved` and the clip on screen (`takeOver`, not `adopt`, which would pick a
	 * fresh file), from its beginning, since the copy is a new element. The ladder's next shape adds
	 * its place at the END, so the spare cell is moved next to the original. At the top of the
	 * ladder the copy goes into the strip rather than a refusal. A cell still finding its first file
	 * has no clip, and `takeOver` restarts it.
	 */
	duplicate(index: number): void {
		const source = this.all[index];
		/* The shape's places only: a preview has no slot to open after. */
		if (source === undefined || index >= this.shape.slots.length) return;

		const next = this.canGrow ? grown(this.shape) : null;
		if (next === null) {
			/* The strip grows at its FRONT (`previews`), so the first preview is the new one. */
			if (!this.canPreview) return;
			this.addPreview();
			const preview = this.previews[0];
			if (preview === undefined) return;
			void preview.takeOver(source.saved, source.playing);
			return;
		}

		/*
		 * THE CELLS THEMSELVES MOVE ALONG, rather than their settings being copied.
		 *
		 * `adopt` would restart every later feed from a fresh file. A cell is drawn under its own
		 * key (`TheaterWall`), so moving the object keeps its element, stream and playhead. The
		 * spare at the end of the larger shape is empty and goes in beside the original.
		 */
		const spare = this.all[next.slots.length - 1];
		this.all.splice(next.slots.length - 1, 1);
		this.all.splice(index + 1, 0, spare);
		void spare.takeOver(source.saved, source.playing);
		this.reshape(next);
	}

	/**
	 * Take one feed off the wall, and let its cell go. The cells after it move up, so the sources
	 * somebody set follow their pictures.
	 */
	remove(index: number): void {
		const next = removeSlot(this.shape, index);
		if (next === this.shape) return;
		/*
		 * THE CELLS MOVE UP, rather than their settings being copied one place along, which through
		 * `adopt` would restart every later cell (and a copy whose original was closed). Moving the
		 * object keeps each element (`TheaterWall`); the cell taken off is released at the end of
		 * the focus half. No source changed, so nothing restarts.
		 */
		const going = this.all[index];
		const last = this.shape.slots.length - 1;
		this.all.splice(index, 1);
		this.all.splice(last, 0, going);
		going.release();
		this.reshape(next);
	}

	/* --- the strip, and what it is for --------------------------------------------------------- */

	/**
	 * Whether a preview that starts a new file takes the focus place by itself: the two modes of
	 * Center Stage, a wall you drive and a wall of the freshest. A press promotes in both.
	 */
	newestTakesFocus = $state(false);

	/*
	 * The cells just handed a source and expected to start something, by key since a swap moves
	 * what a number means. It stops `newest` ping-ponging a demoted feed straight back up.
	 */
	#settling = new Set<string>();

	/*
	 * What each cell was last seen playing, by key: an element announces a start on a seek and an
	 * unpause too, so only a changed file is news to `newest`.
	 */
	#showing = new Map<string, string | null>();

	/**
	 * Whether another preview can be added: whatever the focus half is not using. The ceiling is
	 * the wall's nine, not five previews, which is only what a Center stage layout opens with.
	 */
	get canPreview(): boolean {
		return this.drawn < MOST_CELLS;
	}

	/** Take the strip away, and let every preview go. The way out of Center Stage. */
	closeStrip(): void {
		this.previews.forEach((cell) => cell.release());
		this.strip = 0;
		if (this.focused >= this.drawn) this.focused = this.drawn - 1;
	}

	/** Put another preview in the strip. Does nothing at either ceiling. */
	addPreview(): void {
		if (!this.canPreview) return;
		this.strip += 1;
		void this.#startMissing();
	}

	/** Take one preview out of the strip; later ones slide up and keep their sources. */
	dropPreview(position: number): void {
		if (!this.isPreview(position)) return;
		/* The previews after it move along; the shorter strip then takes out its FRONT cell,
		   so that is the one released. */
		const previews = this.previews;
		const at = position - this.shape.slots.length;
		for (let step = at; step > 0; step -= 1) previews[step].adopt(previews[step - 1].saved);
		previews[0].release();
		this.strip -= 1;
		if (this.focused >= this.drawn) this.focused = this.drawn - 1;
	}

	/**
	 * Send what this cell is playing DOWN into the strip, leaving the cell where it is.
	 *
	 * Not a mirror of `sendToFocus`'s swap: a place given up would be a hole the wall would have to
	 * reshape around, so this COPIES, with `takeOver` so the strip shows the clip pressed. The new
	 * preview is settled first, or `newest` would bring it straight back up.
	 */
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

	/**
	 * Bring a preview up into the focus half, and send what was there down in its place.
	 *
	 * A SWAP of sources, not of cells: the strip and the grid are different parents, so a moved
	 * picture would rebuild its element anyway. It lands where the keyboard is, or the first place
	 * when the whole wall is addressed or the focus is a preview.
	 */
	sendToFocus(index: number): void {
		if (!this.isPreview(index)) return;
		const place = !this.everyCell && this.focused < this.shape.slots.length ? this.focused : 0;
		const preview = this.at(index);
		const big = this.at(place);
		if (preview === undefined || big === undefined) return;
		/* The file each has UP, not only its source, so the clip pressed is the one shown. */
		const wasInFocus = big.saved;
		const wasShowing = big.playing;
		const brought = preview.playing;
		/* Only the one going DOWN can promote itself back under `newest`; the one coming up is
		   in the focus half, which `previewStarted` ignores. */
		this.#settling.add(preview.key);
		// And where each had reached: the swap rebuilds both elements.
		void big.takeOver(preview.saved, brought, preview.position);
		void preview.takeOver(wasInFocus, wasShowing, big.position);
		this.focus(place);
		// The number key's flash, linking the press at the foot to the change above.
		this.chosenTimes += 1;
	}

	/**
	 * A preview has started playing something. In `newest` it takes the focus place. Told by the
	 * view on a real start, since a watcher would fire on the swap it caused.
	 */
	previewStarted(index: number, file: string | null): void {
		const cell = this.at(index);
		if (cell === undefined) return;
		const seen = this.#showing.get(cell.key);
		this.#showing.set(cell.key, file);
		if (this.#settling.delete(cell.key)) return;
		if (!this.newestTakesFocus || !this.isPreview(index)) return;
		/* A NEW FILE, not merely a start: an element announces on every unpause, seek and stall,
		   and promoting on those would move the selection off the cell being driven. */
		if (file === null || file === seen) return;
		this.sendToFocus(index);
	}

	/**
	 * Whether another feed can be added to the wall itself: four with a strip, nine without, since
	 * four is where a feed stops being big enough to watch. This, not the ladder, keeps a custom
	 * wall of more than four from growing into a place the strip draws.
	 */
	get canGrow(): boolean {
		const most = this.centerStage ? MOST_IN_FOCUS : MOST_CELLS;
		return this.shape.slots.length < most && this.drawn < MOST_CELLS;
	}

	/** Whether a feed can be taken off. The last one stays. See the shape's own file. */
	get canShrink(): boolean {
		return this.shape.slots.length > 1;
	}

	/* --- arriving and leaving ----------------------------------------------------------------- */

	/*
	 * Whether the shape has been set by anything at all yet, so `open` does not lay the stored
	 * layout over a shape somebody built while the preferences were in flight.
	 */
	#shaped = false;

	/*
	 * Whether this wall has already been opened once. The wall outlives the screen (the corner
	 * panel), so a second open re-reads preferences but not the arriving state: re-applying it
	 * would pause a wall taken out of the corner panel.
	 */
	#opened = false;

	/** Whether leaving Theater keeps this wall for the way back. The account's setting. */
	resumes = false;

	/** What this wall is showing, for taking up again later. See `KeptWall`. */
	get kept(): KeptWall {
		return {
			preset: this.preset,
			playing: this.cells.map((cell) =>
				cell.playing === null ? null : { file: cell.playing, at: cell.position, seed: cell.seed }
			),
			focused: this.focused,
			vaultOpen: vault.unlocked
		};
	}

	/**
	 * Take up a wall that was put away: the same shape, and each cell on the file it had up, from
	 * where it had reached. A cell that had nothing up finds its own, as a fresh one does.
	 */
	resume(kept: KeptWall): void {
		const shape = readShape(kept.preset.shape) ?? layoutNamed(kept.preset.layout).shape;
		const strip = Math.max(0, Math.min(kept.preset.strip, MOST_CELLS - shape.slots.length));
		// Nothing the layout preference says afterwards is to land on top of this. See `#shaped`.
		this.#shaped = true;
		this.shape = shape;
		this.strip = strip;
		const wanted = this.cells;
		this.focused = Math.min(Math.max(kept.focused, 0), wanted.length - 1);
		kept.preset.cells.slice(0, wanted.length).forEach((saved, at) => {
			const was = kept.playing[at] ?? null;
			const cell = wanted[at];
			void cell.takeOver(saved, was?.file ?? null, was?.at ?? 0);
			// The same shuffle carries on past the file on screen, rather than a fresh one.
			if (was !== null && was.seed !== null && cell.sort === RANDOM) cell.seed = was.seed;
		});
	}

	/** Read the account's preferences and fill the wall. */
	async open(): Promise<void> {
		const arriving = !this.#opened;
		this.#opened = true;
		// Before the preferences round trip, so no cell says "nothing chosen yet" through it.
		this.opening = true;
		try {
			const values = await fetchSettingValues();
			const stored = values.get(LAYOUT_KEY);
			// Only if nobody built a shape meanwhile (`#shaped`). The strip comes with it, or Center
			// Stage would open as a wall of one.
			if (typeof stored === 'string' && !this.#shaped) {
				const opening = layoutNamed(stored);
				this.shape = opening.shape;
				this.strip = opening.strip ?? 0;
			}
			this.autoplay = values.get(AUTOPLAY_KEY) === true;
			this.resumes = values.get(RESUME_KEY) === true;
			this.newestTakesFocus = values.get(CENTER_STAGE_KEY) === 'newest';

			/* How loud is the application's one level, held by `$lib/player/loudness`; the row is
			   handed there before anything is unmuted, never copied into the cells. */
			loudness.heard(values.get(PLAYER_VOLUME_KEY));
			const timer = Number(values.get(TIMER_KEY));
			if (Number.isFinite(timer) && timer > 0) {
				for (const cell of this.all) cell.timerSeconds = Math.round(timer);
			}
		} catch {
			// The wall opens on its shipped defaults.
		}
		// Muted either way: no browser grants sound to a page nobody has touched. Only on the way
		// IN (`#opened`).
		if (arriving) this.paused = !this.autoplay;
		try {
			await (arriving ? this.#openSomewhere() : this.#startMissing());
		} finally {
			// A draw that threw still ends the opening.
			this.opening = false;
		}
	}

	/* --- the record of sitting in front of it ---------------------------------------------------- */

	/** The session this wall is in while it is open, or none. See `TheaterSession`. */
	session: TheaterSession | null = null;
	/** The saved wall last loaded into this one, if any. */
	#savedWall: string | null = null;

	/** What this wall is, as a session's closing report records it. */
	get facts(): WallFacts {
		return {
			layout: this.layout ?? 'custom',
			cells: this.cells.length,
			arrangement: this.#savedWall,
			sources: this.cells.map((cell) => cell.saved.source)
		};
	}

	/** Begin a session, closing any before it. Called when the wall is made and when a page that
	 *  was put away comes back. */
	beginSession(): void {
		this.endSession();
		this.session = new TheaterSession();
		this.session.open();
	}

	/** End the session this wall is in, saying what the wall was. Does nothing when there is none. */
	endSession(): void {
		this.session?.close(this.facts);
		this.session = null;
	}

	/** Let every cell go: no element, no run, no socket. */
	close(): void {
		this.all.forEach((cell) => cell.release());
		this.audible = null;
	}

	/**
	 * The vault has been locked or unlocked, and what the cells may draw has changed with it. On a
	 * LOCK every drawn cell is emptied at once, since what is on screen and the run behind it may
	 * now be nobody's; then every cell fills again.
	 */
	vaultChanged(locked: boolean): void {
		for (const cell of this.cells) {
			if (locked) cell.release('loading');
			void cell.restart();
		}
	}

	async #startMissing(): Promise<void> {
		await Promise.all(this.cells.filter((cell) => cell.state === 'empty').map((c) => c.restart()));
	}

	/*
	 * ARRIVING FROM NOTHING: each cell opens somewhere else in the library.
	 *
	 * Without a draw every evening would open on the newest files. The draw is ONE page of a fresh
	 * seeded permutation per distinct filter, all asked at once: its rows cannot repeat each other,
	 * where a draw per cell would be serial and only avoid the one before. Each cell draws under its
	 * own filter, so nothing is drawn and thrown back. Cells with different filters may still meet
	 * on one file, a fact about the library. Only cells still EMPTY, so a loaded wall is as saved.
	 * `/assets/random` stays the player's, which has nothing to line a file up against.
	 */
	async #openSomewhere(): Promise<void> {
		const waiting = this.cells.filter((cell) => cell.state === 'empty');
		const drawn = await this.#drawOneEach(waiting);
		await Promise.all(
			waiting.map((cell, at) => {
				const file = drawn[at];
				// Nothing drawn (an empty set, or a filter the route cannot take): it finds its own.
				return file === null ? cell.restart() : cell.startOn(file);
			})
		);
	}

	/**
	 * One file for each cell, asked for ONCE PER DISTINCT FILTER. See `#openSomewhere`.
	 *
	 * `null` in a slot means that cell fills itself; the list matches the one handed in by position.
	 * The key is the filter's sorted parameters, so two spellings of one filter make one request. A
	 * cell whose `drawQuery` is null is in no group.
	 */
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
				// By position; a short page leaves the rest null, and those cells fill themselves.
				places.forEach((at, nth) => (out[at] = files[nth] ?? null));
			})
		);
		return out;
	}

	/**
	 * As many different files as are asked for, drawn at random out of the set this filter
	 * describes: the first `count` rows of a fresh seeded permutation. A failure is an empty list,
	 * and the cells fill themselves.
	 */
	async #someAtRandom(narrowing: Record<string, string>, count: number): Promise<Playable[]> {
		try {
			const page = await api.get<components['schemas']['AssetPageResponse']>('/assets', {
				query: {
					...narrowing,
					sort: RANDOM,
					seed: String(mintSeed()),
					limit: String(count)
				},
				/* The cell's own deadline (`PAGE_TIMEOUT_MS`). */
				signal: AbortSignal.timeout(PAGE_TIMEOUT_MS)
			});
			return page.items;
		} catch {
			return [];
		}
	}

	/* --- what a wall is when it is saved ------------------------------------------------------ */

	/*
	 * This wall as a preset is saved: the shape itself, and the layout's name, which older presets
	 * and readers know it by. A wall built by hand is stored under `custom`.
	 */
	get preset(): { layout: string; shape: StoredShape; strip: number; cells: SavedCell[] } {
		return {
			layout: this.layout ?? 'custom',
			shape: writeShape(this.shape),
			/* Beside the shape, not in the grid; the cells are the places then the strip. */
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
		/* Which saved wall this is now, for the session's record. */
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
		/* The places then the strip, as `arrangement` wrote them, so previews land at the END. */
		const wanted = this.cells;
		saved.cells.slice(0, wanted.length).forEach((one, at) => wanted[at].adopt(one));
	}
}

/**
 * Report what a cell gave one file, when it stops showing it.
 *
 * No position, so a wall shuffling through a hundred files leaves none of them offering to resume.
 * A named piece of the cell's sitting with the time already reported, so a sitting reported in two
 * pieces is one view, and marked as Theater's with its session.
 */
export async function countView(
	sitting: CellSitting,
	watchMs: number,
	session: string | null,
	inside: InsidePiece = {}
): Promise<void> {
	const before = sitting.reported;
	/* Moved before the request, since the next piece may be sent before this lands; put back on
	   failure if nothing was added since. */
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
				// What happened inside the cell's piece: the time at each speed, the time the screen
				// was filled, and the passes through the end (see `$lib/player/inside`).
				...inside
			},
			// The commonest way a wall ends is the window closing.
			keepalive: true
		});
	} catch {
		// A view that could not be counted is not worth interrupting a wall for.
		if (sitting.reported === (before ?? 0) + watchMs) sitting.reported = before;
	}
}

/* What each of a cell's two choices is called on screen; keyed by value, so a missing name is a
   compile error. */
export const MEDIA_KIND_LABELS: Record<MediaKind, string> = {
	video_gif: 'Video and GIF',
	all: 'Everything'
};

/*
 * The wall the screen is showing, reachable from the bar above it, which the layout draws and so
 * cannot be handed anything. Set by the screen when it mounts and given back when it goes.
 */
class Showing {
	/**
	 * The wall on screen, which OUTLIVES the screen so the corner panel can keep it running. Made on
	 * first use and let go when nothing draws it.
	 */
	wall = $state<Wall | null>(null);

	/* The wall put away on the way out, when the account keeps it. See `KeptWall`. */
	#kept: KeptWall | null = null;

	/**
	 * The wall, made if there is not one yet, and the one put away taken up again if there is.
	 * Making one begins its session; the page going away ends it.
	 */
	ensure(): Wall {
		if (this.wall === null) {
			this.wall = new Wall();
			const kept = this.#kept;
			this.#kept = null;
			if (kept !== null && (vault.unlocked || !kept.vaultOpen)) this.wall.resume(kept);
			this.wall.beginSession();
			this.#listen();
		}
		return this.wall;
	}

	/**
	 * Let the wall go, unless something is still drawing it: whichever of the screen and the panel
	 * is last releases it.
	 */
	drop(stillDrawn: boolean): void {
		if (stillDrawn || this.wall === null) return;
		this.#kept = this.wall.resumes ? this.wall.kept : null;
		this.wall.endSession();
		this.wall.close();
		this.wall = null;
	}

	/** The setting moved. Turned off, whatever was put away goes. */
	resumeChanged(on: boolean): void {
		if (this.wall !== null) this.wall.resumes = on;
		if (!on) this.#kept = null;
	}

	#listening = false;

	/*
	 * The page going away ends the session; one brought back from the back-forward cache begins a
	 * new one. Listened to once, since the handlers read whichever wall there is.
	 */
	#listen(): void {
		if (this.#listening || typeof window === 'undefined') return;
		this.#listening = true;
		window.addEventListener('pagehide', () => this.wall?.endSession());
		window.addEventListener('pageshow', (event) => {
			if (event.persisted) this.wall?.beginSession();
		});
	}
}

export const showing = new Showing();

onSettingsSaved((saved) => {
	if (RESUME_KEY in saved) showing.resumeChanged(saved[RESUME_KEY] === true);
});
