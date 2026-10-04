/* Support for the tests, and only for the tests: a wall of cards mounted, measured and paged.
 *
 * Five walls (Sites, Photo Sets, Collections, Tags, People) are tested the same way: a stand-in
 * server that names every row after its position, a stand-in router that moves the bar, and the
 * wall mounted over them. A copy of the arithmetic below and of the mounting in each file would
 * drift in ways that leave a test unable to fail: next-visit tests keeping the rows between the
 * visits (see `leave`), round trips arriving without `near` (see `arrival`). So the arithmetic, the mounting and the clearing live here
 * once, and a file keeps only what is its own: its wall, its rows, and the tests that are not the
 * common shape.
 *
 * Nothing in the app imports this, and it is not in the bundle.
 *
 * ## THE FEWEST CARDS THAT SHOW IT
 *
 * Every card drawn is paid for in a document that lays nothing out: a whole wall card costs a few
 * milliseconds here and several times that on a two-processor runner measuring coverage, where tests
 * drawing the unmeasured page of 60 can run close to or past the five-second limit with every
 * request answered. So the stand-in library is short, and a test that turns pages measures its cards first
 * (`measuredWall`), so each page it turns to holds 8.
 *
 * The arithmetic, once: 600px cards (`CARD`) in the 1200px box, one row to a screen (`measuring`
 * answers every box at the card's size), are 2 x 1 x 4 screens = 8 to a page (`MEASURED`). A list of
 * 17 (`WHOLE`) is two whole pages and one row more, so its pages begin at 0, 8 and 16, and Last lands
 * on floor((17 - 1) / 8) x 8 = 16 (`LAST_PAGE`): the first row of the last page, which is the row the
 * round-trip tests name, so coming back to it means something.
 *
 * A test that counts first-page requests opens a dozen rows instead (`SHORT`): more than one
 * measured page, so the measurement still has rows to trim, and fewer than the unmeasured page,
 * which is still what the first request asks for. Which way `CardPaging.fill` answers a smaller page
 * is its own file's business (`cards-first-page.svelte.test.ts`); these prove the wall is wired to it.
 */

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

/**
 * The address a round-trip test arrives at: the row, AND where it was (`near`).
 *
 * Both halves a wall writes, as a link copied from the bar carries them. Arriving with the row alone,
 * the address written on the way back differs from the arrival by `near` and nothing else, so a wall
 * comparing against the address it ARRIVED at still writes, and the round trip passes with that
 * fault in.
 *
 * The stand-in server names a row after its position (`place16` is the 17th Site), so the row and
 * `near` carry the same number.
 */
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

/**
 * The stand-ins a wall test's mocks read. Each file declares them with `vi.hoisted`, because its
 * `vi.mock` factories run before any import, this one included, and hands them over here.
 */
interface WallStandIns {
	server: WallServer;
	router: { replaced: string[] };
	at: { url: URL };
}

/**
 * Mount `Wall` over the stand-ins, and clear everything a test could leave for the next one.
 *
 * `rows` is the store the wall keeps its rows in, or null for a wall whose rows live in the page
 * (Photo Sets), which unmounting already drops.
 *
 * Registers its own `beforeEach` and `afterEach`; call it once, at the top level of the file.
 */
export function wallHarness(Wall: Component, standIns: WallStandIns, rows: HeldRows | null) {
	const { server, router, at } = standIns;
	let host: HTMLElement | null = null;
	let mounted: Record<string, unknown> | null = null;
	/** The browser laying the wall out, when a test asked for it; put back after every test. */
	let browser: ReturnType<typeof measuring> | null = null;

	/*
	 * The rows live in a module singleton, so one visit's page would otherwise still be held by the
	 * next one: a wall holding a page it did not ask for asks for nothing, and a wall that still holds
	 * cards measures one the moment it attaches, so its size comes from the card rather than from the
	 * memory a "next visit" test is about. Left in place between visits, that test would pass for a
	 * wall that forgot its measurement.
	 */
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

	/**
	 * Between two visits: the wall leaves, the rows it held go with it, and the count of what the
	 * server was asked starts again. What the wall MEASURED stays, which is what a next visit tests.
	 */
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
