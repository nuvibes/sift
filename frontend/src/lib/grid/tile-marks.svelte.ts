/*
 * What a tile draws in its corners, and when.
 *
 * Seven marks, three answers each: always, only while the tile is under the pointer, never. Held
 * once for the whole app for the reason `appearance.svelte.ts` gives about its two: the grid, the
 * strip above it, every entity wall and the settings pane that changes them all read the same
 * answers, and a preference each of them fetched for itself would be answers that drift apart the
 * moment one is changed.
 *
 * The defaults below mirror the server's registration, and they have to be: an answer arriving
 * different from what the server says for an account that never chose would change every library
 * on upgrade, silently, and read as the update having broken the grid. A tile at rest shows only
 * the GIF word and the length; everything else waits for the pointer.
 *
 * ## Where the position and the glyph live, and why they are not here
 *
 * They are the tile's. This file knows a key and an answer; `Tile` knows which corner a mark sits
 * in, and the settings pane knows how to draw a picture of one. Putting a corner in a store shared
 * by both would be the layout described in two places.
 */

import { fetchSettingValues, onSettingsSaved, saveSettings } from '$lib/settings-ui/settings';

/** Drawn whenever the tile is. */
export const ALWAYS = 'always';
/** Drawn while the pointer is over the tile, or something inside it has keyboard focus. */
export const ON_HOVER = 'hover';
/** Not drawn. */
export const NEVER = 'never';

export type MarkAnswer = typeof ALWAYS | typeof ON_HOVER | typeof NEVER;

/* One name per mark, spelled out.
 *
 * Not built from a shared prefix, deliberately. The gate that refuses a setting no screen can reach
 * credits a prefix as well as a key, so a single `'appearance.tile.'` written anywhere in the client
 * would vouch for every mark including one nothing draws, which is the exact fault that gate
 * exists to catch, bought back for the sake of one shorter line. */
export const VIEWS_MARK = 'appearance.tile.views';
export const O_COUNT_MARK = 'appearance.tile.o_count';
export const PINNED_MARK = 'appearance.tile.pinned';
export const SHARING_MARK = 'appearance.tile.sharing';
export const HIDDEN_MARK = 'appearance.tile.hidden';
export const FAVORITE_MARK = 'appearance.tile.favorite';
export const RATING_MARK = 'appearance.tile.rating';
export const GIF_MARK = 'appearance.tile.gif';
export const DURATION_MARK = 'appearance.tile.duration';

/** Every mark, in the order they sit on a tile: along the top, then the bottom pair, then the clock. */
export const MARK_KEYS = [
	VIEWS_MARK,
	O_COUNT_MARK,
	PINNED_MARK,
	SHARING_MARK,
	HIDDEN_MARK,
	FAVORITE_MARK,
	RATING_MARK,
	GIF_MARK,
	DURATION_MARK
] as const;

const DEFAULTS: Record<string, MarkAnswer> = {
	[VIEWS_MARK]: ON_HOVER,
	[O_COUNT_MARK]: ON_HOVER,
	[PINNED_MARK]: ON_HOVER,
	[SHARING_MARK]: ON_HOVER,
	[HIDDEN_MARK]: ON_HOVER,
	[FAVORITE_MARK]: ON_HOVER,
	[RATING_MARK]: ON_HOVER,
	[GIF_MARK]: ALWAYS,
	[DURATION_MARK]: ALWAYS
};

/** The class a mark carries when its answer is `hover`. Defined in `app.css`, so that the tile and
 *  the controls overlay (two components) reveal on the same rule rather than on two copies. */
export const ON_HOVER_CLASS = 'tile-mark-on-hover';

function known(value: unknown): value is MarkAnswer {
	return value === ALWAYS || value === ON_HOVER || value === NEVER;
}

class TileMarks {
	#answers = $state<Record<string, MarkAnswer>>({ ...DEFAULTS });

	/** Whether the server has answered yet. The settings pane waits for it before drawing. */
	loaded = $state(false);

	#loading: Promise<void> | null = null;

	/* How many times these answers have been thrown away. Only ever compared with itself.
	 *
	 * The same guard `Theme` carries, for the same reason: a read that was in the air when the
	 * account changed describes the PREVIOUS account's preferences, and letting it land would show
	 * one person's answers on another person's screen until the second read finished.
	 */
	#discarded = 0;

	/** Read them, once per page load. Repeated calls join the request already in flight. */
	load(): Promise<void> {
		if (this.#loading) return this.#loading;
		const asked = this.#discarded;
		this.#loading = (async () => {
			try {
				const values = await fetchSettingValues();
				if (this.#discarded !== asked) return;
				const answers = { ...DEFAULTS };
				for (const key of MARK_KEYS) {
					const value = values.get(key);
					// An answer this version does not know is the default rather than an error: a
					// preference written by a later Sift must not leave a tile with no marks at all.
					if (known(value)) answers[key] = value;
				}
				this.#answers = answers;
			} catch {
				// Unreadable preferences are not worth a message. The defaults are what a fresh
				// install draws and the grid is usable either way.
			} finally {
				// Guarded like the assignment above: a read discarded halfway must not announce that
				// this account's answers have arrived, or the settings pane draws the defaults as
				// though they were somebody's choice until the real read lands.
				if (this.#discarded === asked) this.loaded = true;
			}
		})();
		return this.#loading;
	}

	/** Drop what was read, because it belonged to somebody else.
	 *
	 * Called from `account-scoped.ts` when the signed-in account changes, and nowhere else. Without
	 * it the memo above means "read once per PAGE" rather than once per account, and signing out
	 * and back in without a reload (which is what the desktop shell always does, because its page
	 * never reloads) would leave the previous account's answers in place for good.
	 */
	forget(): void {
		this.#discarded += 1;
		this.#loading = null;
		this.#answers = { ...DEFAULTS };
		this.loaded = false;
	}

	answer(key: string): MarkAnswer {
		return this.#answers[key] ?? ALWAYS;
	}

	/** Whether this mark is drawn at all. */
	shows(key: string): boolean {
		return this.answer(key) !== NEVER;
	}

	/**
	 * These marks in the order a row draws them: every one drawn always, then every one drawn only
	 * on hover, each group keeping the order it was given in.
	 *
	 * A mark waiting for the hover is transparent, not absent: `ON_HOVER_CLASS` keeps its space,
	 * so a row's layout does not jump under the pointer. So a waiting mark written first would hold
	 * a hole open at the start of the row with the marks that ARE showing after it: the
	 * always-drawn Hidden mark starting about 86px in, behind two invisible counts. Drawing the
	 * resting marks first puts what is on screen at the edge, and the waiting ones fill in after
	 * it.
	 *
	 * The ORDER of the markup, not CSS `order`: two of the top row's marks are buttons, and the
	 * keyboard walks the markup. A visual order the focus did not follow would be two orders for
	 * one row. One answer for both rows (the corner marks in `Tile`, the heart and rating in
	 * `TileControls`), so the two cannot come to disagree about which end the resting marks go.
	 */
	restingFirst<Key extends string>(keys: readonly Key[]): Key[] {
		const resting = keys.filter((key) => this.answer(key) === ALWAYS);
		return [...resting, ...keys.filter((key) => !resting.includes(key))];
	}

	/** The class to put on it: empty when it is always drawn, the reveal class when it is not. */
	revealed(key: string): string {
		return this.answer(key) === ON_HOVER ? ON_HOVER_CLASS : '';
	}

	/** Take marks chosen somewhere else. Only the ones the batch mentions (see `Theme.follow`).
	 *
	 * The record is rebuilt rather than written into, because it is what the walls read: assigning
	 * a field on the held object changes nothing anybody is watching.
	 */
	follow(saved: Record<string, unknown>): void {
		let answers: Record<string, MarkAnswer> | null = null;
		for (const key of MARK_KEYS) {
			/* ONE guard, and it covers both cases: a mark the batch does not mention reads as
			 * `undefined`, which is not an answer this version knows either, so a separate `key
			 * in saved` check ahead of it would be dead code.
			 *
			 * And "not known" means LEFT WHERE IT WAS, never reset: a preference written by a
			 * later Sift must not strip the marks off a tile.
			 */
			const value = saved[key];
			if (!known(value)) continue;
			answers ??= { ...this.#answers };
			answers[key] = value;
		}
		if (answers) this.#answers = answers;
	}

	/** Change one, on the screen first and then on the server. Put back if the server refuses. */
	async set(key: string, answer: MarkAnswer): Promise<void> {
		const previous = this.answer(key);
		this.#answers = { ...this.#answers, [key]: answer };
		try {
			await saveSettings({ [key]: answer });
		} catch (error) {
			this.#answers = { ...this.#answers, [key]: previous };
			throw error;
		}
	}
}

export const tileMarks = new TileMarks();

/* Follow the marks while the app is open, wherever they were changed. See `theme.svelte`: the
 * same gap, and these are read by every wall in the application. */
onSettingsSaved((saved) => tileMarks.follow(saved));
