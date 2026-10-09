/*
 * The pages somebody had in front of them, for Insights: a page about a thing or a place (a file
 * opened over a wall is a sitting instead). Time counts only while the tab shows; batched, reported
 * again every `EVERY_MS` under one id, sent on hide and close. A page resumed after a reload is the
 * same visit (`Kept`). Each visit belongs to who was signed in when it began.
 */

import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { bridge } from '$lib/bridge';
import { phoneWidth } from '$lib/components/common/phone-width.svelte';
import { kindOf, setClientKind } from '$lib/shell/client-kind';
import { session } from '$lib/shell/session.svelte';

type VisitPlace = VisitReport['place'];

export interface Place {
	place: VisitPlace;
	ref: string;
}

export type VisitReport = components['schemas']['VisitReport'];

const EVERY_MS = 60_000;

/** The server's gap for a new sitting, so a visit never spans two. */
export const RESUME_MS = 30 * 60_000;

const MOST = 240;

const THINGS: Readonly<Record<string, VisitPlace>> = {
	'/people/[id]': 'person',
	'/tags/[id]': 'tag',
	'/sites/[id]': 'site',
	'/collections/[id]': 'collection',
	'/photo-sets/[id]': 'photo_set',
	'/songs/[id]': 'song'
};

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

const ORGANIZE: Readonly<Record<string, string | null>> = {
	'/organize': '',
	'/organize/[queue]': null,
	'/organize/[queue]/[id]': null,
	'/organize/may-be/[person]/[pile]': 'may-be'
};

const INSIGHTS: Readonly<Record<string, string>> = {
	'/insights': '',
	'/insights/recaps': 'recaps',
	'/insights/recaps/[id]': 'recap'
};

const AN_ID = /^[0-9A-HJKMNP-TV-Z]{26}$/;

const A_WORD = /^[A-Za-z0-9_.-]{0,64}$/;

export interface Where {
	route: string | null;
	params: Readonly<Record<string, string | undefined>>;
	search: URLSearchParams;
	settings?: string | null;
}

/**
 * Null for what is not a visit (signing in, a file's own address, the Remote). Settings counts
 * wherever it is open.
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

/** Not `randomUUID`, which plain http withholds. */
function newVisitId(): string {
	const bytes = new Uint8Array(16);
	crypto.getRandomValues(bytes);
	return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

interface Open {
	id: string;
	place: Place;
	user: string;
	openedAt: number;
	frontMs: number;
	since: number | null;
	lastAt: number;
	sent: { front: number; last: number; at: number } | null;
}

/** Durations only; the wall clock measures the time away between two documents. */
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

/** Handed in so a test can stand in for each. */
export interface Surroundings {
	now: () => number;
	who: () => string | null;
	send: (visits: VisitReport[], leaving: boolean) => Promise<void>;
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

	#takeUp(place: Place, user: string, now: number, showing: boolean): Open | null {
		const keep = this.#world.keep;
		const kept = keep?.read();
		if (!keep || !kept) return null;
		if (kept.place !== place.place || kept.ref !== place.ref || kept.user !== user) return null;
		// A clock stepped back reads as no time away.
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

	tab(showing: boolean): void {
		this.#inFront = showing;
		this.#settle();
		if (!showing) void this.flush(true);
	}

	/** A file over the page is a sitting, not time on the page. */
	cover(covered: boolean): void {
		this.#covered = covered;
		this.#settle();
	}

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
			// Let go: a report kept back could go as the wrong person after a sign-out.
		}
	}

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
	 * Not handed over again unless it moved, so closing a tab (hide, then leave) is one request.
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

const KEPT_KEY = 'sift.visit.open';

export const visits = new Visits({
	now: () => performance.now(),
	// Nobody while Sift is locked.
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

/** Also says which kind of window this is (`client-kind.ts`). */
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

let watching: (() => void) | null = null;

export function watchVisits(): () => void {
	// One watcher whoever asks.
	if (watching) return watching;
	const tab = () => visits.tab(document.visibilityState === 'visible');
	// Leaving hides too: Chrome can fire the leaving first.
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

/** A file opened from the library's wall with words typed in. */
export function noteSearchOpen(route: string | null, search: URLSearchParams, id: string): void {
	const query = route === '/browse' ? (search.get('q') ?? '').trim() : '';
	if (!query || !session.viewer) return;
	api.post('/search/opened', { body: { query, asset_id: id } }).catch(() => {
		// The file opened; a note about it that did not save is nothing to tell anybody.
	});
}
