/*
 * The row a wall is opened at, carried in the address.
 *
 * Walls page by whole rows, so a page NUMBER means different cards on different screens; the row
 * somebody was looking at is durable. One module for the seven walls because the rule has quiet
 * edges: the anchor is honoured on ARRIVAL only (a new question must not start at the old one's
 * row); it is written only while the screen is still the one on show and nobody is navigating
 * away; and it is written with `replaceState`, immediately and with the page state carried, so Back
 * leaves the screen and a panel just opened is not thrown away. `replaceState` does not move
 * `page.url`, so the writer also tells the recorder itself (`write`).
 */

import { replaceState } from '$app/navigation';
import { navigating, page } from '$app/state';

import { noteAddress } from '$lib/shell/navigation.svelte';

/** The address's name for the row a page starts at. One name, on every screen. */
export const ANCHOR = 'from';

/**
 * The address's name for WHERE that row was: its offset when the page was written. The server
 * reads it only when the row named by `from` is gone (`resume_at`), so the same offset opens the
 * same page with the gap closed instead of page one.
 */
export const NEAR = 'near';

/** Where a wall was left, as its address carries it: the first row, and the offset it was at. */
export interface Anchor {
	from: string;
	/** Null for an address written before `near` existed, or one somebody typed without it. */
	near: number | null;
}

/**
 * Whether somebody is already on their way somewhere else. The path check reads where we are now,
 * which a started navigation has not settled, so the write would land on top of it. Skipped, not
 * queued: the screen being left writes its position again next time.
 */
function leaving(): boolean {
	return navigating.to !== null;
}

/**
 * The address as the BROWSER has it, which is not always what the router says.
 *
 * `page.url` stays at the arrival address after a `replaceState`, which is right for reading the
 * anchor and wrong for deciding whether a write is needed: paging back to the row a wall opened at
 * would write nothing, and `forgetAnchor` could never take an anchor off. Taken only when it names
 * the same screen, so an address for somewhere else (every test) comes back as passed.
 */
function inTheBar(url: URL): URL {
	try {
		const live = new URL(window.location.href);
		return live.pathname === url.pathname ? live : url;
	} catch {
		return url;
	}
}

/**
 * The anchor in the address, or null.
 *
 * Read ONCE, when the screen first settles; only the screen knows what a new question means.
 * `near` counts only beside a `from` (alone it would be a page number) and only as a whole number.
 */
export function anchorIn(url: URL): Anchor | null {
	const from = url.searchParams.get(ANCHOR);
	if (!from) return null;
	return { from, near: offsetIn(url.searchParams.get(NEAR)) };
}

function offsetIn(raw: string | null): number | null {
	if (raw === null || !/^\d{1,9}$/.test(raw)) return null;
	return Number(raw);
}

/**
 * Write where we ended up into the address, once the page has landed: the first row (`from`) and
 * the offset it is at (`near`, see `NEAR`).
 *
 * Afterwards, because the row is known only once the answer arrives, which also covers a resize, a
 * re-sort or an import above. `path` is the screen's route captured at mount, so a file opened over
 * the screen is not navigated away from. A row with no id (the Identified wall's nameless card) is
 * not written.
 */
export function rememberAnchor(
	url: URL,
	path: string,
	id: string | null | undefined,
	near: number
): void {
	if (url.pathname !== path || !id || leaving()) return;
	const now = inTheBar(url);
	const wanted = new URL(now);
	wanted.searchParams.set(ANCHOR, id);
	// Where that row was, for the way back when the row is gone; never one without the other.
	if (Number.isInteger(near) && near >= 0) wanted.searchParams.set(NEAR, String(near));
	else wanted.searchParams.delete(NEAR);
	// Same address means nothing to do, against the address in the BAR (`inTheBar`), since a write
	// to where you already are still costs a history entry.
	if (wanted.href === now.href) return;
	write(wanted);
}

/** Take the anchor out of the address when the question changes, so a copied link does not carry it. */
export function forgetAnchor(url: URL, path: string): void {
	if (url.pathname !== path || leaving()) return;
	const now = inTheBar(url);
	if (!now.searchParams.has(ANCHOR) && !now.searchParams.has(NEAR)) return;
	const wanted = new URL(now);
	wanted.searchParams.delete(ANCHOR);
	wanted.searchParams.delete(NEAR);
	write(wanted);
}

/**
 * Put an address up, without going anywhere.
 *
 * Not a navigation: nothing loads, the entry is replaced, and it happens NOW, carrying the page
 * state a panel is drawn from. `replaceState` never assigns `page.url`, so an effect watching it
 * could not see this write; the recorder (`noteAddress`, what "back to the wall" reads) is told in
 * the same breath instead.
 */
function write(wanted: URL): void {
	replaceState(here(wanted), page.state);
	recordOnEntry();
	noteAddress(wanted);
}

/** Where the router keeps each history entry's own record of the address it is for. */
const ROUTERS_RECORD = 'sveltekit:pageurl';

/**
 * Tell the history entry the address it now holds.
 *
 * SvelteKit's `replaceState` stamps the entry's `sveltekit:pageurl` with the stale `page.url`, and
 * a `popstate` navigates to that stamp, so Back to a wall would land on its arrival address. This
 * copies the bar into the stamp. The key is the router's; `anchor.test.ts` holds it against the
 * router's constants so a rename is caught.
 */
function recordOnEntry(): void {
	try {
		const kept = history.state as Record<string, unknown> | null;
		if (!kept || !(ROUTERS_RECORD in kept)) return;
		history.replaceState(
			{ ...kept, [ROUTERS_RECORD]: window.location.href },
			'',
			window.location.href
		);
	} catch {
		// A browser that refuses history writes has already refused the one above. Nothing to say.
	}
}

/**
 * The same address, as a path this app can navigate to: an origin that is not the page's (Sift is
 * reached through proxies and tunnels) is an external navigation the router refuses.
 */
function here(url: URL): string {
	return `${url.pathname}${url.search}`;
}

/**
 * What a wall asks for: a page size, and either where to start or which row to start AT, never
 * both, so the server does not decide between two questions (`CardPaging.query`). `near` rides
 * with `from` and is read only when the row is gone.
 */
export type PageAsk =
	{ limit: number; offset: number } | { limit: number; from: string; near?: number | null };

/** The same, as query parameters. `from` and `offset` are mutually exclusive by construction. */
export function asked(page: PageAsk): Record<string, string> {
	if (!('from' in page)) return { limit: String(page.limit), offset: String(page.offset) };
	const ask: Record<string, string> = { limit: String(page.limit), from: page.from };
	if (page.near !== undefined && page.near !== null) ask.near = String(page.near);
	return ask;
}
