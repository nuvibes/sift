import { describe, expect, it } from 'vitest';
import { kindOf } from './client-kind';
import { session } from './session.svelte';
import {
	placeOf,
	RESUME_MS,
	visits,
	Visits,
	watchVisits,
	type Kept,
	type VisitReport,
	type Where
} from './visits';

/* The record of pages somebody had in front of them: which pages are visits, how long each was in
 * front, and that a visit is only ever sent as the person it began with. */

const AN_ID = '01HX0000000000000000000841';

function where(route: string | null, search = '', params: Record<string, string> = {}): Where {
	return { route, params, search: new URLSearchParams(search) };
}

describe('placeOf', () => {
	it('names a thing by its id and a wall by its word', () => {
		expect(placeOf(where('/people/[id]', '', { id: AN_ID }))).toEqual({
			place: 'person',
			ref: AN_ID
		});
		expect(placeOf(where('/favorites'))).toEqual({ place: 'wall', ref: 'favorites' });
		expect(placeOf(where('/insights/recaps'))).toEqual({ place: 'insights', ref: 'recaps' });
		expect(placeOf(where('/organize/[queue]', '', { queue: 'faces' }))).toEqual({
			place: 'organize',
			ref: 'faces'
		});
	});

	it('reads the library as a folder, as the wall of a search, or as itself', () => {
		expect(placeOf(where('/browse', `?in=${AN_ID}`))).toEqual({ place: 'folder', ref: AN_ID });
		expect(placeOf(where('/browse', '?q=harbour'))).toEqual({ place: 'wall', ref: 'search' });
		expect(placeOf(where('/browse', '?in=clips/holiday'))).toEqual({
			place: 'wall',
			ref: 'library'
		});
	});

	it('counts Settings wherever it is open, and nothing that is not a page about something', () => {
		expect(placeOf({ ...where('/favorites'), settings: 'privacy' })).toEqual({
			place: 'settings',
			ref: 'privacy'
		});
		expect(placeOf(where('/settings/[[section]]', '', { section: 'general' }))).toEqual({
			place: 'settings',
			ref: 'general'
		});
		for (const route of ['/login', '/more', '/remote', '/asset/[id]', null]) {
			expect(placeOf(where(route))).toBeNull();
		}
		expect(placeOf(where('/people/[id]', '', { id: 'not an id' }))).toBeNull();
	});
});

describe('Visits', () => {
	function harness(user: string | null = 'u-one') {
		let now = 0;
		let who = user;
		const sent: { reports: VisitReport[]; leaving: boolean }[] = [];
		const visits = new Visits({
			now: () => now,
			who: () => who,
			send: async (reports, leaving) => {
				sent.push({ reports, leaving });
			}
		});
		return {
			visits,
			sent,
			at: (ms: number) => (now = ms),
			as: (next: string | null) => (who = next)
		};
	}

	const wall = { place: 'wall' as const, ref: 'library' };
	const person = { place: 'person' as const, ref: AN_ID };

	it('counts only the time the page was in front, and ends a visit when the page changes', async () => {
		const h = harness();
		h.visits.show(wall, 'u-one');
		h.at(10_000);
		h.visits.tab(false);
		h.at(40_000);
		h.visits.tab(true);
		h.at(45_000);
		h.visits.cover(true);
		h.at(90_000);
		h.visits.cover(false);
		h.at(100_000);
		h.visits.show(person, 'u-one');
		await h.visits.flush();
		const all = h.sent.flatMap((one) => one.reports);
		// Hiding the tab sent the visit as it stood; the last report of it is the whole of it.
		const last = all.filter((one) => one.place === 'wall').at(-1);
		// In front 0-10 s, 40-45 s and 90-100 s: twenty-five seconds of a hundred.
		expect(last).toMatchObject({ opened_ago_ms: 100_000, front_ms: 25_000, last_ago_ms: 0 });
		expect(all.find((one) => one.place === 'person')).toMatchObject({ front_ms: 0 });
	});

	it('reports a page still open under the same id, brought forward', async () => {
		const h = harness();
		h.visits.show(wall, 'u-one');
		h.at(60_000);
		await h.visits.flush();
		h.at(120_000);
		await h.visits.flush();
		const [once, twice] = h.sent.map((one) => one.reports[0]);
		expect(once.id).toBe(twice.id);
		expect([once.front_ms, twice.front_ms]).toEqual([60_000, 120_000]);
	});

	it('stays one visit when the tab comes back as a new document on the same page', async () => {
		// One tab's storage and one wall clock, shared by two records as a reload or a tab the
		// browser put to sleep would share them; each record has its own page clock from zero.
		let kept: Kept | null = null;
		let clock = 1_000_000;
		const sent: VisitReport[] = [];
		const record = (now: () => number, who = 'u-one') =>
			new Visits({
				now,
				who: () => who,
				send: async (reports) => void sent.push(...reports),
				keep: { read: () => kept, write: (next) => (kept = next), wall: () => clock }
			});

		let first = 0;
		const before = record(() => first);
		before.show(person, 'u-one');
		first = 20_000;
		clock += 20_000;
		before.tab(false);
		await before.flush(true);

		// Five minutes out of sight, then a fresh document showing the same page.
		clock += 300_000;
		let second = 0;
		const after = record(() => second);
		after.show(person, 'u-one');
		second = 10_000;
		clock += 10_000;
		await after.flush();

		expect(new Set(sent.map((one) => one.id)).size).toBe(1);
		expect(sent.at(-1)?.front_ms).toBe(30_000);
		expect(sent.at(-1)?.opened_ago_ms).toBe(330_000);

		// Another page, or another person, is a visit of its own.
		after.show(wall, 'u-one');
		await after.flush();
		const third = record(() => 0);
		third.show(person, 'u-one');
		await third.flush();
		expect(new Set(sent.map((one) => one.id)).size).toBe(3);
	});

	it('takes up nothing for somebody else, or after the tab was away too long', () => {
		const stored = (user: string, savedAt: number): Kept => ({
			id: 'kept-visit',
			place: 'person',
			ref: AN_ID,
			user,
			openedAgo: 1_000,
			front: 1_000,
			lastAgo: 0,
			savedAt
		});
		let kept: Kept | null = stored('u-two', 0);
		const ids: string[] = [];
		const record = () =>
			new Visits({
				now: () => 0,
				who: () => 'u-one',
				send: async (reports) => void ids.push(...reports.map((one) => one.id)),
				keep: {
					read: () => kept,
					write: (next) => void (next && ids.push(next.id)),
					wall: () => RESUME_MS + 1
				}
			});
		record().show(person, 'u-one');
		kept = stored('u-one', 0);
		record().show(person, 'u-one');
		expect(ids).not.toContain('kept-visit');
	});

	it('never sends a visit as somebody other than who it began with', async () => {
		const h = harness();
		h.visits.show(wall, 'u-one');
		h.at(5_000);
		h.visits.show(null, null);
		h.as('u-two');
		await h.visits.flush();
		expect(h.sent).toEqual([]);
	});

	it('sends what is waiting as the tab goes, on a request that outlives the page', async () => {
		const h = harness();
		h.visits.show(wall, 'u-one');
		h.at(5_000);
		h.visits.tab(false);
		await Promise.resolve();
		expect(h.sent).toHaveLength(1);
		expect(h.sent[0].leaving).toBe(true);
	});
});

describe('one request a visit', () => {
	it('hands a page over once as the tab hides and the page goes, and again only once it moved', async () => {
		let now = 0;
		const sent: VisitReport[][] = [];
		const visits = new Visits({
			now: () => now,
			who: () => 'u-one',
			send: async (reports) => {
				sent.push(reports);
			}
		});
		visits.show({ place: 'wall', ref: 'favorites' }, 'u-one');
		now = 3_000;
		visits.show({ place: 'wall', ref: 'people' }, 'u-one');
		now = 6_000;
		// Closing a tab: hidden first, then the page leaves.
		visits.tab(false);
		await Promise.resolve();
		now = 7_500;
		await visits.flush(true);
		expect(sent).toHaveLength(1);
		expect(sent[0].map((one) => one.ref)).toEqual(['favorites', 'people']);
		// Back in front, the minute's re-send carries the page because it moved; a second tick at
		// once has nothing new to say.
		visits.tab(true);
		now = 67_500;
		await visits.flush();
		await visits.flush();
		expect(sent).toHaveLength(2);
		expect(sent[1].map((one) => one.ref)).toEqual(['people']);
	});

	it('hands a page over once when the page leaves before it hides', async () => {
		const posted: string[] = [];
		const realFetch = globalThis.fetch;
		globalThis.fetch = (async (url: unknown) => {
			posted.push(String(url));
			return new Response(null, { status: 204 });
		}) as unknown as typeof fetch;
		session.viewer = { id: 'u-leaving', locked: false } as typeof session.viewer;
		const undo = watchVisits();
		try {
			visits.show({ place: 'wall', ref: 'favorites' }, 'u-leaving');
			await new Promise((done) => setTimeout(done, 5));
			// A browser leaving a page: the leaving first, then the hiding.
			window.dispatchEvent(new Event('pagehide'));
			await new Promise((done) => setTimeout(done, 5));
			Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true });
			document.dispatchEvent(new Event('visibilitychange'));
			await new Promise((done) => setTimeout(done, 5));
			expect(posted.filter((one) => one.includes('/insights/visits'))).toHaveLength(1);
		} finally {
			undo();
			visits.show(null, null);
			session.viewer = null;
			globalThis.fetch = realFetch;
			Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
		}
	});

	it('watches the tab once however many times the layout mounts', () => {
		const first = watchVisits();
		expect(watchVisits()).toBe(first);
		first();
		const again = watchVisits();
		expect(again).not.toBe(first);
		again();
	});
});

describe('kindOf', () => {
	const plain = { desktop: false, route: '/browse', phone: false, touchOnly: false };

	it('is the Remote first, then the app, the phone layout, a finger on a wide window', () => {
		expect(kindOf({ ...plain, route: '/remote', desktop: true })).toBe('remote');
		expect(kindOf({ ...plain, desktop: true, phone: true })).toBe('app');
		expect(kindOf({ ...plain, phone: true, touchOnly: true })).toBe('phone');
		expect(kindOf({ ...plain, touchOnly: true })).toBe('tablet');
		expect(kindOf(plain)).toBe('computer');
	});
});
