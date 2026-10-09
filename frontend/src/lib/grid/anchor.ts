/*
 * The row a wall is opened at, carried in the address. Honoured on ARRIVAL only; written with
 * `replaceState`, immediately, only while the screen is on show and nobody is navigating away.
 */

import { replaceState } from '$app/navigation';
import { navigating, page } from '$app/state';

import { noteAddress } from '$lib/shell/navigation.svelte';

export const ANCHOR = 'from';

/** WHERE that row was; the server reads it only when the row is gone (`resume_at`). */
export const NEAR = 'near';

export interface Anchor {
	from: string;
	/** Null for an address written before `near` existed. */
	near: number | null;
}

/** A started navigation has not settled; skipped, not queued. */
function leaving(): boolean {
	return navigating.to !== null;
}

/** The address as the BROWSER has it: `page.url` stays at arrival after a `replaceState`. */
function inTheBar(url: URL): URL {
	try {
		const live = new URL(window.location.href);
		return live.pathname === url.pathname ? live : url;
	} catch {
		return url;
	}
}

/** Read ONCE, when the screen first settles; `near` counts only beside a `from`. */
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
 * Written once the page has landed; a row with no id is not written. `path` is captured at mount.
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
	if (Number.isInteger(near) && near >= 0) wanted.searchParams.set(NEAR, String(near));
	else wanted.searchParams.delete(NEAR);
	// A write to where you already are still costs a history entry.
	if (wanted.href === now.href) return;
	write(wanted);
}

export function forgetAnchor(url: URL, path: string): void {
	if (url.pathname !== path || leaving()) return;
	const now = inTheBar(url);
	if (!now.searchParams.has(ANCHOR) && !now.searchParams.has(NEAR)) return;
	const wanted = new URL(now);
	wanted.searchParams.delete(ANCHOR);
	wanted.searchParams.delete(NEAR);
	write(wanted);
}

/** Not a navigation; `page.url` never moves, so the recorder (`noteAddress`) is told directly. */
function write(wanted: URL): void {
	replaceState(here(wanted), page.state);
	recordOnEntry();
	noteAddress(wanted);
}

const ROUTERS_RECORD = 'sveltekit:pageurl';

/** SvelteKit stamps the entry with the stale `page.url`; this copies the bar into the stamp. */
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
		// A browser that refuses history writes refused the one above.
	}
}

/** A path, since Sift is reached through proxies and an origin the router refuses. */
function here(url: URL): string {
	return `${url.pathname}${url.search}`;
}

/**
 * A page size and either where to start or which row to start AT, never both (`CardPaging.query`).
 */
export type PageAsk =
	{ limit: number; offset: number } | { limit: number; from: string; near?: number | null };

export function asked(page: PageAsk): Record<string, string> {
	if (!('from' in page)) return { limit: String(page.limit), offset: String(page.offset) };
	const ask: Record<string, string> = { limit: String(page.limit), from: page.from };
	if (page.near !== undefined && page.near !== null) ask.near = String(page.near);
	return ask;
}
