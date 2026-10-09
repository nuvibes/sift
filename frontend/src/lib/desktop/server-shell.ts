/*
 * The computer running Sift, reached through its own API (`/api/desktop`), for a window on
 * another computer: what an admin changes about the install happens there. Acts that restart
 * Sift wait for a new run (`followSwitch`); each names the asking device.
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

/** Turn starting with Windows on or off there, answering what is true afterwards. */
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

/** Ask Windows there to let other computers through; the prompt appears there. */
export async function openServerFirewall(scope: 'private' | 'any'): Promise<ServerFirewall> {
	return api.post<ServerFirewall>(
		'/desktop/firewall',
		naming({ body: { scope } }, await thisDevice())
	);
}

type RestartOutcome = { ok: true } | { ok: false; problem: string };

/** Restart Sift there, wait for a different run (its boot id), then load the page. */
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

/** The wait for Sift to come back after an act that restarts it there. */
export type Wait = Parameters<typeof followSwitch>[1];

type ActOutcome = { ok: true } | { ok: false; problem: string; refused: boolean };

/** Ask for an act that restarts Sift there, boot id read first, and wait for its return. */
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

/** Move both storage folders there into a folder the server's own browser handed back. */
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

/** Open a library the app there has opened before, by its data folder as listed. */
export async function openServerLibrary(dataDir: string): Promise<ServerTaken> {
	return api.post<ServerTaken>(
		'/desktop/libraries/open',
		naming({ body: { data_dir: dataDir } }, await thisDevice())
	);
}
