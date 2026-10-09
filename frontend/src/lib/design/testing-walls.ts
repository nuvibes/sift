/* Support for the tests, and only for the tests: a wall of cards mounted, measured and paged. */

import { afterEach, beforeEach, expect } from 'vitest';
import { flushSync, mount, unmount, type Component } from 'svelte';
import { forgetMeasurements } from '$lib/grid/cards.svelte';
import { measuring } from '$lib/grid/measuring';

/** The card every measured wall lays out: 8 to a page in the 1200px box. */
export const CARD = { width: 600, height: 300 };
/** Cards to a measured page. */
export const MEASURED = 8;
/** The stand-in library: two whole pages and one row more. */
export const WHOLE = 2 * MEASURED + 1;
/** Where Last lands in a list of `WHOLE`, and the first row of that page. */
export const LAST_PAGE = 2 * MEASURED;
/** The library a first-page test opens: more than a measured page, less than an unmeasured one. */
export const SHORT = 12;

/** The address a round-trip test arrives at: the row, AND where it was (`near`). */
export function arrival(path: string, prefix: string, position: number): string {
	return `${path}?from=${prefix}${position}&near=${position}`;
}

/** What the stand-in server was asked, and how long a list it answers from. */
interface WallServer {
	asks: Record<string, unknown>[];
	whole: number;
}

/** A wall whose rows live in a module singleton rather than in the page. */
interface HeldRows {
	forget(): void;
	loading: boolean;
	at: number;
}

/** The stand-ins a wall test's mocks read. Each file declares them with `vi.hoisted`, because its
 * `vi.mock` factories run before any import, this one included, and hands them over here. */
interface WallStandIns {
	server: WallServer;
	router: { replaced: string[] };
	at: { url: URL };
}

/** Mount `Wall` over the stand-ins, and clear everything a test could leave for the next one. */
export function wallHarness(Wall: Component, standIns: WallStandIns, rows: HeldRows | null) {
	const { server, router, at } = standIns;
	let host: HTMLElement | null = null;
	let mounted: Record<string, unknown> | null = null;
	/** The browser laying the wall out, when a test asked for it; put back after every test. */
	let browser: ReturnType<typeof measuring> | null = null;

	/* The rows live in a module singleton, so one visit's page would otherwise still be held by
	 * the next one: a wall holding a page it did not ask for asks for nothing, and a wall that
	 * still holds cards measures one the moment it attaches, so its size comes from the card
	 * rather than from the memory a "next visit" test is about. */
	function forgetRows() {
		if (!rows) return;
		rows.forget();
		rows.loading = false;
		rows.at = 0;
	}

	beforeEach(() => {
		server.asks = [];
		server.whole = WHOLE;
		router.replaced = [];
		// What a wall measured is kept for the session, like its rows: left standing, one test's
		// measurement sizes the next test's pages, and a test counting what an unmeasured wall asks
		// for counts a remembered size instead.
		forgetMeasurements();
		forgetRows();
		Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
			configurable: true,
			get: () => 1200
		});
		Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
			configurable: true,
			get: () => 900
		});
	});

	/** Take the wall off the document, as leaving the page does. */
	function unmountWall() {
		if (mounted) void unmount(mounted);
		mounted = null;
		host?.remove();
		host = null;
	}

	afterEach(() => {
		unmountWall();
		browser?.done();
		browser = null;
		window.history.replaceState({}, '', '/');
	});

	async function settle() {
		for (let round = 0; round < 6; round += 1) {
			flushSync();
			await Promise.resolve();
		}
		flushSync();
	}

	/** The wall opened at `address`, with nothing measured. */
	async function wall(address: string): Promise<HTMLElement> {
		window.history.replaceState({}, '', address);
		at.url = new URL(`${window.location.origin}${address}`);
		const drawn = document.createElement('div');
		host = drawn;
		document.body.append(drawn);
		mounted = mount(Wall, { target: drawn, props: {} });
		await settle();
		await settle();
		return drawn;
	}

	/** The wall at an address with its first cards measured, so every page it turns to holds 8. */
	async function measuredWall(address: string, card = CARD): Promise<HTMLElement> {
		browser = measuring(card);
		const drawn = await wall(address);
		browser.deliver();
		await settle();
		await settle();
		return drawn;
	}

	/** Between two visits: the wall leaves, the rows it held go with it, and the count of what
	 * the server was asked starts again. */
	function leave() {
		unmountWall();
		forgetRows();
		server.asks = [];
	}

	/** A page control, by what it says to a screen reader. */
	function pager(named: string): HTMLButtonElement {
		const found = [...(host?.querySelectorAll('button') ?? [])].find((one) =>
			`${one.getAttribute('aria-label') ?? ''} ${one.textContent ?? ''}`
				.toLowerCase()
				.includes(named)
		);
		expect(found, `no ${named} control on the wall`).toBeTruthy();
		return found as HTMLButtonElement;
	}

	async function press(named: string) {
		pager(named).click();
		await settle();
		await settle();
	}

	return { wall, measuredWall, leave, press, settle };
}
