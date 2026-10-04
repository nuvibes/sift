/* General from ANOTHER computer: the app in client mode (or a browser) draws a group about the
 * computer running Sift, and every press in it asks the SERVER, never this window's bridge.
 *
 * The server is stubbed at `server-shell`, the one module that asks it, so what is proved here is
 * what the pane draws from the server's answer and which door each press goes through. */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import General from './General.svelte';
import { COPY } from './General.search';
import { NETWORK_SHARING } from './NetworkSharing.search';

const startsWithWindows = vi.hoisted(() => vi.fn());
const setStartsWithWindows = vi.hoisted(() => vi.fn());
const restartApp = vi.hoisted(() => vi.fn(async () => true));
const readServerDesktop = vi.hoisted(() => vi.fn());
const setServerStartsWithWindows = vi.hoisted(() => vi.fn());
const restartServer = vi.hoisted(() => vi.fn());
const readServerFirewall = vi.hoisted(() => vi.fn());
const openServerFirewall = vi.hoisted(() => vi.fn());
const setServerSharing = vi.hoisted(() => vi.fn());
const followSwitch = vi.hoisted(() => vi.fn());

/* The app in client mode: the verbs a window onto another computer has, and none of the local ones.
   How THIS window behaves (links, the close button, starting with Windows) is among them: those are
   this computer's own. */
vi.mock('$lib/bridge', () => ({
	bridge: {
		canChooseBrowser: () => true,
		canKeepRunningWhenClosed: () => true,
		canStartWithWindows: () => true,
		startsWithWindows,
		setStartsWithWindows,
		canForgetMode: () => true,
		forgetMode: vi.fn(async () => true),
		canRestartApp: () => false,
		restartApp,
		canShareOnNetwork: () => false,
		browsers: vi.fn(async () => ({ chosen: null, browsers: [] })),
		keepRunningWhenClosed: vi.fn(async () => null)
	}
}));

vi.mock('$lib/desktop/server-shell', () => ({
	offersServer: (desk: { has_app?: boolean } | null) => desk?.has_app === true,
	readServerDesktop,
	setServerStartsWithWindows,
	restartServer,
	thisDevice: async () => 'LAPTOP-TWO',
	readServerFirewall,
	openServerFirewall,
	setServerSharing
}));

vi.mock('$lib/shell/health', () => ({ serverBootId: async () => 'run-1' }));
vi.mock('./follow-switch', () => ({ followSwitch }));

vi.mock('$lib/shell/interface-state.svelte', () => ({
	askBeforeChipRemove: vi.fn(),
	chipRemoveSkipped: () => false,
	recallInterfaceState: async () => {},
	skipChipRemoveConfirm: vi.fn()
}));

vi.mock('$lib/shell/toasts.svelte', () => ({ toasts: { show: vi.fn() } }));

const SERVER = {
	has_app: true,
	machine: 'DESK-ONE',
	starts_with_windows: false,
	sharing: { enabled: true, live: true, address: 'http://192.168.1.20:5171', port: 5171 }
};

let host: HTMLDivElement;
let shown: Record<string, unknown> | null = null;

beforeEach(() => {
	startsWithWindows.mockResolvedValue(true);
	setStartsWithWindows.mockImplementation(async (on: boolean) => on);
	readServerDesktop.mockResolvedValue(SERVER);
	setServerStartsWithWindows.mockImplementation(async (on: boolean) => ({
		...SERVER,
		starts_with_windows: on
	}));
	readServerFirewall.mockResolvedValue({ state: 'closed', networks: ['Private'], scope: null });
	openServerFirewall.mockResolvedValue({ state: 'open', networks: ['Private'], scope: 'private' });
	setServerSharing.mockResolvedValue({ ok: true, refusal: null });
	followSwitch.mockResolvedValue(false);
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (shown) unmount(shown);
	shown = null;
	host.remove();
	vi.clearAllMocks();
});

async function settle() {
	for (let turn = 0; turn < 8; turn += 1) await Promise.resolve();
	flushSync();
}

async function draw() {
	shown = mount(General, { target: host });
	await settle();
	return host;
}

function group(): HTMLElement | null {
	return host.querySelector('[id="general.server"]')?.closest('section') ?? null;
}

it('draws the computer running Sift by its name, with its own switch and restart', async () => {
	await draw();

	expect(group()?.textContent).toContain(COPY.server.heading);
	expect(group()?.textContent).toContain(COPY.server.help('DESK-ONE'));
	expect(group()?.textContent).toContain(COPY.startWithWindows.name);
	expect(group()?.textContent).toContain(COPY.restart.label);
	expect(group()?.querySelector('[role="switch"]')?.getAttribute('aria-checked')).toBe('false');
});

it('turns start with Windows on THERE, and never asks this window', async () => {
	await draw();

	(group()?.querySelector('[role="switch"]') as HTMLElement).click();
	await settle();

	expect(setServerStartsWithWindows).toHaveBeenCalledWith(true);
	expect(setStartsWithWindows).not.toHaveBeenCalled();
	expect(group()?.querySelector('[role="switch"]')?.getAttribute('aria-checked')).toBe('true');
});

it('restarts Sift on that computer and says so while it waits', async () => {
	let finish: (outcome: { ok: false; problem: string }) => void = () => {};
	restartServer.mockReturnValue(new Promise((resolve) => (finish = resolve)));
	await draw();

	const press = [...(group()?.querySelectorAll('button') ?? [])].find((one) =>
		one.textContent?.includes(COPY.restart.action)
	);
	press?.click();
	await settle();

	expect(restartServer).toHaveBeenCalledOnce();
	expect(restartApp).not.toHaveBeenCalled();
	expect(group()?.textContent).toContain(COPY.server.restarting('DESK-ONE'));

	finish({ ok: false, problem: COPY.server.slow });
	await settle();
	expect(group()?.textContent).toContain(COPY.server.slow);
	expect(group()?.textContent).not.toContain(COPY.server.restarting('DESK-ONE'));
});

it('opens the firewall there, and says it is approved at that computer', async () => {
	await draw();

	expect(group()?.textContent).toContain(COPY.server.firewall.closed);
	expect(group()?.textContent).toContain(COPY.server.firewall.approveThere);
	const press = [...(group()?.querySelectorAll('button') ?? [])].find((one) =>
		one.textContent?.includes(COPY.server.firewall.action)
	);
	press?.click();
	await settle();

	expect(openServerFirewall).toHaveBeenCalledWith('private');
	expect(group()?.textContent).toContain(COPY.server.firewall.open(5171));
});

it('draws nothing about the server where no app runs Sift there, or the viewer cannot ask', async () => {
	readServerDesktop.mockResolvedValue({
		has_app: false,
		machine: null,
		starts_with_windows: null,
		sharing: null
	});
	await draw();
	expect(group()).toBeNull();

	unmount(shown as Record<string, unknown>);
	readServerDesktop.mockResolvedValue(null);
	await draw();
	expect(group()).toBeNull();
});

function sharingSwitch(): HTMLElement {
	const found = [...(group()?.querySelectorAll('[role="switch"]') ?? [])].find(
		(one) => one.getAttribute('aria-label') === NETWORK_SHARING.name
	);
	if (!found) throw new Error('no sharing switch in the group');
	return found as HTMLElement;
}

function pressInPage(words: string) {
	const press = [...document.body.querySelectorAll('button')].find(
		(one) => one.textContent?.trim() === words
	);
	if (!press) throw new Error(`no button saying ${words}`);
	press.click();
}

it('asks before turning sharing off there, and says this window is cut off afterwards', async () => {
	await draw();

	expect(sharingSwitch().getAttribute('aria-checked')).toBe('true');
	sharingSwitch().click();
	await settle();

	expect(document.body.textContent).toContain(COPY.server.sharing.offSays('DESK-ONE'));
	expect(setServerSharing).not.toHaveBeenCalled();

	pressInPage(COPY.server.sharing.offConfirm);
	await settle();

	expect(setServerSharing).toHaveBeenCalledWith(false);
	expect(group()?.textContent).toContain(COPY.server.sharing.cutOff('DESK-ONE'));
	expect(followSwitch).toHaveBeenCalledOnce();
});

it('says the refusal of the app there in its words, and puts the switch back', async () => {
	setServerSharing.mockResolvedValue({ ok: false, refusal: 'Not running its own library.' });
	await draw();

	sharingSwitch().click();
	await settle();
	pressInPage(COPY.server.sharing.offConfirm);
	await settle();

	expect(group()?.textContent).toContain('Not running its own library.');
	expect(sharingSwitch().getAttribute('aria-checked')).toBe('true');
	expect(followSwitch).not.toHaveBeenCalled();
});

it('puts the sharing switch back on when the question is closed without stopping', async () => {
	await draw();

	sharingSwitch().click();
	await settle();
	pressInPage('Cancel');
	await settle();

	expect(setServerSharing).not.toHaveBeenCalled();
	expect(sharingSwitch().getAttribute('aria-checked')).toBe('true');
});

/* A setting about how a window behaves changes the app on the computer the window is on. In
   client mode that is this computer, named above its rows, and its switch is this shell's;
   the server's own switch stays in the server's group, asked of the server. */
it('names this computer above its own rows, and its switch changes this computer only', async () => {
	await draw();

	const mine = host.querySelector('[id="general.this_window"]')?.closest('section');
	expect(mine?.textContent).toContain(COPY.thisWindow.heading('LAPTOP-TWO'));
	expect(mine?.textContent).toContain(COPY.thisWindow.help);
	/* Two switches of one name, each under the computer it means. */
	const named = [...host.querySelectorAll('[role="switch"]')].filter(
		(one) => one.getAttribute('aria-label') === COPY.startWithWindows.name
	);
	expect(named).toHaveLength(2);
	const theirs = group()?.querySelector(
		`[role="switch"][aria-label="${COPY.startWithWindows.name}"]`
	);
	const ours = named.find((one) => one !== theirs) as HTMLElement;
	expect(ours.getAttribute('aria-checked')).toBe('true');

	ours.click();
	await settle();

	expect(setStartsWithWindows).toHaveBeenCalledWith(false);
	expect(setServerStartsWithWindows).not.toHaveBeenCalled();
});
