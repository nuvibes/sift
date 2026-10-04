// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * Which screen we were on before this one.
 *
 * It exists for one case: opening a person from the People wall and pressing the way back must
 * not put somebody on PAGE ONE, at the top, looking at eight different people.
 *
 * ## What goes wrong, because it is not the sort
 *
 * The order does not move: `people.sort` lives on a module-level store and survives navigation, and
 * the requests after the return carry the chosen sort. What moves is WHICH PAGE, and from the seat
 * it is the same experience: eight names you were not looking at, in an arrangement you did not
 * choose.
 *
 * Two separate causes, and `returnTo` at the foot of this file is the answer to both.
 *
 * 1. **A way back that is a plain link.** The crumb `EntityHeader` draws and `BackButton` are both
 *    links; `<a href="/people">` is a new navigation to the bare address, so the row the wall
 *    wrote into the address it was left at (`?from=<row>`) is dropped. The browser's own Back, on
 *    the same journey, restores the page perfectly.
 * 2. **The scroll position**, which is not this file's to restore. The walls scroll inside
 *    `PageFrame`'s body rather than the window, and `components/shell/page-scroll.ts` puts that
 *    box back through SvelteKit's `snapshot`, captured as a screen is left, and handed back ONLY
 *    ON A `popstate`, which is precisely "you stepped back to this entry". (That file says why a
 *    restore listening for scrolling is the wrong design.)
 *
 *    **So the way back has to be a step back to get it, and that is the answer rather than a
 *    second restore.** Back RETURNS TO THE ENTRY the wall was left in (the address, the router's
 *    stamped state and the snapshot with it), where going to the remembered address makes a new
 *    entry, which has no snapshot and starts at the top. So `returnTo` below takes the browser's
 *    own step whenever the crumb's target IS the screen before this one in this tab's history, and
 *    goes to the remembered address only where the browser cannot say so.
 */

import { goto } from '$app/navigation';

/** Where the previous and the current address are kept so they survive a page load. */
const CAME_FROM = 'sift:came-from';
const HERE = 'sift:here';

class CameFrom {
	/**
	 * The address of the screen before this one, when that screen is at `path`, or null.
	 *
	 * The question a "back to the wall" control asks before it decides what it is. The answer is
	 * the wall's address AS IT WAS LEFT, anchor and all, so the control can go there and the wall
	 * opens on the row it was showing; a fixed link throws that away and starts the screen again
	 * from nothing.
	 *
	 * The SECOND answer, not the first: see `returnTo`. A screen whose tabs are real links pushes
	 * an entry per tab at the same pathname, so a blind step back lands on the previous tab;
	 * `stepsBackTo` answers that by counting over the tabs from the browser's own record, and this
	 * is what is left where the browser keeps no such record.
	 *
	 * Returned as a same-origin path (`/people?from=abc`), which is what the router takes: a URL
	 * object carries an origin, and one from a proxy or a tunnel is an origin the browser may not
	 * be on. Same origin as well as same path is still checked: an address from somewhere else
	 * names a path that means nothing here.
	 */
	addressOf(path: string): string | null {
		const previous = read(CAME_FROM);
		if (
			previous === null ||
			previous.origin !== window.location.origin ||
			previous.pathname !== path
		) {
			return null;
		}
		return `${previous.pathname}${previous.search}`;
	}

	/** Whether the screen before this one is at `path`. `addressOf`, as a yes or no. */
	leadsTo(path: string): boolean {
		return this.addressOf(path) !== null;
	}

	/**
	 * HOW MANY STEPS BACK THE SCREEN BEFORE THIS ONE IS, when that screen is at `path`, or null.
	 *
	 * Read from the browser's own record of this tab rather than from anything this module wrote,
	 * and that is what makes a step through history safe. A blind `history.back()` gets the COUNT
	 * wrong: a screen whose tabs are real links pushes an entry per tab at the same pathname, so
	 * one step back is the previous tab. This counts: it walks back over every entry at THIS
	 * screen's pathname (the tabs, and a query the page wrote into its own address), and the
	 * first entry at any other pathname is the screen before this one. Only when that screen is
	 * `path` is there a number to hand back.
	 *
	 * `path` is a bare pathname, as `addressOf` takes it, and an address carrying a query never
	 * matches: a crumb to `/browse?in=a` is a particular folder, and stepping back to whichever
	 * folder was open before would be a different place with the same name.
	 *
	 * Null wherever the browser cannot say (no Navigation API in older engines, an entry from
	 * another origin, or nothing behind this entry at all), and the caller then goes to the
	 * remembered address. Null is never "go nowhere"; it is "ask the next rule".
	 */
	stepsBackTo(path: string): number | null {
		const record = browserHistory();
		const here = record?.currentEntry;
		if (!record || !here) return null;
		const herePath = pathOf(here.url);
		if (herePath === null) return null;
		const entries = record.entries();
		for (let at = here.index - 1; at >= 0; at -= 1) {
			const there = pathOf(entries[at]?.url ?? null);
			if (there === null) return null;
			if (there === herePath) continue;
			return there === path ? here.index - at : null;
		}
		return null;
	}
}

/**
 * THE ONE RULE FOR A WAY BACK THAT NAMES A PLACE: the crumb above a page and the back link.
 *
 * Handed a plain left-click on a link to `href`, it decides between three answers, in order:
 *
 * 1. **The browser's own step**, where the screen before this one in this tab IS `href`. The same
 *    thing the Back button does, so the wall comes back as it was left: its page, the router's
 *    stamped state, and the scroll position, which nothing else here can restore (see the header).
 * 2. **The remembered address**, where this module noted that screen but the browser cannot say
 *    where it sits (no Navigation API). The page comes back; the scroll starts at the top.
 * 3. **The link itself**, for everything else: somebody who opened this page directly, who came
 *    from somewhere else, or who pressed with a modifier or the middle button. Each modifier means
 *    they asked for something else, a new tab or a new window, and answering it by moving THIS
 *    window ignores what they asked for.
 *
 * One function for both components because it is one question, and two copies of it would let one
 * step back while the other went to an address.
 */
export function returnTo(event: MouseEvent, href: string): void {
	if (event.defaultPrevented || event.button !== 0) return;
	if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
	const way = wayBackTo(href);
	if (way === null) return;
	event.preventDefault();
	void take(way);
}

/**
 * THE SAME RULE FOR A WAY BACK NOBODY PRESSED: the exit a screen takes on its own.
 *
 * A group of faces leaves once its last face is decided; a person, a Collection, a Tag, a Site or a
 * Photo Set leaves its page once it is deleted or hidden; every "new ..." screen leaves on Cancel.
 * A fixed address there (`goto('/people')`) is a NEW entry at the bare wall: page one, at the
 * top, and for a group of faces always Faces to name, even when it was opened from Discarded.
 *
 * So it is `returnTo`'s three answers, in `returnTo`'s order, read from the same memory: the
 * browser's own step where the screen before this one is `href` (the page, the scroll, the router's
 * state: the entry itself), else the address that screen was left at, else `href` itself. The
 * last is the only difference, and it is not a different rule: a click on a link with nothing
 * better behind it follows the link, and an exit with nothing better behind it goes to it.
 *
 * `href` is the WALL this screen belongs to, as a bare path: the same thing its crumb names.
 *
 * Resolves as soon as the move is under way. A step through history is answered by the router on
 * the `popstate` that follows, and there is nothing a caller could usefully wait on: every caller
 * is leaving the screen it is on.
 */
export async function leaveFor(href: string): Promise<void> {
	await take(wayBackTo(href) ?? { address: href });
}

/** Where a way back to `href` goes, when it goes somewhere better than the plain link. */
type WayBack = { steps: number } | { address: string };

/**
 * The first two of `returnTo`'s three answers, as one decision both entrances share: how many steps
 * back the screen is, else the address it was left at, else null ("use the link").
 */
function wayBackTo(href: string): WayBack | null {
	const steps = cameFrom.stepsBackTo(href);
	if (steps !== null) return { steps };
	const remembered = cameFrom.addressOf(href);
	return remembered === null ? null : { address: remembered };
}

async function take(way: WayBack): Promise<void> {
	if ('steps' in way) {
		history.go(-way.steps);
		return;
	}
	await goto(way.address);
}

/* THE SLICE OF THE NAVIGATION API THIS READS, declared here because the compiler's DOM library does
   not carry it yet. Two members and no more: which entry this is, and the list of them. */
interface EntryRecord {
	url: string | null;
	index: number;
}

interface TabRecord {
	currentEntry: EntryRecord | null;
	entries(): EntryRecord[];
}

/** The browser's record of this tab's history, or null in an engine that does not offer one. */
function browserHistory(): TabRecord | null {
	const found = (globalThis as { navigation?: Partial<TabRecord> }).navigation;
	return found && typeof found.entries === 'function' ? (found as TabRecord) : null;
}

/** The pathname of one entry, or null for an entry with no address or another origin's. */
function pathOf(url: string | null): string | null {
	if (!url) return null;
	try {
		const parsed = new URL(url);
		return parsed.origin === window.location.origin ? parsed.pathname : null;
	} catch {
		return null;
	}
}

export const cameFrom = new CameFrom();

/**
 * Note the address this page is at, so the NEXT one knows where it came from.
 *
 * ## Why this is written by hand and not taken from `afterNavigate`
 *
 * Registering an `afterNavigate` callback in the root layout stops SvelteKit's client router
 * intercepting links ALTOGETHER: every navigation becomes a full document load, every module store
 * is destroyed on every click, and the app goes on looking completely normal. Nothing else would
 * catch it.
 *
 * So this asks the address directly instead. It needs nothing from the router, cannot interfere
 * with it, and works whether a navigation reloads the document or not.
 *
 * ## Why `sessionStorage` and not a variable
 *
 * So that it survives a page load. One tab, gone when the tab closes. Every read and write is
 * wrapped, because a browser with site data blocked throws on the accessor itself rather than
 * returning nothing, and a way back that throws is worse than one that forgets.
 *
 * An address that only changes its QUERY is not a move between screens: the walls write which row
 * they are showing into their own address, and treating each of those as a step would make "where I
 * came from" the page I am standing on.
 *
 * It also keeps a second memory, per DOCUMENT rather than per tab: the first screen this page load
 * showed and whether it has shown another since. Same question (which addresses has this been
 * at?) and a different lifetime, so a variable rather than `sessionStorage`. `isFirstScreen` reads
 * it.
 */
export function noteAddress(url: URL): void {
	if (firstPath === null) firstPath = url.pathname;
	else if (url.pathname !== firstPath) movedOn = true;
	const here = read(HERE);
	if (here && here.pathname === url.pathname) {
		write(HERE, url);
		return;
	}
	if (here) write(CAME_FROM, here);
	write(HERE, url);
}

/* The first pathname this document showed, and whether it has shown a different one since. Module
   variables on purpose: a page load starts them again, and a client-side navigation does not. */
let firstPath: string | null = null;
let movedOn = false;

/**
 * Whether `url` is the first screen of this page load: nothing of Sift's is behind it to go back to.
 *
 * True on a cold load and on a refresh; false for an address reached by following a link inside the
 * application, whichever order the layout's note and the caller run in: a link from elsewhere means
 * the first path noted was another one. Nothing noted yet is the first screen by definition.
 *
 * Why not `afterNavigate`'s `from === null`: see the asset route. A route drawn after the session
 * check registers its callback too late to hear the first navigation, so no answer would come.
 */
export function isFirstScreen(url: URL): boolean {
	return firstPath === null || (!movedOn && url.pathname === firstPath);
}

/** Test support only: forget what this document has shown, as a page load would. */
export function forgetScreensForTests(): void {
	firstPath = null;
	movedOn = false;
}

function read(key: string): URL | null {
	try {
		const kept = sessionStorage.getItem(key);
		return kept ? new URL(kept) : null;
	} catch {
		return null;
	}
}

function write(key: string, url: URL): void {
	try {
		sessionStorage.setItem(key, url.href);
	} catch {
		// Nothing to do and nothing to say. The way back falls back to being a plain link.
	}
}
