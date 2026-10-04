/*
 * The pages somebody had in front of them, reported to the server for Insights to add up later.
 *
 * WHAT IS A VISIT. A page about a thing or a place: a person's, a tag's, a Site's, a Collection's,
 * a Photo Set's, a song's, a folder, one of the walls, a section of Settings, Organize, Insights
 * (`placeOf`). A file opened over a wall is not a visit: it is a sitting, which the player reports,
 * so the wall underneath stops counting time while the file is open.
 *
 * HOW LONG IT WAS IN FRONT. Counted on the page's own monotonic clock, and only while the tab is
 * showing: a tab behind another one, a minimised window and a locked phone add nothing. The server
 * is told how long AGO each visit opened and was last in front, never a time of day, because this
 * device's clock is not the server's.
 *
 * CHEAP. One small write a visit, batched: a visit is handed over when it ends, a page still open
 * is reported again every `EVERY_MS` under the same id (the server keeps one row and moves it on),
 * and everything waiting goes when the tab is hidden or closed, on a request the browser finishes
 * after the page has gone. Nothing here is on the path of drawing a wall, and a report that fails
 * is let go: a page somebody looked at is not worth an error message.
 *
 * ONE VISIT UNTIL THE PAGE IS LEFT. A tab the browser put to sleep and brought back, a reload, or
 * a fresh copy of this module starts a new record of visits with nothing in it, and the page still
 * on screen would begin a second visit beside the first. So the visit open is written into the
 * tab's own storage as it goes (`Kept`), and the next record takes it up again when it opens the
 * same page for the same person within `RESUME_MS`: the same id, its time in front carried on.
 *
 * WHOSE. Each visit remembers who was signed in when it began, and is only ever sent while that
 * same person is signed in: a visit from before a sign-out is dropped, never sent as the next
 * person's. The server writes nothing while their history is paused, and nothing for a page the
 * vault keeps from them.
 */

import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { bridge } from '$lib/bridge';
import { phoneWidth } from '$lib/components/common/phone-width.svelte';
import { kindOf, setClientKind } from '$lib/shell/client-kind';
import { session } from '$lib/shell/session.svelte';

/** The kinds of page a visit can be to, as the server spells them. */
type VisitPlace = VisitReport['place'];

/** Which page: the kind, and which one (an id, or a wall's, section's or queue's own word). */
export interface Place {
	place: VisitPlace;
	ref: string;
}

/** A visit as it crosses the wire. */
export type VisitReport = components['schemas']['VisitReport'];

/** How often a page still open is reported again. */
const EVERY_MS = 60_000;

/**
 * How long a page out of sight can stay one visit when the tab comes back to it as a new document:
 * the gap after which the server starts a new sitting of the app, so a visit never spans two.
 */
export const RESUME_MS = 30 * 60_000;

/** The most visits one report carries: the server's own ceiling. */
const MOST = 240;

/** The pages about one thing, by route. */
const THINGS: Readonly<Record<string, VisitPlace>> = {
	'/people/[id]': 'person',
	'/tags/[id]': 'tag',
	'/sites/[id]': 'site',
	'/collections/[id]': 'collection',
	'/photo-sets/[id]': 'photo_set',
	'/songs/[id]': 'song'
};

/** The walls, by route, with the word each is recorded under. */
const WALLS: Readonly<Record<string, string>> = {
	'/browse': 'library',
	'/favorites': 'favorites',
	'/recent': 'recent',
	'/loops': 'loops',
	'/hidden': 'hidden',
	'/downloads': 'downloads',
	'/start': 'start',
	'/theater': 'theater',
	'/people': 'people',
	'/tags': 'tags',
	'/sites': 'sites',
	'/collections': 'collections',
	'/photo-sets': 'photo_sets',
	'/songs': 'songs'
};

/** The Organize screens, by route, with the queue each is about (null: the route's own). */
const ORGANIZE: Readonly<Record<string, string | null>> = {
	'/organize': '',
	'/organize/[queue]': null,
	'/organize/[queue]/[id]': null,
	'/organize/may-be/[person]/[pile]': 'may-be'
};

/** The Insights screens, by route. */
const INSIGHTS: Readonly<Record<string, string>> = {
	'/insights': '',
	'/insights/recaps': 'recaps',
	'/insights/recaps/[id]': 'recap'
};

/** An id as the server mints them, and nothing a path or a name could be. */
const AN_ID = /^[0-9A-HJKMNP-TV-Z]{26}$/;

/** A word the server keeps as a place's `ref`. */
const A_WORD = /^[A-Za-z0-9_.-]{0,64}$/;

/** What a route's parameters and query say, for `placeOf`. */
export interface Where {
	route: string | null;
	params: Readonly<Record<string, string | undefined>>;
	search: URLSearchParams;
	/** The Settings section open over the page, from the address's state, if one is. */
	settings?: string | null;
}

/**
 * Which page this is, or null for one that is not a visit (signing in, the phone's lists, a file's
 * own address, the Remote).
 *
 * Settings is a visit wherever it is open: on its own address, or over another page. The library
 * wall is a folder when it is narrowed to one by its id, and the wall of a search when words were
 * typed into it.
 */
export function placeOf(where: Where): Place | null {
	const { route, params, search } = where;
	if (where.settings) return word('settings', where.settings);
	if (route === null) return null;
	if (route === '/settings/[[section]]') return word('settings', params.section ?? '');
	const thing = THINGS[route];
	if (thing) return params.id && AN_ID.test(params.id) ? { place: thing, ref: params.id } : null;
	if (route === '/browse') {
		const folder = search.get('in') ?? '';
		if (AN_ID.test(folder)) return { place: 'folder', ref: folder };
		if ((search.get('q') ?? '').trim()) return { place: 'wall', ref: 'search' };
	}
	if (route in WALLS) return { place: 'wall', ref: WALLS[route] };
	if (route in ORGANIZE) return word('organize', ORGANIZE[route] ?? params.queue ?? '');
	if (route in INSIGHTS) return { place: 'insights', ref: INSIGHTS[route] };
	return null;
}

function word(place: VisitPlace, ref: string): Place | null {
	return A_WORD.test(ref) ? { place, ref } : null;
}

/** A visit's id: random, the same each time it is reported. Not `randomUUID`, which a browser
 *  withholds from a page served over plain http on the network. */
function newVisitId(): string {
	const bytes = new Uint8Array(16);
	crypto.getRandomValues(bytes);
	return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

interface Open {
	id: string;
	place: Place;
	/** Who was signed in when it began. */
	user: string;
	openedAt: number;
	/** Time in front before the current stretch. */
	frontMs: number;
	/** When the current stretch in front began, or null while it is not in front. */
	since: number | null;
	/** When it was last in front. */
	lastAt: number;
	/** The time in front and the last moment in front as last handed over, or null before. */
	sent: { front: number; last: number; at: number } | null;
}

/**
 * The open visit as the tab's storage keeps it, so a new record can take it up (see the head of
 * this file). Durations only, and the device's wall clock only to measure how long the tab was
 * away between two documents, whose own clocks do not share a zero.
 */
export interface Kept {
	id: string;
	place: VisitPlace;
	ref: string;
	user: string;
	openedAgo: number;
	front: number;
	lastAgo: number;
	savedAt: number;
}

interface Waiting {
	user: string;
	report: VisitReport;
}

/** What `Visits` needs from the world, handed in so a test can stand in for each. */
export interface Surroundings {
	now: () => number;
	/** Who is signed in now, or null. */
	who: () => string | null;
	send: (visits: VisitReport[], leaving: boolean) => Promise<void>;
	/** The tab's own storage for the visit open, and the wall clock it is timed by. */
	keep?: { read: () => Kept | null; write: (kept: Kept | null) => void; wall: () => number };
}

export class Visits {
	#open: Open | null = null;
	#waiting = new Map<string, Waiting>();
	#inFront = true;
	#covered = false;
	readonly #world: Surroundings;

	constructor(world: Surroundings) {
		this.#world = world;
	}

	/** The page now on screen, by who is looking at it. A new page ends the visit before it. */
	show(place: Place | null, user: string | null): void {
		const open = this.#open;
		if (
			open &&
			place &&
			open.user === user &&
			open.place.place === place.place &&
			open.place.ref === place.ref
		) {
			return;
		}
		if (open) this.#close();
		if (!place || !user) return;
		const now = this.#world.now();
		const showing = this.#inFront && !this.#covered;
		this.#open = this.#takeUp(place, user, now, showing) ?? {
			id: newVisitId(),
			place,
			user,
			openedAt: now,
			frontMs: 0,
			since: showing ? now : null,
			lastAt: now,
			sent: null
		};
		this.#save();
	}

	/** The visit the tab's storage holds, when it is this page for this person and recent enough. */
	#takeUp(place: Place, user: string, now: number, showing: boolean): Open | null {
		const keep = this.#world.keep;
		const kept = keep?.read();
		if (!keep || !kept) return null;
		if (kept.place !== place.place || kept.ref !== place.ref || kept.user !== user) return null;
		// A clock stepped back reads as no time away rather than as time before the visit began.
		const away = Math.max(0, keep.wall() - kept.savedAt);
		if (away > RESUME_MS) return null;
		return {
			id: kept.id,
			place,
			user,
			openedAt: now - kept.openedAgo - away,
			frontMs: kept.front,
			since: showing ? now : null,
			lastAt: now - kept.lastAgo - away,
			sent: null
		};
	}

	/** Write the open visit as it stands into the tab's storage, or clear it when none is open. */
	#save(): void {
		const keep = this.#world.keep;
		if (!keep) return;
		const open = this.#open;
		if (!open) {
			keep.write(null);
			return;
		}
		const now = this.#world.now();
		keep.write({
			id: open.id,
			place: open.place.place,
			ref: open.place.ref,
			user: open.user,
			openedAgo: now - open.openedAt,
			front: open.frontMs + (open.since === null ? 0 : now - open.since),
			lastAgo: open.since === null ? now - open.lastAt : 0,
			savedAt: keep.wall()
		});
	}

	/** Whether the tab is showing. Hidden, the visit stops counting and everything waiting goes. */
	tab(showing: boolean): void {
		this.#inFront = showing;
		this.#settle();
		if (!showing) void this.flush(true);
	}

	/** Whether a file is open over the page, which is a sitting and not time on the page. */
	cover(covered: boolean): void {
		this.#covered = covered;
		this.#settle();
	}

	/** Report everything waiting, and the page still open as it stands. */
	async flush(leaving = false): Promise<void> {
		if (this.#open) this.#keep(this.#open, false, leaving);
		const who = this.#world.who();
		const mine: VisitReport[] = [];
		for (const [id, waiting] of this.#waiting) {
			if (waiting.user === who) mine.push(waiting.report);
			this.#waiting.delete(id);
		}
		if (mine.length === 0) return;
		try {
			await this.#world.send(mine.slice(-MOST), leaving);
		} catch {
			// Let go. A page somebody looked at is not worth an error message, and a report kept
			// back to try again is one more thing to send as the wrong person after a sign-out.
		}
	}

	/** Stop or start counting the open visit's time in front, as the tab and the panel say. */
	#settle(): void {
		const open = this.#open;
		if (!open) return;
		const now = this.#world.now();
		const showing = this.#inFront && !this.#covered;
		if (open.since !== null && !showing) {
			open.frontMs += now - open.since;
			open.lastAt = now;
			open.since = null;
		} else if (open.since === null && showing) {
			open.since = now;
		}
		this.#save();
	}

	#close(): void {
		const open = this.#open;
		if (!open) return;
		this.#keep(open, true, true);
		this.#open = null;
		this.#save();
	}

	/**
	 * Put the visit's report as it stands among those waiting, replacing an earlier one, unless it
	 * says nothing the last report of it did not.
	 *
	 * ONE REQUEST A VISIT UNTIL ITS MINUTE. Closing a tab hides it and then leaves the page, and each
	 * of the two hands over what is waiting, about a second and a half apart (the hidden tab's
	 * report, then the page's leaving): handing the open visit over on both would cost every page
	 * somebody closed on two requests with one id. A visit whose time
	 * in front and last moment in front have not moved since it was handed over is not handed
	 * over again; the minute's re-send of a page in front always has moved.
	 *
	 * And a re-send that is not a leaving waits its minute: two tickers (a layout mounted twice, a
	 * module swapped in development) would otherwise each hand the page in front over, a moment
	 * apart, with one id.
	 */
	#keep(open: Open, ended: boolean, leaving: boolean): void {
		const now = this.#world.now();
		const front = open.frontMs + (open.since === null ? 0 : now - open.since);
		const last = open.since === null ? open.lastAt : now;
		if (open.sent && open.sent.front === front && open.sent.last === last) return;
		if (open.sent && !leaving && now - open.sent.at < EVERY_MS / 2) return;
		open.sent = { front, last, at: now };
		this.#waiting.set(open.id, {
			user: open.user,
			report: {
				id: open.id,
				place: open.place.place,
				ref: open.place.ref,
				opened_ago_ms: Math.max(0, Math.round(now - open.openedAt)),
				last_ago_ms: ended || open.since === null ? Math.max(0, Math.round(now - last)) : 0,
				front_ms: Math.max(0, Math.round(front))
			}
		});
	}
}

/** Where the tab keeps the visit open. Per tab, gone when the tab is closed. */
const KEPT_KEY = 'sift.visit.open';

/** The one record of this page's visits. */
export const visits = new Visits({
	now: () => performance.now(),
	// Nobody while Sift is locked: a report then would only be refused, and the lock screen is not a
	// page anybody visited.
	who: () => (session.viewer && !session.viewer.locked ? session.viewer.id : null),
	send: async (reports, leaving) => {
		await api.post('/insights/visits', { body: { visits: reports }, keepalive: leaving });
	},
	keep: {
		read: () => {
			try {
				const raw = sessionStorage.getItem(KEPT_KEY);
				return raw ? (JSON.parse(raw) as Kept) : null;
			} catch {
				return null;
			}
		},
		write: (kept) => {
			try {
				if (kept) sessionStorage.setItem(KEPT_KEY, JSON.stringify(kept));
				else sessionStorage.removeItem(KEPT_KEY);
			} catch {
				// A browser that keeps nothing for the page splits a visit at a reload, no worse.
			}
		},
		wall: () => Date.now()
	}
});

/**
 * Where the layout is now: the page, who is looking, and whether a file is open over it. Also says
 * which kind of window this is, for every request after it (`client-kind.ts`).
 */
export function noteWhere(where: Where & { covered: boolean }): void {
	setClientKind(
		kindOf({
			desktop: bridge.isDesktop(),
			route: where.route,
			phone: phoneWidth.yes,
			touchOnly:
				typeof matchMedia === 'function' &&
				matchMedia('(pointer: coarse)').matches &&
				!matchMedia('(any-pointer: fine)').matches
		})
	);
	visits.cover(where.covered);
	visits.show(placeOf(where), session.viewer?.id ?? null);
}

/** The one watcher's undo, while it watches. */
let watching: (() => void) | null = null;

/**
 * Watch the tab: hidden or closed, the visit stops counting and what is waiting is sent; and every
 * `EVERY_MS` while it is open, the page in front is reported as it stands. Answers the undo.
 */
export function watchVisits(): () => void {
	// One watcher whoever asks: a second mount answers the first one's undo rather than starting a
	// second minute's ticker.
	if (watching) return watching;
	const tab = () => visits.tab(document.visibilityState === 'visible');
	// Leaving hides the page as well: a browser can fire the leaving before the hiding (Chrome
	// does), and a leave that kept the page in front would make the hiding that follows a moment
	// later a second report of it with one id.
	const leave = () => visits.tab(false);
	const every = setInterval(() => void visits.flush(), EVERY_MS);
	document.addEventListener('visibilitychange', tab);
	window.addEventListener('pagehide', leave);
	watching = () => {
		clearInterval(every);
		document.removeEventListener('visibilitychange', tab);
		window.removeEventListener('pagehide', leave);
		watching = null;
	};
	return watching;
}

/**
 * A file opened from the wall a typed search narrowed: which file, from which search. Only the
 * library's wall with words in it is such a wall. Fire and forget, for the reason a visit is.
 */
export function noteSearchOpen(route: string | null, search: URLSearchParams, id: string): void {
	const query = route === '/browse' ? (search.get('q') ?? '').trim() : '';
	if (!query || !session.viewer) return;
	api.post('/search/opened', { body: { query, asset_id: id } }).catch(() => {
		// The file opened; a note about it that did not save is nothing to tell anybody.
	});
}
