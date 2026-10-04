/** What has happened to one file, as its History pane shows it; held by the file's view so it
 *  survives the pane folding. `null` is "not asked yet", an empty list "nothing is recorded".
 *  Read once per file, and only when the pane opens. */

import { historyOfAsset, undoAt } from '$lib/api/history';
import type { HistoryEvent } from '$lib/components/common';
import { OneRead } from '$lib/jobs/one-read';
import { HISTORY_SETTLE_MS } from '$lib/library/changes.svelte';
import { toasts } from '$lib/shell/toasts.svelte';

/* The read keeps the NEWEST `limit` events and answers them oldest first with the total, so a long
   history loses its top, and "Show earlier" asks again for more, up to what the route accepts. */
const HISTORY_PAGE = 50;
const HISTORY_MOST = 500;

/* The least time between two quiet re-reads: a busy library rings a bell every second or so, and
   a thread read on each of them is a read nobody sees the answer of before the next. */
const HISTORY_SPACING_MS = 1000;

/** The way back an event offers. */
type UndoDoor = NonNullable<HistoryEvent['undo']>;

export class FileHistory {
	events = $state<HistoryEvent[] | null>(null);
	/** Every line the server could draw, which the tab wears; null until the thread is read. */
	total = $state<number | null>(null);
	failed = $state(false);
	limit = $state(HISTORY_PAGE);
	widening = $state(false);
	/** The undo in flight, by its own id, so one row spins rather than the whole thread. */
	undoing = $state<string | null>(null);
	/** The file the thread was last asked for; empty when it is to be asked again. */
	private asked = '';

	/** How many lines the server holds above the ones drawn. */
	readonly earlier = $derived(
		this.events !== null && this.total !== null ? Math.max(0, this.total - this.events.length) : 0
	);

	/* Offered while the server says there is more, up to what the route accepts. */
	readonly mayGoFurther = $derived(this.earlier > 0 && this.limit < HISTORY_MOST);

	/** How many lines the next Show earlier brings. */
	readonly nextEarlier = $derived(Math.min(this.earlier, HISTORY_PAGE));

	/* Bells settle into one quiet re-read, and reads never overlap: a bell while one is out is
	   answered by one more after it (`OneRead`). */
	private settling: ReturnType<typeof setTimeout> | null = null;
	private lastRead = -Infinity;
	/** When the first bell not yet answered rang, so bells that never stop still get a read. */
	private owedSince: number | null = null;
	private current: () => string = () => '';
	private readonly once = new OneRead(async () => {
		this.lastRead = Date.now();
		await this.reread(this.current);
	});

	/** A different file: the thread goes with the old one and is not asked for until wanted. */
	reset(): void {
		this.events = null;
		this.total = null;
		this.failed = false;
		this.asked = '';
		this.limit = HISTORY_PAGE;
	}

	/** Read the thread for this file, once; `current` says which file is showing when it lands. */
	async load(wanted: string, current: () => string): Promise<void> {
		if (this.asked === wanted) return;
		this.asked = wanted;
		this.failed = false;
		try {
			const what = await historyOfAsset(wanted, this.limit);
			// A slower answer for a file already moved on from must not land on the new one.
			if (wanted === current()) this.landed(what.items, what.total);
		} catch {
			if (wanted === current()) {
				this.failed = true;
				// Asked again on the next press: opening the pane again is how somebody retries.
				this.asked = '';
			}
		}
	}

	/* Read the thread again quietly when something it records may have moved. Only a thread already
	   read for this file; the events stay up until the new ones land, and a failed re-read leaves
	   them, since a thread right a moment ago beats a failure line nobody asked for. */
	async reread(current: () => string): Promise<void> {
		const wanted = current();
		if (this.events === null || this.asked !== wanted) return;
		try {
			const what = await historyOfAsset(wanted, this.limit);
			if (wanted === current() && this.asked === wanted) this.landed(what.items, what.total);
		} catch {
			// The thread on screen stays.
		}
	}

	private landed(items: HistoryEvent[], total: number): void {
		this.events = items;
		this.total = Math.max(total, items.length);
	}

	/** A bell rang that the thread may record: one quiet re-read once the bells settle, never
	 *  sooner than `HISTORY_SPACING_MS` after the last one began, and never later than twice that
	 *  after the first bell it answers. */
	soon(current: () => string): void {
		this.current = current;
		const now = Date.now();
		this.owedSince ??= now;
		if (this.settling !== null) clearTimeout(this.settling);
		const due = Math.min(
			Math.max(now + HISTORY_SETTLE_MS, this.lastRead + HISTORY_SPACING_MS),
			this.owedSince + 2 * HISTORY_SPACING_MS
		);
		this.settling = setTimeout(() => {
			this.settling = null;
			this.owedSince = null;
			void this.once.ask();
		}, due - now);
	}

	/** The view is going: no re-read owed to it. */
	stop(): void {
		if (this.settling !== null) clearTimeout(this.settling);
		this.settling = null;
		this.owedSince = null;
		this.once.forget();
	}

	async showEarlier(current: () => string): Promise<void> {
		this.limit = Math.min(this.limit + HISTORY_PAGE, HISTORY_MOST);
		this.widening = true;
		try {
			await this.reread(current);
		} finally {
			this.widening = false;
		}
	}

	/* Take one event back through the server's door, then read the whole file again: undoing a
	   rename changes the name above, and a move changes where the file is. */
	async undo(door: UndoDoor, reload: () => Promise<void>): Promise<void> {
		if (this.undoing) return;
		this.undoing = door.id;
		try {
			await undoAt(door);
			// One re-read: the reload asks for the thread again itself while its pane is showing.
			await reload();
		} catch {
			toasts.show("That couldn't be undone", { tone: 'error' });
		} finally {
			this.undoing = null;
		}
	}
}
