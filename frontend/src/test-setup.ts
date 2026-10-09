/* What the unit environment is missing, supplied so a test can mount a component that moves.
 * `Element.animate` is absent and the framework's transitions throw without it; the stand-in
 * finishes immediately, since nothing here has frames to animate over. */

import { afterAll, afterEach, vi } from 'vitest';

/* The address and navigation stood in; a test that cares declares its own `vi.mock`. */
vi.mock('$app/state', () => ({
	page: { url: new URL('http://localhost/'), state: {}, params: {} },
	// `navigating.to` is read before the address is rewritten, so a stand-in must carry it.
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
		// A microtask, so a handler the caller sets on the next line is still called.
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

/* A ResizeObserver that reports nothing: jsdom has none, and nothing here has a size to report. */
if (typeof globalThis.ResizeObserver === 'undefined') {
	globalThis.ResizeObserver = class {
		observe(): void {}
		unobserve(): void {}
		disconnect(): void {}
	} as unknown as typeof ResizeObserver;
}

/*
 * No unit test reaches the network: a stray request lands in whichever test runs later. `fetch`
 * refuses and keeps the refusal, since a screen swallows a failed fetch quietly; `afterEach` fails
 * the test that asked, naming it.
 */
export const NETWORK_REFUSED = 'A unit test reached the network';

const strays: string[] = [];

/** Addresses a test file said to answer as a server that is not there. See `noServerAt`. */
const absent = new Set<string>();

/** Answer requests to these paths, whatever the query, as a server that is not there. */
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
 * `mount()` without `unmount()` leaves the component's effects alive for the rest of the file, and
 * a later test fails on a count. Keep the instance and unmount it with `{ outro: false }` in
 * `afterEach` before removing the host.
 */

/* The component library restores scrolling a few dozen milliseconds after a dialog closes; a timer
 * landing after the document is gone fails the run on a random file. */
const SCROLL_LOCK_CLEANUP_MS = 40;

afterAll(async () => {
	await new Promise((done) => setTimeout(done, SCROLL_LOCK_CLEANUP_MS));
	// A request made after the file's last test (a timer, a late effect) is reported here.
	refuseStrays();
	absent.clear();
});

/*
 * A library timer reading its own derived after the component is unmounted prints `derived_inert`
 * hundreds of times, harmlessly. Dropped only when the stack says the library read it; one raised
 * while our code runs still prints.
 */
const LIBRARY_TEARDOWN = /node_modules[/\\](bits-ui|svelte-toolbelt)[/\\]/;
const OUR_OWN_CODE = /[/\\]frontend[/\\]src[/\\]/;

/* This file's own frame is in every stack and lives under `frontend/src`: without dropping it,
 * every warning reads as ours and nothing is suppressed. */
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
