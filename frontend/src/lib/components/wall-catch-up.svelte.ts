/**
 * Keeping a wall of files current while somebody looks at it: re-reading the page showing, in
 * place, whenever files arrive, leave or gain a picture.
 *
 * A re-read is anchored on the file at the top of the page and never moves the reader: one file
 * arriving at the head re-rows the whole justified wall, so the page holds its files and the server
 * counts what is newer, and the newer files come in by themselves only while the top of the page is
 * on screen. Constructed while the wall initialises, because it keeps effects of its own.
 */

import { untrack } from 'svelte';
import { page } from '$app/state';
import { capture } from '$lib/capture/capture.svelte';
import type { Grid, PageStart, RowSource } from '$lib/grid/grid.svelte';
import { arrivals, whenChanged } from '$lib/library/changes.svelte';
import { imports } from '$lib/library/imports.svelte';
import { mini } from '$lib/player/mini.svelte';
import type { WallMedia } from './wall-media.svelte';
import type { WallOrder } from './wall-order.svelte';

/** How long a few arrivals wait for more while files are pouring in. */
const SETTLED_MS = 1500;

/** Whether this tab's own player is open over the wall: the popout, or the corner panel. */
function playerOver(): boolean {
	return page.state.asset !== undefined || mini.showing;
}

/** What a wall's re-reads read and touch. */
interface WallParts {
	grid: Grid;
	order: WallOrder;
	source(): RowSource;
	media(): WallMedia;
	/** Write the page's first file into the address once a page lands. */
	remember(): void;
}

export class WallCatchUp {
	private wall: WallParts;
	/* A re-read that could not be made when asked (a read in flight, or a hidden window), made
	   when it can be: dropped, a hidden window would go on showing deleted files. */
	private owed = false;
	private settling: ReturnType<typeof setTimeout> | undefined;
	private settledWas: number;
	/** An opinion re-read held while this tab's own player was over the wall. */
	private heldForPlayer = $state(false);
	/** A take of the newer files is on its way, so the line offering them is not drawn for it. */
	takingIn = $state(false);
	/* The read asked in this flush: one live message rings several bells together, and a read asked
	   after them all covers each. A later ask while it is out is owed, never joined. */
	private askedThisTurn: Promise<unknown> | undefined;

	constructor(wall: WallParts) {
		this.wall = wall;
		const grid = wall.grid;
		$effect(() => {
			void grid.reading;
			untrack(() => this.payWhatIsOwed());
		});
		/* Whenever the count of what is waiting changes, and the take itself, which returns it to
		   nought and stands the timer down. */
		$effect(() => {
			void grid.newer;
			untrack(() => this.letThemIn());
		});
		$effect(() => () => clearTimeout(this.settling));
		$effect(() => {
			if (!this.heldForPlayer || playerOver()) return;
			untrack(() => {
				this.heldForPlayer = false;
				void this.catchUp();
			});
		});
		/* Work finished, so there is something new; not on mount, which would repeat the first read. */
		this.settledWas = untrack(() => imports.settled);
		$effect(() => {
			const now = imports.settled;
			untrack(() => {
				if (now === this.settledWas) return;
				this.settledWas = now;
				void this.catchUp();
				wall.media().rearm();
			});
		});
		/* While a scan runs, on every push, so a tile appears within a second of Sift reading it. */
		$effect(() => {
			void imports.pulse;
			if (imports.busy > 0) void untrack(() => this.catchUp());
		});
		/* A placeholder hands over to the real tile the moment the row exists: re-read first and
		   cleared after, so the tile is on screen before the placeholder leaves. */
		$effect(() => {
			void imports.pulse;
			untrack(() => {
				for (const item of capture.imports) {
					if (!imports.landed(item.id)) continue;
					void Promise.resolve(this.catchUp()).finally(() => capture.settled(item.id));
				}
			});
		});
		/* Files entered the library, left it, moved or gained a picture; the queue changes no tile. */
		whenChanged(arrivals, () => {
			void this.catchUp();
			wall.media().rearm();
		});
	}

	/** Make a re-read that is owed, if one is and it can be made now. */
	payWhatIsOwed(): void {
		const grid = this.wall.grid;
		if (!this.owed || grid.reading || document.visibilityState !== 'visible') return;
		this.owed = false;
		void this.catchUp();
		this.wall.media().rearm();
	}

	/** Re-read the page showing, where it is. Returns the ask, for the placeholder handover. */
	catchUp(): Promise<unknown> | undefined {
		if (this.askedThisTurn) return this.askedThisTurn;
		const { grid, order } = this.wall;
		const source = this.wall.source();
		if (document.visibilityState !== 'visible' || grid.reading) {
			this.owed = true;
			return;
		}
		/* Not anchored when asking by meaning (an anchor deep in the ranking is not in the set), nor
		   where the source cannot be anchored. */
		const anchor = order.byMeaning || !source.anchored ? undefined : grid.items[0]?.id;
		// With where the page is, so a first file deleted since is not a jump to the top: `near`.
		const start: PageStart =
			anchor === undefined ? { at: grid.offset } : { from: anchor, near: grid.offset };
		// Quiet: nobody asked for this one. See `loadAt`.
		const asked = untrack(() => grid.loadAt(order.fullQuery, start, { quiet: true }));
		this.askedThisTurn = asked;
		queueMicrotask(() => (this.askedThisTurn = undefined));
		return asked;
	}

	/* A heart, stars or a view moved a file in an order that hangs on them. Held while this tab's
	   own player is over the wall, which reorders nothing under it, and made once it has gone. */
	opinionMoved(): void {
		if (playerOver()) this.heldForPlayer = true;
		else void this.catchUp();
	}

	/* Take the files that arrived since this page was chosen, from where the page was ASKED to
	   start, so a page further down catches up without jumping to the top. Not quiet: it was pressed. */
	takeTheNewOnes(): void {
		const { grid, order } = this.wall;
		this.takingIn = true;
		void grid
			.loadAt(order.fullQuery, { at: grid.askedAt })
			.then(() => this.wall.remember())
			.finally(() => (this.takingIn = false));
	}

	/** The "new" or "moved" line, for what a take does not already have on its way; a lone
	 *  newcomer waits behind it even at the top, since nothing on a page moves by itself. */
	get offersTheNew(): boolean {
		const grid = this.wall.grid;
		return grid.newer > 0 && !this.takingIn;
	}

	/** Whether the newest file this page holds is on screen; no before the observer has run. */
	private atTheTop(): boolean {
		const newest = this.wall.grid.items[0]?.id;
		return newest !== undefined && this.wall.media().onScreen.has(newest);
	}

	/** Enough files to move the wall for: whatever fills its first row. */
	private aRowsWorth(): number {
		return Math.max(1, this.wall.grid.rows[0]?.tiles.length ?? 1);
	}

	/* A row's worth comes in by itself at the top of the page, fewer once a scan stops pouring them
	   in, and otherwise waits behind its line: re-rowing a justified wall is a strobe by its rate.
	   Only arrivals: files that moved up wait for the press. */
	letThemIn(): void {
		const grid = this.wall.grid;
		clearTimeout(this.settling);
		this.settling = undefined;
		// Never over a page somebody asked for that is still on its way.
		if (grid.newer === 0 || !grid.newerArrived || grid.loading || !this.atTheTop()) return;
		if (grid.newer >= this.aRowsWorth()) {
			this.takeTheNewOnes();
			return;
		}
		if (imports.busy === 0) return;
		this.settling = setTimeout(() => {
			this.settling = undefined;
			// Asked again: a page can be turned or scrolled in the wait.
			if (grid.newer > 0 && grid.newerArrived && !grid.loading && this.atTheTop())
				this.takeTheNewOnes();
		}, SETTLED_MS);
	}
}
