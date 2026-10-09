/* The Logs tab's level filter: the choices are the log's own levels, and each one is what the
 * server is asked for. */
import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';

const get = vi.hoisted(() =>
	vi.fn(async () => ({
		lines: [],
		path: 'sift.log',
		size_bytes: 0,
		present: true,
		searched_bytes: 0,
		whole: true
	}))
);

vi.mock('$lib/api/client', () => ({
	ApiError: class extends Error {},
	api: { get }
}));

vi.mock('$lib/bridge', () => ({
	bridge: { canReadShellLog: () => false, shellLog: vi.fn(async () => null) }
}));

/* No app on the computer running Sift to read a log from: the library's log alone. */
vi.mock('$lib/desktop/server-shell', () => ({
	offersServer: () => false,
	readServerDesktop: async () => null,
	readServerAppLog: vi.fn()
}));

import ApplicationLog from './ApplicationLog.svelte';

/* jsdom has no pointer capture, and the select primitive releases it on the way down. */
for (const name of ['setPointerCapture', 'releasePointerCapture', 'hasPointerCapture'] as const) {
	if (!(name in Element.prototype)) {
		Object.defineProperty(Element.prototype, name, { value: () => false, writable: true });
	}
}

let host: HTMLElement;
let shown: ReturnType<typeof mount> | null = null;

beforeEach(async () => {
	get.mockClear();
	host = document.createElement('div');
	document.body.append(host);
	shown = mount(ApplicationLog, { target: host });
	flushSync();
	await vi.waitFor(() => expect(get).toHaveBeenCalled());
	await tick();
});

afterEach(() => {
	if (shown) unmount(shown);
	shown = null;
	host.remove();
	document.body.innerHTML = '';
});

function press(element: Element | null | undefined) {
	// The primitive opens and picks on pointer events, not on click alone.
	element?.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0 }));
	element?.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, button: 0 }));
	(element as HTMLElement | null | undefined)?.click();
	flushSync();
}

/** The label of each row, which is the row less the tick the chosen one wears. */
function offered(): string[] {
	press(host.querySelector('.ui-select'));
	return [...document.querySelectorAll('.ui-select-item-label')].map((one) =>
		one.textContent!.trim()
	);
}

async function choose(label: string) {
	press(host.querySelector('.ui-select'));
	const item = [...document.querySelectorAll('.ui-select-item-label')].find(
		(one) => one.textContent?.trim() === label
	);
	expect(item, `no choice labelled ${label}`).toBeTruthy();
	press(item?.closest('.ui-select-item'));
	await tick();
}

/** The level the last read of the library's log asked the server for. */
function askedFor(): unknown {
	const calls = get.mock.calls as unknown as [string, { query: Record<string, unknown> }][];
	const last = calls.at(-1);
	expect(last?.[0]).toBe('/logs');
	return last?.[1].query.level;
}

it('offers every level by its own name, loudest first, Critical above Error', () => {
	expect(offered()).toEqual(['Critical', 'Error', 'Warning', 'Info', 'Debug']);
});

it('starts on Debug, which narrows nothing and so asks for no level', () => {
	expect(host.querySelector('.ui-select')?.textContent).toContain('Debug');
	expect(askedFor()).toBeUndefined();
});

it('asks the server for exactly the level chosen', async () => {
	for (const [label, level] of [
		['Critical', 'critical'],
		['Error', 'error'],
		['Warning', 'warning'],
		['Info', 'info']
	] as const) {
		await choose(label);
		expect(askedFor()).toBe(level);
	}
	await choose('Debug');
	expect(askedFor()).toBeUndefined();
});

it("draws a day's heading as a band inside the log, never as a group heading with its hairline", () => {
	// Read from the source: the heading is pinned as the lines scroll, and a group heading there
	// would draw its hairline with a band of empty ground above it, inside a box that is not a page.
	const source = readFileSync('src/lib/settings-ui/ApplicationLog.svelte', 'utf8');
	expect(source).toMatch(/<div class="day"><SectionHeading band level=\{3\}>/);
});
