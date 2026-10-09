/* General: what the application does on this device, each row drawn only where the shell
 * answers, and the choices about the whole app that belong to no one feature. */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import General from './General.svelte';
import { COPY } from './General.search';

const canChooseBrowser = vi.hoisted(() => vi.fn(() => false));
const canKeepRunningWhenClosed = vi.hoisted(() => vi.fn(() => false));
const canStartWithWindows = vi.hoisted(() => vi.fn(() => true));
const startsWithWindows = vi.hoisted(() => vi.fn());
const setStartsWithWindows = vi.hoisted(() => vi.fn());
const canForgetMode = vi.hoisted(() => vi.fn(() => false));
const forgetMode = vi.hoisted(() => vi.fn(async () => true));
const canRestartApp = vi.hoisted(() => vi.fn(() => false));
const restartApp = vi.hoisted(() => vi.fn(async () => true));
const show = vi.hoisted(() => vi.fn());

vi.mock('$lib/bridge', () => ({
	bridge: {
		canChooseBrowser,
		canKeepRunningWhenClosed,
		canStartWithWindows,
		startsWithWindows,
		setStartsWithWindows,
		canForgetMode,
		forgetMode,
		canRestartApp,
		restartApp,
		canShareOnNetwork: () => false,
		browsers: vi.fn(async () => ({ chosen: null, browsers: [] })),
		keepRunningWhenClosed: vi.fn(async () => null)
	}
}));

vi.mock('$lib/shell/interface-state.svelte', () => ({
	askBeforeChipRemove: vi.fn(),
	chipRemoveSkipped: () => false,
	recallInterfaceState: async () => {},
	skipChipRemoveConfirm: vi.fn()
}));

vi.mock('$lib/shell/toasts.svelte', () => ({ toasts: { show } }));

/* The computer running Sift is asked about from another computer only; this file is about this
   device's own rows, so the server answers that it cannot be asked
   (`General.server.svelte.test.ts` draws the other case). */
vi.mock('$lib/desktop/server-shell', () => ({
	offersServer: () => false,
	readServerDesktop: async () => null,
	thisDevice: async () => 'LAPTOP-TWO'
}));

let host: HTMLDivElement;
let shown: Record<string, unknown> | null = null;

beforeEach(() => {
	canChooseBrowser.mockReturnValue(false);
	canKeepRunningWhenClosed.mockReturnValue(false);
	canStartWithWindows.mockReturnValue(true);
	canForgetMode.mockReturnValue(false);
	canRestartApp.mockReturnValue(false);
	startsWithWindows.mockResolvedValue(false);
	setStartsWithWindows.mockImplementation(async (on: boolean) => on);
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (shown) unmount(shown);
	shown = null;
	host.remove();
	vi.clearAllMocks();
});

async function draw() {
	shown = mount(General, { target: host });
	for (let turn = 0; turn < 6; turn += 1) await Promise.resolve();
	flushSync();
	return host;
}

it('says where the device rows are in a browser, and asks the shell nothing', async () => {
	canStartWithWindows.mockReturnValue(false);

	await draw();

	expect(host.textContent).toContain(COPY.inABrowser);
	expect(startsWithWindows).not.toHaveBeenCalled();
});

it('draws the start-with-Windows row from what the shell says, off by default', async () => {
	await draw();

	const row = host.querySelector('[id="general.start_with_windows"]');
	expect(row, 'the row carries its address, so a link can ring it').not.toBeNull();
	expect(host.textContent).toContain(COPY.startWithWindows.name);
	expect(host.querySelector('[role="switch"]')?.getAttribute('aria-checked')).toBe('false');
});

it('writes a change through the shell', async () => {
	await draw();

	(host.querySelector('[role="switch"]') as HTMLElement).click();
	for (let turn = 0; turn < 6; turn += 1) await Promise.resolve();
	flushSync();

	expect(setStartsWithWindows).toHaveBeenCalledWith(true);
});

it('is not drawn by a shell that cannot answer it', async () => {
	canStartWithWindows.mockReturnValue(false);
	canKeepRunningWhenClosed.mockReturnValue(true);

	await draw();

	expect(host.textContent).not.toContain(COPY.startWithWindows.name);
	expect(host.textContent).not.toContain(COPY.inABrowser);
});

/* The confirmations are about the whole app, so they are drawn in a browser as much as in the
   app, under the row ids they had on Editing, so an old link still rings them. */
it('draws the two confirmations everywhere, under their old addresses', async () => {
	canStartWithWindows.mockReturnValue(false);

	await draw();

	expect(host.textContent).toContain(COPY.confirmations);
	expect(host.querySelector('[id="editing.delete.ask"] [role="switch"]')).not.toBeNull();
	expect(host.querySelector('[id="editing.remove.ask"] [role="switch"]')).not.toBeNull();
});

it('offers setup again, and says what pressing it does', async () => {
	canForgetMode.mockReturnValue(true);

	await draw();

	const row = host.querySelector('[id="general.run_setup"]');
	expect(row?.textContent).toContain(COPY.again.label);
	expect(row?.textContent).toContain(COPY.again.help);
	row?.querySelector<HTMLElement>('button')?.click();
	for (let turn = 0; turn < 6; turn += 1) await Promise.resolve();

	expect(forgetMode).toHaveBeenCalled();
	expect(show).toHaveBeenCalledWith(COPY.again.done, { tone: 'success' });
});

/* Restart follows Run setup again, and only where the shell can restart itself. */
it('restarts through the shell where it can, and says to reopen Sift where it cannot', async () => {
	canForgetMode.mockReturnValue(true);
	canRestartApp.mockReturnValue(true);

	await draw();

	const rows = [...host.querySelectorAll('[id^="general."]')].map((one) => one.id);
	expect(rows.indexOf('general.restart')).toBeGreaterThan(rows.indexOf('general.run_setup'));
	host.querySelector<HTMLElement>('[id="general.restart"] button')?.click();
	for (let turn = 0; turn < 6; turn += 1) await Promise.resolve();
	expect(restartApp).toHaveBeenCalledTimes(1);

	unmount(shown!);
	shown = null;
	canRestartApp.mockReturnValue(false);
	await draw();

	expect(host.querySelector('[id="general.restart"]')).toBeNull();
	expect(host.textContent).toContain(COPY.restart.byHand);
});

it('offers neither where the shell cannot forget how it was set up', async () => {
	await draw();

	expect(host.querySelector('[id="general.library_location"]')).toBeNull();
	expect(host.textContent).not.toContain(COPY.restart.label);
});

it('draws the browser note as the first group, so the next heading keeps its gap', async () => {
	canStartWithWindows.mockReturnValue(false);

	await draw();

	const note = [...host.querySelectorAll('.group')].find((one) =>
		one.textContent?.includes(COPY.inABrowser)
	);
	expect(note?.id).toBe('general.in_a_browser');
});

/* Unmounted by a reader TypeScript cannot narrow away: `draw` sets it behind the checker's back. */
function clear() {
	const drawn = shown as Record<string, unknown> | null;
	if (drawn) unmount(drawn);
	shown = null;
}

/* The heading naming this computer is for the app on ANOTHER computer only: the app on the
   computer running Sift has nothing to tell apart, and a browser draws none of these rows. */
it('names this computer only in the app on a computer that is not running Sift', async () => {
	await draw();
	expect(host.querySelector('[id="general.this_window"]')?.textContent).toContain(
		COPY.thisWindow.heading('LAPTOP-TWO')
	);
	clear();

	canRestartApp.mockReturnValue(true);
	await draw();
	expect(host.querySelector('[id="general.this_window"]')).toBeNull();
	clear();

	canStartWithWindows.mockReturnValue(false);
	canRestartApp.mockReturnValue(false);
	await draw();
	expect(host.querySelector('[id="general.this_window"]')).toBeNull();
});

it('answers a link to the group about the computer running Sift by saying where it is changed', async () => {
	host.className = 'section-body';
	await draw();
	expect(host.querySelector('[id="general.server"]')).toBeNull();
	const { revealSetting } = await import('./settings-anchor.svelte');

	await expect(revealSetting('general.server')).resolves.toBe(false);
	expect(show).toHaveBeenCalledWith(
		'The computer running Sift is changed from here only while the Sift app is open on it and you are on another computer.'
	);
});
