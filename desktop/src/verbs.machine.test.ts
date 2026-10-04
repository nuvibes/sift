/* The verbs about this machine and the network: the address another computer types, this
 * machine's hardware and name, the connect screen, the firewall, the version, the window buttons,
 * and offering the library.
 *
 * The stand-ins below are the ones `verbs.test.ts` explains, one per module that would otherwise
 * reach the real machine.
 */

import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const FAKE_ADDON = path.resolve(__dirname, '..', 'test', 'fake-drag-addon.cjs');
vi.mock('./paths', async (importOriginal) => ({
	...(await importOriginal<typeof import('./paths')>()),
	dragAddonFile: () => FAKE_ADDON
}));

vi.mock('./firewall', () => ({
	firewallState: vi.fn(async (port: number) => `read:${port}` as unknown as string),
	openFirewall: vi.fn(async (port: number) => `open:${port}` as unknown as string)
}));

vi.mock('./transfers', () => ({
	transfers: () => ({
		start: async (
			_url: string,
			_headers: Record<string, string>,
			at: { partial: string; finished: string },
			total: number | null,
			onProgress: (received: number, total: number | null) => void
		) => {
			fs.mkdirSync(path.dirname(at.finished), { recursive: true });
			fs.writeFileSync(at.finished, 'data');
			onProgress(total ?? 4, total);
			return at.finished;
		}
	})
}));

const BROWSERS = [
	{ id: 'C:\\Program Files\\Bluebird\\bluebird.exe', name: 'Bluebird' },
	{ id: 'C:\\Program Files\\Kestrel\\kestrel.exe', name: 'Kestrel' }
];
vi.mock('./browsers', () => ({
	installed: vi.fn(async () => BROWSERS)
}));

const SHELL_LOG_ANSWER = {
	lines: ['one line'],
	path: 'C:\\Sift\\shell.log',
	size: 9,
	present: true
};
vi.mock('./log', async (importOriginal) => ({
	...(await importOriginal<typeof import('./log')>()),
	tail: vi.fn(() => SHELL_LOG_ANSWER)
}));

vi.mock('./update', async (importOriginal) => ({
	...(await importOriginal<typeof import('./update')>()),
	applyUpdate: vi.fn(async () => ({ ok: true, version: '0.1.157' }))
}));

type StubInterface = { family: string; internal: boolean; address: string };
const machineInterfaces: Record<string, StubInterface[] | undefined> = {};
vi.mock('node:os', async (importOriginal) => ({
	...(await importOriginal<typeof import('node:os')>()),
	networkInterfaces: () => machineInterfaces
}));

// eslint-disable-next-line @typescript-eslint/no-require-imports
const addon = require(FAKE_ADDON) as {
	calls: {
		verb: string;
		path?: string;
		partial?: string;
		finished?: string;
		name?: string;
		total?: number;
	}[];
};

import {
	app,
	BrowserWindow,
	ipcRenderer,
	paths,
	resetElectronStub,
	sender,
	setVersion
} from '../test/electron-stub';
import * as channels from './channels';
import { GET_SHARING, SET_SHARING } from './channels';
import { log } from './log';
import type { Reach } from './origins';
import type { LocalMachine } from './machine';

import { applyUpdate } from './update';
import {
	CONNECT_STATE,
	FORGET_SERVER,
	GET_FIREWALL,
	LOCAL_HARDWARE,
	OPEN_FIREWALL,
	SET_TITLE_BAR,
	SHELL_VERSION,
	networkAddress,
	preferredAddress,
	registerVerbs
} from './verbs';

const TRUSTED = 'http://127.0.0.1:5171';

const localAt =
	(origin: string) =>
	(url: string): Reach | null =>
		url.startsWith(origin) ? 'local' : null;
const remoteAt =
	(origin: string) =>
	(url: string): Reach | null =>
		url.startsWith(origin) ? 'remote' : null;

let temp: string;

beforeEach(() => {
	resetElectronStub();
	temp = fs.mkdtempSync(path.join(os.tmpdir(), 'sift-verbs-'));
	paths.temp = temp;
	addon.calls.length = 0;
	registerVerbs(localAt(TRUSTED));
});

afterEach(() => {
	fs.rmSync(temp, { recursive: true, force: true });
});

describe('the address to type on the other computer', () => {
	/* A machine running a VPN client has two: an address in the carrier range and the one the
	 * person knows their computer by. The first one the operating system lists is often the VPN
	 * one: an address their other machine on the same switch cannot reach.
	 */
	it('prefers a home-network address over anything else on offer', () => {
		expect(preferredAddress(['100.64.0.7', '192.168.1.41'])).toBe('192.168.1.41');
		/* Anything that is not four numbers is no home-network address, whatever it starts with. */
		expect(preferredAddress(['10.0.0', '192.168.x.1', '100.64.0.7'])).toBe('10.0.0');
		expect(preferredAddress(['10.0.0', '192.168.1.5'])).toBe('192.168.1.5');
		expect(preferredAddress(['169.254.5.5', '192.168.1.20'])).toBe('192.168.1.20');
		expect(preferredAddress(['100.64.0.7', '172.16.0.4'])).toBe('172.16.0.4');
	});

	it('takes what there is when none of them is a home-network address', () => {
		// Better a reachable-looking address than nothing at all: the screen says where to check it.
		expect(preferredAddress(['100.64.0.7'])).toBe('100.64.0.7');
	});

	it('answers nothing when the machine is not on a network', () => {
		expect(preferredAddress([])).toBe(null);
	});

	/* 172.15 and 172.32 are OUTSIDE the private range and 172.16-31 are inside it, which is the
	 * boundary every hand-written check of this gets wrong in one direction or the other. */
	it('gets the edges of the 172 range right', () => {
		expect(preferredAddress(['8.8.8.8', '172.15.0.1'])).toBe('8.8.8.8');
		expect(preferredAddress(['8.8.8.8', '172.32.0.1'])).toBe('8.8.8.8');
		expect(preferredAddress(['8.8.8.8', '172.16.0.1'])).toBe('172.16.0.1');
		expect(preferredAddress(['8.8.8.8', '172.31.255.1'])).toBe('172.31.255.1');
	});
});

/* What computer this window is on.
 *
 * The whole design of it is in the two answers, not in the facts: it says NOTHING on the machine
 * running the library, because the hardware block there already describes that machine, and a
 * second block saying the same thing twice would invite somebody to look for a difference that
 * cannot exist. That null is also what saves the page from needing a mode flag of its own.
 */
describe('localHardware', () => {
	function withMode(mode: 'standalone' | 'client') {
		registerVerbs(localAt(TRUSTED), {
			sharing: {
				read: () => ({
					enabled: false,
					live: false,
					mode,
					address: null,
					port: 5171
				}),
				write: async () => {}
			}
		});
	}

	it('describes this computer when the library is on another one', async () => {
		withMode('client');

		const said = (await ipcRenderer.invoke(LOCAL_HARDWARE)) as LocalMachine | null;

		expect(said).not.toBeNull();
		expect(said?.thread_count).toBeGreaterThan(0);
	});

	it('says nothing on the machine running the library', async () => {
		withMode('standalone');

		expect(await ipcRenderer.invoke(LOCAL_HARDWARE)).toBeNull();
	});

	it('refuses a page that has been navigated somewhere else', async () => {
		withMode('client');
		sender.senderFrame = { url: 'https://example.com/anything', parent: null };

		expect(await ipcRenderer.invoke(LOCAL_HARDWARE)).toBeNull();
	});
});

/* What this computer is called: the name a phone's remote tells two desks apart by. */
describe('machineName', () => {
	it("answers this computer's own name, on this machine's Sift and on a server elsewhere", async () => {
		registerVerbs(localAt(TRUSTED));
		expect(await ipcRenderer.invoke(channels.MACHINE_NAME)).toBe(os.hostname().trim().slice(0, 63));

		resetElectronStub();
		registerVerbs(remoteAt(TRUSTED));
		expect(await ipcRenderer.invoke(channels.MACHINE_NAME)).toBe(os.hostname().trim().slice(0, 63));
	});

	it('refuses a page that has been navigated somewhere else', async () => {
		registerVerbs(localAt(TRUSTED));
		sender.senderFrame = { url: 'https://example.com/anything', parent: null };

		expect(await ipcRenderer.invoke(channels.MACHINE_NAME)).toBeNull();
	});
});

/* The firewall verbs. The rule they would create is firewall.test.ts's subject; what matters here
 * is that they are a security boundary like every other verb, and that the port they open is the
 * one the backend is listening on rather than one the page chose. */
/** What the verbs answer when the question cannot be put at all. */
const NOT_ASKED = { state: 'unknown', networks: null, scope: null };

/* The connect screen's verbs. What the screen draws when the saved address did not answer is the
 * shell's sentence, and the way back from a dead server is the list it was on. */
describe('the connect screen', () => {
	const saved = [{ label: 'http://10.0.0.5:5171', origin: 'http://10.0.0.5:5171' }];
	const book = {
		remember: vi.fn(async () => null),
		last: () => 'http://10.0.0.5:5171',
		problem: () => 'Sift could not reach http://10.0.0.5:5171. Nothing answered at that address.',
		saved: () => saved,
		forget: vi.fn((origin: string) => saved.filter((one) => one.origin !== origin))
	};

	beforeEach(() => {
		resetElectronStub();
		registerVerbs(localAt(TRUSTED), {
			servers: book
		});
	});

	it('answers where the screen stands in one question', async () => {
		expect(await ipcRenderer.invoke(CONNECT_STATE)).toEqual({
			last: 'http://10.0.0.5:5171',
			problem: 'Sift could not reach http://10.0.0.5:5171. Nothing answered at that address.',
			servers: saved
		});
	});

	it('forgets one address and answers with the list afterwards', async () => {
		expect(await ipcRenderer.invoke(FORGET_SERVER, 'http://10.0.0.5:5171')).toEqual([]);
		expect(book.forget).toHaveBeenCalledWith('http://10.0.0.5:5171');
		expect(await ipcRenderer.invoke(FORGET_SERVER, 42)).toEqual(saved);
	});

	it('answers nothing to a page that is not Sift', async () => {
		sender.senderFrame.url = 'https://elsewhere.example/';
		expect(await ipcRenderer.invoke(CONNECT_STATE)).toEqual({
			last: null,
			problem: null,
			servers: []
		});
		expect(await ipcRenderer.invoke(FORGET_SERVER, 'http://10.0.0.5:5171')).toEqual([]);
	});
});

describe('opening the firewall', () => {
	const sharing = {
		read: () => ({
			enabled: true,
			live: true,
			mode: 'standalone' as const,
			address: 'http://10.0.0.2:5171',
			port: 5171
		}),
		write: async () => {}
	};

	beforeEach(() => {
		resetElectronStub();
		registerVerbs(localAt(TRUSTED), {
			sharing,
			port: 5171
		});
	});

	it('reads the state for the port the backend listens on', async () => {
		expect(await ipcRenderer.invoke(GET_FIREWALL)).toBe('read:5171');
	});

	it('opens the same port, and nothing the page named', async () => {
		expect(await ipcRenderer.invoke(OPEN_FIREWALL, 25)).toBe('open:5171');
	});

	/* The one that matters. Opening a port needs administrator rights, so a page that had been
	 * navigated somewhere else must not be able to raise that prompt. A person shown an elevation
	 * dialog they did not ask for is being trained to click yes. */
	it('refuses a page that has been navigated somewhere else', async () => {
		sender.senderFrame = { url: 'https://example.com/anything', parent: null };

		expect(await ipcRenderer.invoke(OPEN_FIREWALL)).toEqual(NOT_ASKED);
		expect(await ipcRenderer.invoke(GET_FIREWALL)).toEqual(NOT_ASKED);
	});

	it('refuses a subframe of a trusted page', async () => {
		sender.senderFrame = {
			url: `${TRUSTED}/browse`,
			parent: { url: `${TRUSTED}/browse` }
		};

		expect(await ipcRenderer.invoke(OPEN_FIREWALL)).toEqual(NOT_ASKED);
	});

	/* In a shell with no library of its own there is nothing to open a port for, and the screen
	 * does not offer it. This is the second place that has to be true. */
	it('answers nothing in a shell that is not running a library', async () => {
		resetElectronStub();
		registerVerbs(localAt(TRUSTED));

		expect(await ipcRenderer.invoke(OPEN_FIREWALL)).toEqual(NOT_ASKED);
		expect(await ipcRenderer.invoke(GET_FIREWALL)).toEqual(NOT_ASKED);
	});
});

/* What this copy of Sift is. On the machine holding the library it is the same install as the
 * backend and the answer is redundant; in client mode it is the only true thing on a screen whose
 * every other number came from somewhere else.
 *
 * The packaged case is what makes the refusal testable at all. Unpackaged the verb answers null
 * whoever asks, so a refusal test written against a checkout passes with the check taken out.
 * Every test here that is about who is asking has to be a test of a copy that has something to
 * tell them.
 */
const asClient = {
	read: () => ({
		enabled: false,
		live: false,
		mode: 'client' as const,
		address: null,
		port: 5171
	}),
	write: async () => {}
};

const asServer = {
	read: () => ({
		enabled: true,
		live: true,
		mode: 'standalone' as const,
		address: 'http://10.0.0.2:5171',
		port: 5171
	}),
	write: async () => {}
};

describe('the version of this copy', () => {
	it('answers nothing from a checkout, rather than Electron own version', async () => {
		setVersion('43.4.1');
		expect(await ipcRenderer.invoke(SHELL_VERSION)).toBeNull();
	});

	/* THE ANSWER IS THE SIGNAL. A version means there is a second computer to name, so it is given
	 * in client mode and withheld on the machine holding the library, where the backend travels
	 * inside this application and the two numbers are one install. The screen then needs no mode
	 * flag: it names two versions exactly when it is handed two. Same rule as `localHardware`. */
	it('answers the application own version in client mode', async () => {
		resetElectronStub();
		app.isPackaged = true;
		setVersion('0.1.1');
		registerVerbs(localAt(TRUSTED), {
			sharing: asClient,
			port: 5171
		});

		expect(await ipcRenderer.invoke(SHELL_VERSION)).toBe('0.1.1');
	});

	it('answers nothing on the machine the library is on', async () => {
		resetElectronStub();
		app.isPackaged = true;
		setVersion('0.1.1');
		registerVerbs(localAt(TRUSTED), {
			sharing: asServer,
			port: 5171
		});

		expect(await ipcRenderer.invoke(SHELL_VERSION)).toBeNull();
	});

	it('refuses a page that has been navigated somewhere else', async () => {
		resetElectronStub();
		app.isPackaged = true;
		setVersion('0.1.1');
		registerVerbs(localAt(TRUSTED), {
			sharing: asClient,
			port: 5171
		});
		sender.senderFrame = { url: 'https://example.com/anything', parent: null };

		expect(await ipcRenderer.invoke(SHELL_VERSION)).toBeNull();
	});

	it('refuses a subframe of a trusted page', async () => {
		resetElectronStub();
		app.isPackaged = true;
		setVersion('0.1.1');
		registerVerbs(localAt(TRUSTED), {
			sharing: asClient,
			port: 5171
		});
		sender.senderFrame = {
			url: `${TRUSTED}/settings`,
			parent: { url: `${TRUSTED}/settings` }
		};

		expect(await ipcRenderer.invoke(SHELL_VERSION)).toBeNull();
	});
});

/* The one verb that changes something the operating system owns.
 *
 * What matters is not that a colour arrives: it is that nothing ELSE can. The caption buttons are
 * how a window gets closed, so a verb able to move, resize or hide them would be a verb for making a
 * window nobody can shut. It takes two colours, it is refused for anything that is not plain hex,
 * and the height is the shell's own rather than anything the page said.
 */
describe('painting the window buttons', () => {
	const OVERLAY = 56;

	function armed() {
		resetElectronStub();
		/* By name, so a value cannot land in the wrong slot: a positional call could hand 5171
		   to `downloads` and 56 to `port`, and the overlay tests would read as a shell refusing
		   to paint its buttons. */
		registerVerbs(localAt(TRUSTED), {
			port: 5171,
			titleBarHeight: OVERLAY
		});
		return new BrowserWindow({});
	}

	it('paints them in the colours the page asked for, at the shell&apos;s own height', async () => {
		const window = armed();

		expect(
			await ipcRenderer.invoke(SET_TITLE_BAR, {
				color: '#1e2024',
				symbolColor: '#eef1f6'
			})
		).toBe(true);
		expect(window.titleBarOverlays).toEqual([
			{ color: '#1e2024', symbolColor: '#eef1f6', height: OVERLAY }
		]);
	});

	it('refuses anything that is not a plain hex colour, and paints nothing', async () => {
		const window = armed();

		for (const asked of [
			{ color: 'red', symbolColor: '#eef1f6' },
			{ color: '#1e2024', symbolColor: 'rgb(1,2,3)' },
			{ color: '#1e2024', symbolColor: '#eef1f6; system-ui' },
			{ color: 1, symbolColor: '#eef1f6' },
			null
		]) {
			expect(await ipcRenderer.invoke(SET_TITLE_BAR, asked)).toBe(false);
		}
		expect(window.titleBarOverlays, 'a refused colour still reached the window').toEqual([]);
	});

	it('cannot be asked for a height, so it cannot make the buttons unreachable', async () => {
		const window = armed();

		await ipcRenderer.invoke(SET_TITLE_BAR, {
			color: '#1e2024',
			symbolColor: '#eef1f6',
			height: 0
		});

		expect(window.titleBarOverlays[0].height).toBe(OVERLAY);
	});

	it('answers false when the call came from no window it knows', async () => {
		armed();
		BrowserWindow.instances = [];

		expect(
			await ipcRenderer.invoke(SET_TITLE_BAR, {
				color: '#1e2024',
				symbolColor: '#eef1f6'
			})
		).toBe(false);
	});

	it('answers false, and does not throw, when the window was made without an overlay', async () => {
		const window = armed();
		window.setTitleBarOverlay = () => {
			throw new Error('Titlebar overlay is not enabled');
		};

		expect(
			await ipcRenderer.invoke(SET_TITLE_BAR, {
				color: '#1e2024',
				symbolColor: '#eef1f6'
			})
		).toBe(false);
	});

	it('refuses a page that is not Sift', async () => {
		const window = armed();
		sender.senderFrame = { url: 'https://example.com/anything', parent: null };

		expect(
			await ipcRenderer.invoke(SET_TITLE_BAR, {
				color: '#1e2024',
				symbolColor: '#eef1f6'
			})
		).toBe(false);
		expect(window.titleBarOverlays).toEqual([]);
	});

	it('does nothing at all on a window that was drawn with a real title bar', async () => {
		/* No overlay height was passed, which is what a shell that never asked for one looks like,
		   and every test in this file that is not about this verb. Answering false is the honest
		   outcome: the page is already drawing itself correctly and only the buttons stay as they
		   were. */
		resetElectronStub();
		registerVerbs(localAt(TRUSTED));
		const window = new BrowserWindow({});

		expect(
			await ipcRenderer.invoke(SET_TITLE_BAR, {
				color: '#1e2024',
				symbolColor: '#eef1f6'
			})
		).toBe(false);
		expect(window.titleBarOverlays).toEqual([]);
	});
});

/* --- The settings the window keeps for itself -------------------------------------------- */

/*
 * Offering this library to the network.
 *
 * The verb answers the state AFTER the change rather than whether the change was accepted, and
 * that is the whole point of it: turning sharing on opens a socket on another address, and a
 * screen told "yes, done" while the socket refused to move would be a screen saying something
 * that is not true. Both halves come back, so it can say which.
 */
describe('offering the library to the network', () => {
	let enabled: boolean;
	let live: boolean;
	const write = vi.fn(async (on: boolean) => {
		enabled = on;
		live = on;
	});

	function armed() {
		enabled = false;
		live = false;
		write.mockClear();
		resetElectronStub();
		registerVerbs(localAt(TRUSTED), {
			sharing: {
				read: () => ({
					enabled,
					live,
					mode: 'standalone' as const,
					address: 'http://10.0.0.5:5171',
					port: 5171
				}),
				write
			}
		});
	}

	beforeEach(armed);

	it('answers the setting and the socket separately, and the port they are about', async () => {
		expect(await ipcRenderer.invoke(GET_SHARING)).toEqual({
			enabled: false,
			live: false,
			mode: 'standalone',
			address: 'http://10.0.0.5:5171',
			port: 5171
		});
	});

	it('answers what is true after the socket moved, not the word the page sent', async () => {
		expect(await ipcRenderer.invoke(SET_SHARING, true)).toMatchObject({
			enabled: true,
			live: true
		});
		expect(write).toHaveBeenCalledWith(true);
	});

	/* Checked against a real boolean rather than cast. What this opens is a socket the rest of the
	   network can reach, so anything that is not one of the two answers is a value that should
	   never have arrived, and it reads the state back rather than refusing silently, so the
	   screen is left drawing the truth. */
	it('opens nothing at all for a value that is not yes or no', async () => {
		expect(await ipcRenderer.invoke(SET_SHARING, 'on')).toMatchObject({
			enabled: false
		});
		expect(write).not.toHaveBeenCalled();
	});

	it('refuses a page that has been navigated somewhere else, and writes nothing', async () => {
		sender.senderFrame = { url: 'https://example.com/anything', parent: null };

		expect(await ipcRenderer.invoke(GET_SHARING)).toBeNull();
		expect(await ipcRenderer.invoke(SET_SHARING, true)).toBeNull();
		expect(write).not.toHaveBeenCalled();
	});

	/* A client is looking at somebody else's library and has nothing of its own to offer. Null is
	   the answer, and the screen draws no switch rather than one that cannot do anything. */
	it('answers nothing on a shell with no library of its own', async () => {
		resetElectronStub();
		registerVerbs(localAt(TRUSTED));

		expect(await ipcRenderer.invoke(GET_SHARING)).toBeNull();
		expect(await ipcRenderer.invoke(SET_SHARING, true)).toBeNull();
	});
});

/*
 * The address to offer for a second computer, read off this machine's own interfaces.
 *
 * `preferredAddress` above states the choice; this states the reading: only IPv4, never the
 * loopback one, because the point of the string is that somebody TYPES IT INTO ANOTHER COMPUTER
 * and 127.0.0.1 gets them nowhere.
 */
describe('reading this machine own addresses', () => {
	afterEach(() => {
		for (const key of Object.keys(machineInterfaces)) delete machineInterfaces[key];
	});

	it('passes over the loopback address and anything that is not IPv4', () => {
		machineInterfaces['Loopback'] = [{ family: 'IPv4', internal: true, address: '127.0.0.1' }];
		machineInterfaces['Ethernet'] = [
			{ family: 'IPv6', internal: false, address: 'fe80::1' },
			{ family: 'IPv4', internal: false, address: '192.168.1.41' }
		];

		expect(networkAddress()).toBe('192.168.1.41');
	});

	/* A machine usually has several, and the first one the operating system lists is arbitrary:
	   here a carrier-range address from a tunnel, while the one the other computer on the same
	   switch can reach is the home-network one. */
	it('prefers the address the other computer can actually reach', () => {
		machineInterfaces['Tunnel'] = [{ family: 'IPv4', internal: false, address: '100.64.0.7' }];
		machineInterfaces['Ethernet'] = [{ family: 'IPv4', internal: false, address: '192.168.1.24' }];

		expect(networkAddress()).toBe('192.168.1.24');
	});

	it('answers nothing at all on a machine with nothing to offer', () => {
		machineInterfaces['Loopback'] = [{ family: 'IPv4', internal: true, address: '127.0.0.1' }];
		machineInterfaces['Absent'] = undefined;

		expect(networkAddress()).toBeNull();
	});
});
