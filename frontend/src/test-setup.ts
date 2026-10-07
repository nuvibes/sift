/* What the unit environment is missing, supplied so a test can mount a component that moves.
 *
 * The environment these tests run in is a document without a renderer: it builds a DOM and answers
 * questions about it, and everything to do with painting is simply absent. `Element.animate` is one
 * of those: it is how a browser is asked to run an animation off the main thread, so an
 * implementation with no main thread and nothing to paint has no reason to carry it.
 *
 * That matters here because the framework's own transitions are built on it. Any component with an
 * arrival or a departure declared on it (a toast, a dialog, the suggestions under the search box)
 * throws the moment it renders, and the failure names the transition rather than the missing
 * method, which sends whoever hits it looking in the wrong file.
 *
 * So it is stood in for, and the stand-in finishes immediately. That is the honest behaviour for a
 * place with no frames: there is no time passing to animate over, and a test asking what is on
 * screen wants the answer the animation was heading towards rather than a halfway state that only
 * exists between two paints. Anything that genuinely depends on the movement itself is measured in
 * a real browser, where it is real.
 */

import { afterAll, afterEach, vi } from 'vitest';

/*
 * The address, and moving to one, stood in for the whole run.
 *
 * Every paged screen in Sift reads where it is from the address: it carries the row a wall
 * was opened at, so a link opens where the sender was. That makes `$app/state` and `$app/navigation`
 * ordinary dependencies of an ordinary component rather than something only a route touches.
 *
 * Neither exists here. Without a stand-in, mounting one of those screens fails in a way that names
 * neither: `page` is undefined, so reading `url.pathname` throws inside an effect, and the run
 * reports an unhandled rejection about a property called `hash` on a file that did not cause it.
 *
 * A default rather than a rule. A test that cares which address it is on declares its own
 * `vi.mock` for these, which takes precedence over anything here, as several files do.
 */
vi.mock('$app/state', () => ({
	page: { url: new URL('http://localhost/'), state: {}, params: {} },
	// `navigating.to` is read before the address is rewritten: a page position is never worth
	// landing on top of a move somebody has already started. A stand-in without it is not a
	// stand-in: the read throws, and the failure names `dispatchEvent` on a line that is fine.
	navigating: { to: null, from: null, type: null, complete: null, delta: null, willUnload: false }
}));
vi.mock('$app/navigation', () => ({
	goto: vi.fn(async () => {}),
	pushState: vi.fn(),
	replaceState: vi.fn(),
	invalidateAll: vi.fn(async () => {}),
	beforeNavigate: vi.fn(),
	afterNavigate: vi.fn()
}));

class FinishedAnimation implements Partial<Animation> {
	currentTime: number | null = 0;
	startTime: number | null = 0;
	playbackRate = 1;
	playState: AnimationPlayState = 'finished';
	pending = false;
	readonly finished: Promise<Animation>;
	onfinish: ((this: Animation, event: AnimationPlaybackEvent) => unknown) | null = null;
	oncancel: ((this: Animation, event: AnimationPlaybackEvent) => unknown) | null = null;
	effect: AnimationEffect | null = null;

	constructor() {
		this.finished = Promise.resolve(this as unknown as Animation);
		// A microtask rather than immediately: the caller is still inside `animate()` and has not
		// been handed this object yet, so a handler set on the next line would never be called.
		queueMicrotask(() => this.onfinish?.call(this as unknown as Animation, undefined!));
	}

	cancel(): void {
		this.playState = 'idle';
	}
	play(): void {}
	pause(): void {}
	finish(): void {}
	reverse(): void {}
	commitStyles(): void {}
	persist(): void {}
	updatePlaybackRate(rate: number): void {
		this.playbackRate = rate;
	}
	addEventListener(): void {}
	removeEventListener(): void {}
}

if (typeof Element !== 'undefined' && typeof Element.prototype.animate !== 'function') {
	Element.prototype.animate = function animate(): Animation {
		return new FinishedAnimation() as unknown as Animation;
	};
}

if (typeof Element !== 'undefined' && typeof Element.prototype.getAnimations !== 'function') {
	Element.prototype.getAnimations = function getAnimations(): Animation[] {
		return [];
	};
}

/*
 * A ResizeObserver that reports nothing, because jsdom has none at all.
 *
 * Svelte compiles `bind:clientWidth` and `bind:clientHeight` into one, so a component that asks how
 * big it is throws on construction here: the whole component fails to render, and the failure
 * names the closing `</style>` tag rather than the binding, which is not a trail anybody follows.
 *
 * It reports nothing rather than faking a size on purpose: nothing in this environment HAS a size,
 * so a made-up one would be a number tests could come to depend on. A component measuring itself is
 * a component whose sizes must be checked in a browser, and there is a browser suite for that.
 */
if (typeof globalThis.ResizeObserver === 'undefined') {
	globalThis.ResizeObserver = class {
		observe(): void {}
		unobserve(): void {}
		disconnect(): void {}
	} as unknown as typeof ResizeObserver;
}

/*
 * No unit test reaches the network.
 *
 * Node carries a real `fetch`, and the address the client builds is a real one on this machine, so
 * without this a request no test answered goes OUT: it comes back later as a 404 from whatever
 * listens there, or as a refused connection on a runner, and lands in whichever test is running
 * by then. A test can pass because a request is still pending, and a coverage count can move
 * between two identical trees.
 *
 * So the global `fetch` refuses every request, naming it, and the refusal is also KEPT: the client
 * turns any failed `fetch` into its "couldn't be reached" sentence and a screen shows that quietly, so a
 * rejection alone would be swallowed exactly where it matters. The test that made the request
 * fails in `afterEach`, naming what it asked for. A test answers what it reads, with a
 * `vi.mock` of the client or its own `fetch`, and a test that means to show a dead server says so
 * by answering with a rejection of its own.
 */
export const NETWORK_REFUSED = 'A unit test reached the network';

const strays: string[] = [];

/** Addresses a test file said to answer as a server that is not there. See `noServerAt`. */
const absent = new Set<string>();

/**
 * Answer requests to these paths as a server that is not there, for the rest of the file.
 *
 * For a read a screen makes on the way past that the file is not about (the interface settings,
 * the learning paths): named here, so the file says what it leaves unanswered, and a request to
 * anything else still fails the test. Matched on the path alone, `/api/insights/path`, whatever
 * the query. The answer is what a real browser gives with nothing listening: a rejected `fetch`.
 */
export function noServerAt(...paths: string[]): void {
	for (const path of paths) absent.add(path);
}

/** One request as a person reads it: the method and the address. */
export function requestWords(input: RequestInfo | URL, init?: RequestInit): string {
	const address = addressOf(input);
	const method = init?.method ?? (input instanceof Request ? input.method : 'GET');
	return `${method.toUpperCase()} ${address}`;
}

function addressOf(input: RequestInfo | URL): string {
	return typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
}

/** The stand-in for `fetch`: refuses, and keeps what it refused for `afterEach` to report. */
export async function refuseTheNetwork(
	input: RequestInfo | URL,
	init?: RequestInit
): Promise<Response> {
	const said = requestWords(input, init);
	if (absent.has(new URL(addressOf(input), 'http://localhost').pathname)) {
		throw new TypeError(`Nothing is listening in this test: ${said}`);
	}
	strays.push(said);
	throw new Error(`${NETWORK_REFUSED}: ${said}. Answer it in the test.`);
}

/** Throw when any request went unanswered since the last look, naming every one; then forget them. */
export function refuseStrays(): void {
	const reached = strays.splice(0);
	if (reached.length === 0) return;
	throw new Error(
		`${NETWORK_REFUSED}, and nothing in the test answered:\n  ${reached.join('\n  ')}\n` +
			'Mock `$lib/api/client`, stub `fetch`, or name a read the file is not about in `noServerAt`.'
	);
}

globalThis.fetch = refuseTheNetwork as typeof fetch;

afterEach(() => {
	refuseStrays();
});

/*
 * A NOTE THIS FILE CANNOT ENFORCE, WRITTEN HERE BECAUSE EVERY TEST FILE IMPORTS IT.
 *
 * `mount()` without a matching `unmount()` leaves the component's EFFECTS alive for the rest of the
 * file. The host element being thrown away is not the same thing: the DOM node goes, the reactive
 * graph does not, so a screen an earlier test mounted is still listening.
 *
 * What that looks like when it bites is nothing like what it is. A later test presses one thing,
 * and the announcement that follows wakes every screen still mounted from every test above it
 * (one press, ten requests), so the assertion that fails is a COUNT, in a test that did nothing
 * wrong, and the number it reports goes up as the file grows. It shows as a test that passes
 * alone and fails in its file.
 *
 * The cure is the pattern the component tests here already use: keep the instance, and in
 * `afterEach` unmount it before removing the host.
 *
 *     let host: HTMLElement;
 *     let instance: Record<string, unknown> | null = null;
 *
 *     afterEach(() => {
 *         if (instance) void unmount(instance, { outro: false });
 *         instance = null;
 *         host?.remove();
 *     });
 *
 * `outro: false` because a departure transition in a place with no frames is a wait for a paint
 * that never comes.
 */

/* Let a dialog's scroll lock finish tidying up before the document is taken away.
 *
 * Closing a dialog does not restore the page's scrolling immediately. The component library
 * schedules that a couple of dozen milliseconds later, so that a dialog closing and another
 * opening in the same tick does not thrash the body's style. Nothing in a test waits for it.
 *
 * This environment is a document that is thrown away when the file finishes, and when the timer
 * lands after that it reaches for a `document` that no longer exists. Every test passes and the run
 * still fails, on a file that need not be the one that opened the dialog, which is what makes it
 * read as random. Waiting out the delay once per file is enough, and it costs milliseconds.
 */
const SCROLL_LOCK_CLEANUP_MS = 40;

afterAll(async () => {
	await new Promise((done) => setTimeout(done, SCROLL_LOCK_CLEANUP_MS));
	// A request made after the file's last test (a timer, a late effect) is reported here.
	refuseStrays();
	absent.clear();
});

/*
 * The component library reading its own derived after we have unmounted it.
 *
 * A full run prints `derived_inert` ("Reading a derived belonging to a now-destroyed effect may
 * result in stale values") hundreds of times across many test files. Given a stack, every one of
 * them ends the same way: a `setTimeout` the component library scheduled and never cancels,
 * landing after the component has gone and reading the box that holds the element reference.
 *
 *     at Object.get current [as current]  svelte-toolbelt/dist/box/box-extras.svelte.js
 *     at Timeout._onTimeout               bits-ui/dist/bits/utilities/dismissible-layer/...
 *
 * The library defers arming a dismissible layer by a millisecond, and re-checks the reference when
 * the timer lands; the check is exactly the read that warns. Nothing in Sift can clear that timer
 * and nothing in Sift can make the check happen sooner: a test cannot unmount a menu more slowly
 * than a timer it cannot see. The read itself is harmless: the reference is gone, so the library
 * returns without doing anything, and the warning only exists in a development build.
 *
 * So this is the one thing left: drop that warning, and only when the stack says the library read
 * it. What is kept is the part that matters: a `derived_inert` raised while OUR code is running
 * still prints, because our frames are in the stack and the second half of the test below refuses
 * it. Suppressing the message outright would be a line shorter and would hide the next real one,
 * and hundreds of lines of noise hide it just as well by drowning it.
 *
 * The honest limit: this proves who READ the destroyed derived, not who is to blame for it being
 * destroyed. A library timer reading a box we handed it is still the library's read, and that is
 * all this claims.
 */
const LIBRARY_TEARDOWN = /node_modules[/\\](bits-ui|svelte-toolbelt)[/\\]/;
const OUR_OWN_CODE = /[/\\]frontend[/\\]src[/\\]/;

/* The one frame that is in EVERY stack this reads and is evidence of nothing: this file's own.
 *
 * !! Without this, the filter suppresses nothing at all and looks right. The stack is taken inside
 * the replacement below, so the replacement is the innermost frame, and it lives under
 * `frontend/src`, which is precisely what `OUR_OWN_CODE` looks for. Every warning would read as
 * "our code did this" and go through, and the only sign would be a run that still prints them. */
const THIS_FILE = 'test-setup';

export function isLibraryTeardownDerivedRead(message: unknown, stack: string): boolean {
	if (typeof message !== 'string' || !message.includes('derived_inert')) return false;
	const raised = stack
		.split('\n')
		.filter((frame) => !frame.includes(THIS_FILE))
		.join('\n');
	return LIBRARY_TEARDOWN.test(raised) && !OUR_OWN_CODE.test(raised);
}

const warnAnyway = console.warn.bind(console);
console.warn = (...args: unknown[]) => {
	if (isLibraryTeardownDerivedRead(args[0], new Error().stack ?? '')) return;
	warnAnyway(...args);
};
