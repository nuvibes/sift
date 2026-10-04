import { untrack } from 'svelte';
import type { components } from '$lib/api/schema';

/* The bells the whole application rings when something it is drawing has moved.
 *
 * Screens read a list once and keep it, so a change elsewhere (a share taken back, a file arriving)
 * needs a bell. What arrives from the server is a word, never a row (one exception below): the
 * screens re-ask the ordinary endpoints, which go through the permission layer. Separate bells
 * because their costs differ by an order of magnitude: an import rings for every beat of a scan,
 * and only the surfaces that draw files should hear it.
 */

/**
 * One thing that can change, and a number that moves when it does: a count, never the value, so
 * a bell is never a second copy of the state a screen holds.
 */
class Signal {
	generation = $state(0);

	/* Stores that live as long as the tab (saved searches, the rating scale, the record registry)
	 * have no setup context for an effect, so they get a plain list. Nobody unsubscribes. */
	#lasting = new Set<() => void>();

	changed(): void {
		this.generation += 1;
		for (const listen of this.#lasting) listen();
	}

	/** Be told for as long as this tab is open. For a store that is not a screen. */
	subscribe(listen: () => void): void {
		this.#lasting.add(listen);
	}
}

/**
 * Re-read when this signal moves. Call it during component setup.
 *
 * Never on the first run, or every screen would load twice. One helper, so no screen is quietly
 * left unwired.
 */
export function whenChanged(signal: Signal, reload: () => void): void {
	let seen = untrack(() => signal.generation);
	$effect(() => {
		const now = signal.generation;
		if (now === seen) return;
		seen = now;
		untrack(reload);
	});
}

/**
 * What this account may see has changed, with no new file involved: something shared, hidden,
 * collected, renamed, created or deleted. Every scoped list re-reads. Not rung for a file arriving
 * (`arrivals`), which happens far more often.
 */
export const libraryChanges = new Signal();

/**
 * Files have entered the library, left it, or moved. Only surfaces that draw files listen, since a
 * scan rings this on every beat.
 */
export const arrivals = new Signal();

/** The work queue moved: something queued, started, finished, failed or got further along. */
export const jobChanges = new Signal();

/** The download queue moved, including a transfer simply getting further along. */
export const downloadChanges = new Signal();

/** A setting moved: this account's own, or one the whole installation shares. */
export const settingChanges = new Signal();

/**
 * A list that is this account's own moved: a saved search, a recent search, or what it thinks of a
 * person, a Site, a collection or a tag. A file's heart and stars arrive on `assetState` instead,
 * or every star would cost a page fetch.
 */
export const mine = new Signal();

/**
 * One of this user's screens (an open player or Theater wall offered to their phone) started,
 * stopped, changed what it plays or went away. The phone's list of screens re-reads.
 */
export const screenChanges = new Signal();

/** Which files share a song moved: the music task paired a file. Only the Same music strip hears it. */
export const sameMusicChanges = new Signal();

/**
 * Re-read this screen's list whenever what it may see changes. Its own name because thirty screens
 * call it and a build check looks for it.
 */
export function reloadOnLibraryChange(reload: () => void): void {
	whenChanged(libraryChanges, reload);
	/* And when this account's own opinions move, which these walls draw; folded in so no call
	 * site forgets. The asset grid watches the library bell directly, so a file's star never
	 * costs it a page read. */
	whenChanged(mine, reload);
}

/** What one file is to the person looking at it, plus which file: the server's generated shape. */
export type AssetOpinion = components['schemas']['AssetOpinion'];

/** What a screen can move about a file on its own: the heart and the stars. The view tally is
 * written by the player, so it arrives only from the server. */
export type OpinionPatch = Pick<AssetOpinion, 'favorite' | 'rating'> &
	Partial<Pick<AssetOpinion, 'views' | 'pinned'>>;

type AssetState = AssetOpinion;

/**
 * A heart, a rating or a view count that was just written, so every screen already drawing that
 * file can follow.
 *
 * The one bell that carries an answer, safe because none of it is a permission: it is this
 * account's opinion of a row it already holds. Not `libraryChanges`, or the rating control would
 * cost a page fetch per press. It reaches the grid under the file's screen and other browsers.
 */
class AssetStateChanges {
	generation = $state(0);
	/** The last one written. A screen reads it when the number moves, and never keeps it. */
	last: AssetState | null = null;

	changed(state: AssetState): void {
		this.last = state;
		this.generation += 1;
	}
}

export const assetState = new AssetStateChanges();

/**
 * Apply somebody else's write to this screen's copy of a row.
 *
 * Call it during component setup, like `reloadOnLibraryChange`. It never fires on the first run.
 */
export function onAssetStateChange(apply: (state: AssetState) => void): void {
	let seen = untrack(() => assetState.generation);
	$effect(() => {
		const now = assetState.generation;
		if (now === seen) return;
		seen = now;
		const state = assetState.last;
		if (state) untrack(() => apply(state));
	});
}

/**
 * Something was just written, BY THIS TAB, that a history records.
 *
 * The server announces a membership write (`add`, `assign`) only to accounts whose VIEW it moved
 * (`bump_stamps_for_object`), which on an unshared install is nobody, so the writing tab says so
 * itself. Its own bell, because only a history listens, where `libraryChanges` re-reads every wall.
 */
export const recorded = new Signal();

/** How long a history waits for the next bell before it asks, so a burst of them is one read. */
export const HISTORY_SETTLE_MS = 250;

/**
 * Re-read a history thread whenever something it records may have moved. Call it during setup.
 *
 * On either bell: `recorded` for this tab's writes, `libraryChanges` for the server's. Settled over
 * a short window, since one press often rings both and a bulk write rings per chunk. The caller
 * re-reads quietly, so a thread being read does not blank.
 */
export function rereadOnHistoryChange(reread: () => void, settle = HISTORY_SETTLE_MS): void {
	let pending: ReturnType<typeof setTimeout> | null = null;
	const soon = () => {
		if (pending !== null) clearTimeout(pending);
		pending = setTimeout(() => {
			pending = null;
			reread();
		}, settle);
	};
	whenChanged(recorded, soon);
	whenChanged(libraryChanges, soon);
	// A thread taken off the screen must not ask for itself after it has gone.
	$effect(() => () => {
		if (pending !== null) clearTimeout(pending);
	});
}
