// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * Which screen we were on before this one, so the way back to a wall returns to the page and the
 * scroll it was left at rather than page one. Back RETURNS TO THE ENTRY (address, router state and
 * the scroll snapshot of `components/shell/page-scroll.ts`); a link makes a new one that starts at
 * the top. So `returnTo` steps back where it can and goes to the remembered address where not.
 */

import { goto } from '$app/navigation';

const CAME_FROM = 'sift:came-from';
const HERE = 'sift:here';

class CameFrom {
	/**
	 * The address the screen at `path` was left at, as a same-origin path, or null: the fallback
	 * where the browser keeps no record (see `returnTo`).
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

	leadsTo(path: string): boolean {
		return this.addressOf(path) !== null;
	}

	/**
	 * How many steps back the screen at `path` is, read from the browser's own record of this tab:
	 * every entry at THIS pathname is walked over first. Null where the browser cannot say.
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
 * THE ONE RULE FOR A WAY BACK THAT NAMES A PLACE: the browser's own step where the screen before is
 * `href`, else the remembered address, else the link itself (always with a modifier key).
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
 * The same rule for an exit a screen takes on its own (deleted, decided, cancelled), so it does not
 * land on page one of the bare wall. `href` is the wall, as its crumb names it.
 */
export async function leaveFor(href: string): Promise<void> {
	await take(wayBackTo(href) ?? { address: href });
}

type WayBack = { steps: number } | { address: string };

/** The first two of `returnTo`'s answers, else null ("use the link"). */
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

/* The slice of the Navigation API this reads: not in the compiler's DOM library yet. */
interface EntryRecord {
	url: string | null;
	index: number;
}

interface TabRecord {
	currentEntry: EntryRecord | null;
	entries(): EntryRecord[];
}

function browserHistory(): TabRecord | null {
	const found = (globalThis as { navigation?: Partial<TabRecord> }).navigation;
	return found && typeof found.entries === 'function' ? (found as TabRecord) : null;
}

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
 * Note this page's address so the next one knows where it came from. Not `afterNavigate`, which
 * registered in the root layout turns every link into a full document load. `sessionStorage`, so a
 * load survives it; a change of query alone is not a move. Also keeps the document's first screen.
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

/* Module variables: a page load starts them again, a client-side navigation does not. */
let firstPath: string | null = null;
let movedOn = false;

/**
 * Whether `url` is the first screen of this page load. Not `afterNavigate`'s `from === null`: a
 * route drawn after the session check registers too late to hear it.
 */
export function isFirstScreen(url: URL): boolean {
	return firstPath === null || (!movedOn && url.pathname === firstPath);
}

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
		// The way back falls back to being a plain link.
	}
}
