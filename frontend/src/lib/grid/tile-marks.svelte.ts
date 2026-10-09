/*
 * What a tile draws in its corners, and when: always, on hover, never. One store for every wall and
 * the settings pane; the defaults mirror the server's. Where a mark sits is the tile's.
 */

import { fetchSettingValues, onSettingsSaved, saveSettings } from '$lib/settings-ui/settings';

export const ALWAYS = 'always';
export const ON_HOVER = 'hover';
export const NEVER = 'never';

export type MarkAnswer = typeof ALWAYS | typeof ON_HOVER | typeof NEVER;

/* Spelled out, never a shared prefix: the reachability gate credits a prefix for every key. */
export const VIEWS_MARK = 'appearance.tile.views';
export const O_COUNT_MARK = 'appearance.tile.o_count';
export const PINNED_MARK = 'appearance.tile.pinned';
export const SHARING_MARK = 'appearance.tile.sharing';
export const HIDDEN_MARK = 'appearance.tile.hidden';
export const FAVORITE_MARK = 'appearance.tile.favorite';
export const RATING_MARK = 'appearance.tile.rating';
export const GIF_MARK = 'appearance.tile.gif';
export const DURATION_MARK = 'appearance.tile.duration';

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

/** Defined in `app.css`, so the tile and the controls overlay reveal by one rule. */
export const ON_HOVER_CLASS = 'tile-mark-on-hover';

function known(value: unknown): value is MarkAnswer {
	return value === ALWAYS || value === ON_HOVER || value === NEVER;
}

class TileMarks {
	#answers = $state<Record<string, MarkAnswer>>({ ...DEFAULTS });

	loaded = $state(false);

	#loading: Promise<void> | null = null;

	/* A read in the air when the account changed must not land. */
	#discarded = 0;

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
					// An answer from a later Sift is the default, not an error.
					if (known(value)) answers[key] = value;
				}
				this.#answers = answers;
			} catch {
			} finally {
				// Guarded too: a discarded read must not announce this account's answers.
				if (this.#discarded === asked) this.loaded = true;
			}
		})();
		return this.#loading;
	}

	/** Called from `account-scoped.ts` when the account changes: the desktop page never reloads. */
	forget(): void {
		this.#discarded += 1;
		this.#loading = null;
		this.#answers = { ...DEFAULTS };
		this.loaded = false;
	}

	answer(key: string): MarkAnswer {
		return this.#answers[key] ?? ALWAYS;
	}

	shows(key: string): boolean {
		return this.answer(key) !== NEVER;
	}

	/**
	 * Resting marks first, then the ones waiting for hover: a waiting mark keeps its space, so
	 * first it would hold a gap. The markup's order, since the keyboard walks it.
	 */
	restingFirst<Key extends string>(keys: readonly Key[]): Key[] {
		const resting = keys.filter((key) => this.answer(key) === ALWAYS);
		return [...resting, ...keys.filter((key) => !resting.includes(key))];
	}

	revealed(key: string): string {
		return this.answer(key) === ON_HOVER ? ON_HOVER_CLASS : '';
	}

	/** Rebuilt, not written into: the walls watch the record. */
	follow(saved: Record<string, unknown>): void {
		let answers: Record<string, MarkAnswer> | null = null;
		for (const key of MARK_KEYS) {
			/* Unknown, a missing key included, is left where it was, never reset. */
			const value = saved[key];
			if (!known(value)) continue;
			answers ??= { ...this.#answers };
			answers[key] = value;
		}
		if (answers) this.#answers = answers;
	}

	/** On the screen first, put back if the server refuses. */
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

onSettingsSaved((saved) => tileMarks.follow(saved));
