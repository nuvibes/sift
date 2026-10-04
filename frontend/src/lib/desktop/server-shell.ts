/*
 * The computer running Sift, reached through its own API.
 *
 * ## Why the server, and not this window's bridge
 *
 * An admin can be looking at a library from another computer: the Sift app in client mode, or a
 * browser. What they change about the install (starting with Windows, the firewall rule, a
 * restart) has to happen on the computer running Sift, never on the one in front of them. So these
 * are requests to the server, which asks the Sift app that started it (`/api/desktop`); the bridge
 * of the window they are sitting at is never asked for them.
 *
 * On the computer running Sift itself, the app's own verbs answer the same questions about the
 * same machine, and the panes keep using those there. This is for every other window.
 *
 * ## The things that must be pressed there
 *
 * Opening the firewall raises Windows' own administrator prompt, and an update opens its installer,
 * both on the computer running Sift. Nothing reached over a network can approve them, so the screen
 * says where to go. Choosing a database file in the computer's own picker is the same: it is not
 * offered from here at all, and the screen says where it is.
 *
 * ## The acts that restart Sift there
 *
 * Sharing, moving the storage folders, an update and opening a remembered library each stop Sift on
 * that computer. The app there answers first (taken on, or refused in its own words before anything
 * stopped), and this page then waits for a new run of the server (`followSwitch`), as a restart
 * does.
 *
 * ## Which device asked
 *
 * Every act that changes that computer is written into History with who asked and from which
 * device. The Sift app knows its own computer's name and says it on the ask (`device`); a browser
 * knows none and says nothing, and the line then reads "from another computer".
 */
import { ApiError, api } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { bridge } from '$lib/bridge';
import { serverBootId } from '$lib/shell/health';
import { followSwitch } from '$lib/settings-ui/follow-switch';

export type ServerDesktop = components['schemas']['DesktopView'];
export type ServerFirewall = components['schemas']['FirewallView'];
type ServerTaken = components['schemas']['ActTaken'];
export type ServerStorage = components['schemas']['StorageView'];
type ServerUpdate = components['schemas']['UpdateTaken'];
type ServerAppLog = components['schemas']['AppLog'];
type ServerLibraries = components['schemas']['RememberedLibraries'];

/** This window's computer by its own name, or null in a browser or where the app cannot say. */
export async function thisDevice(): Promise<string | null> {
	try {
		return await bridge.machineName();
	} catch {
		return null;
	}
}

/** An ask's options with the device named, where this window knows its name. */
function naming<O extends object>(options: O, device: string | null): O {
	return device ? { ...options, query: { device } } : options;
}

/** Read the computer running Sift, or null where it cannot be read (not an admin, no answer). */
export async function readServerDesktop(): Promise<ServerDesktop | null> {
	try {
		return await api.get<ServerDesktop>('/desktop');
	} catch {
		return null;
	}
}

/** Whether an answer names a computer this window can act on: an app is running Sift there. */
export function offersServer(desk: ServerDesktop | null): desk is ServerDesktop {
	return desk?.has_app === true;
}

/** Sift starting with Windows is turned on or off THERE, and what is true there afterwards is answered. */
export async function setServerStartsWithWindows(on: boolean): Promise<ServerDesktop> {
	return api.put<ServerDesktop>(
		'/desktop/start-with-windows',
		naming({ body: { on } }, await thisDevice())
	);
}

/** Whether Windows there lets other computers reach Sift. */
export async function readServerFirewall(): Promise<ServerFirewall | null> {
	try {
		return await api.get<ServerFirewall>('/desktop/firewall');
	} catch {
		return null;
	}
}

/** Ask Windows there to let other computers through. The prompt appears THERE. */
export async function openServerFirewall(scope: 'private' | 'any'): Promise<ServerFirewall> {
	return api.post<ServerFirewall>(
		'/desktop/firewall',
		naming({ body: { scope } }, await thisDevice())
	);
}

/** What came of a restart asked from here. */
type RestartOutcome = { ok: true } | { ok: false; problem: string };

/**
 * Restart Sift on the computer running it, wait for it to come back, and load its first page.
 *
 * The server answers the ask before it goes, so the wait is for a DIFFERENT run (its boot id)
 * rather than for any answer, which the one about to stop would still give. `arrive` is how the
 * page is loaded again once it is back; the tests hand their own.
 */
export async function restartServer(
	cannot: string,
	slow: string,
	wait: Parameters<typeof followSwitch>[1] = {}
): Promise<RestartOutcome> {
	const before = await serverBootId();
	const device = await thisDevice();
	try {
		if (device) await api.post('/performance/restart', { query: { device } });
		else await api.post('/performance/restart');
	} catch (error) {
		return {
			ok: false,
			problem: error instanceof ApiError ? (error.detail ?? error.message) : cannot
		};
	}
	return (await followSwitch(before, wait)) ? { ok: true } : { ok: false, problem: slow };
}

/** The wait for Sift to come back after an act that restarts it there. Parameters of `followSwitch`. */
export type Wait = Parameters<typeof followSwitch>[1];

/** What came of an act that restarts Sift there: back on a new run, refused, or not back in time. */
type ActOutcome = { ok: true } | { ok: false; problem: string; refused: boolean };

/**
 * Ask for an act that restarts Sift there, and wait for it to come back.
 *
 * The boot id is read BEFORE asking, since the run answering the ask is the one about to stop. A
 * refusal comes back in the app's own words and nothing stopped; a request that fails outright is
 * said with `cannot`; a wait that runs out is said with `slow`.
 */
export async function actThere(
	ask: () => Promise<ServerTaken>,
	cannot: string,
	slow: string,
	wait: Wait = {}
): Promise<ActOutcome> {
	const before = await serverBootId();
	let taken: ServerTaken;
	try {
		taken = await ask();
	} catch (error) {
		return {
			ok: false,
			refused: true,
			problem: error instanceof ApiError ? (error.detail ?? error.message) : cannot
		};
	}
	if (!taken.ok) return { ok: false, refused: true, problem: taken.refusal ?? cannot };
	return (await followSwitch(before, wait))
		? { ok: true }
		: { ok: false, refused: false, problem: slow };
}

/** Offer the library to the network there, or stop. Off cuts off every other computer. */
export async function setServerSharing(on: boolean): Promise<ServerTaken> {
	return api.put<ServerTaken>('/desktop/sharing', naming({ body: { on } }, await thisDevice()));
}

/** Where Sift keeps its two folders there, or null where it cannot be read. */
export async function readServerStorage(): Promise<ServerStorage | null> {
	try {
		return await api.get<ServerStorage>('/desktop/storage');
	} catch {
		return null;
	}
}

/** Move both storage folders there into `folder`, one the server's own folder browser handed back. */
export async function moveServerStorage(folder: string): Promise<ServerTaken> {
	return api.post<ServerTaken>(
		'/desktop/storage/move',
		naming({ body: { folder } }, await thisDevice())
	);
}

/** Install a newer Sift there. Names nothing; the installer opens on that computer's screen. */
export async function updateServer(): Promise<ServerUpdate> {
	const device = await thisDevice();
	return device
		? api.post<ServerUpdate>('/desktop/update', { query: { device } })
		: api.post<ServerUpdate>('/desktop/update');
}

/** The end of the Sift app's own log there. */
export function readServerAppLog(lines: number): Promise<ServerAppLog> {
	return api.get<ServerAppLog>('/desktop/log', { query: { lines: String(lines) } });
}

/** The libraries the Sift app there has opened, or null where they cannot be read. */
export async function readServerLibraries(): Promise<ServerLibraries | null> {
	try {
		return await api.get<ServerLibraries>('/desktop/libraries');
	} catch {
		return null;
	}
}

/** Open a library the Sift app there has opened before, named by its data folder as it listed it. */
export async function openServerLibrary(dataDir: string): Promise<ServerTaken> {
	return api.post<ServerTaken>(
		'/desktop/libraries/open',
		naming({ body: { data_dir: dataDir } }, await thisDevice())
	);
}
