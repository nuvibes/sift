/* The shell's own settings.
 *
 * Small and deliberately separate from the backend's settings: these are the things that have to be
 * known BEFORE a backend exists: which lifecycle to run, where the data lives, which servers have
 * been saved. In client mode there is no local backend at all, so there is nowhere else they could
 * live.
 *
 * NOTHING SECRET GOES IN HERE. It is a plain JSON file in the user's profile with no encryption and
 * no permissions worth the name. Passwords, tokens and keys stay where they already are: sealed in
 * the database behind the master key.
 */

import type { KnownLibrary } from '../../shared/bridge';

import * as fs from 'node:fs';
import * as path from 'node:path';

import { defaultDataLocations, settingsFile, type DataLocations } from './paths';

export type Mode = 'standalone' | 'client';

export interface SavedServer {
	/** What the person called it. Free text; only ever displayed. */
	label: string;
	/** The origin to load, e.g. "http://192.168.1.20:5171". No path, no trailing slash. */
	origin: string;
}

export interface DesktopSettings {
	/** Bumped only when a field's meaning changes in a way a reader must know about. */
	version: 1;
	/** Unset until first run has been through. */
	mode: Mode | null;
	dataDir: string | null;
	cacheDir: string | null;
	servers: SavedServer[];
	/** The origin last successfully loaded in client mode, so a relaunch goes straight there. */
	lastServer: string | null;
	/**
	 * Where releases are looked for. Null means the built-in address.
	 *
	 * A setting for two real cases and not as a knob: proving the download-and-verify chain against
	 * a release served locally before anything is published, and a self-hoster who mirrors. It
	 * cannot weaken anything: an installer from any address still has to carry a signature made
	 * with the key compiled into the application.
	 */
	feedUrl: string | null;
	/**
	 * Whether the library is offered to the rest of the network, rather than to this computer only.
	 *
	 * OFF, and it has to default to off. Turning it on makes the backend listen on every address
	 * this machine has instead of on the loopback one, which is the difference between "a program
	 * on my computer" and "a service on my network", a decision that belongs to the person, taken
	 * deliberately, never inherited from a default they never saw.
	 *
	 * It lives in the SHELL's settings rather than the backend's because the shell is what starts
	 * the backend, and the address is chosen at that moment. A backend cannot rebind itself from a
	 * setting it reads after it is already listening.
	 */
	shareOnNetwork: boolean;
	/**
	 * Which browser links open in, as the path to its executable. Null means whichever one Windows
	 * would have used.
	 *
	 * NULL IS THE DEFAULT AND HAS TO BE. Recording the system default as a path would freeze it:
	 * somebody who later changed their Windows default would find Sift still opening the old one,
	 * with a setting that says "default" and does not mean it.
	 *
	 * It lives in the SHELL's settings rather than the user's because the shell is what opens
	 * links, and because it names a file on THIS machine: a user setting would follow somebody
	 * to a computer where that path is a different program or no program at all.
	 */
	browser: string | null;
	/**
	 * Where a file saved out of Sift lands, as a folder on this machine. Null means the one Windows
	 * would have used.
	 *
	 * NULL IS THE DEFAULT AND HAS TO BE, for the reason `browser` above is: recording the system's
	 * Downloads folder as a path would freeze it, so somebody who later moved it would find Sift
	 * still writing to the old one under a setting that says "default" and does not mean it.
	 *
	 * It lives in the SHELL's settings rather than the user's because the shell is what performs
	 * the save, and because it names a folder on THIS machine: a user setting would follow
	 * somebody to a computer where that path is somebody else's folder or none at all.
	 */
	downloadDir: string | null;
	/**
	 * Whether closing the window leaves Sift running in the notification area.
	 *
	 * ON, and unlike every other switch in this file the default is the permissive one. The reason
	 * is what Sift IS while its window is shut: a library being scanned, a download finishing, a
	 * recognition pass over thousands of faces, and (when sharing is on) a service another
	 * computer in the house is reading from. None of that is work somebody meant to abandon by
	 * pressing the close button, and a media application that stops mid-job because a window closed
	 * is the behaviour people complain about rather than the one they expect.
	 *
	 * It is still a setting rather than a rule, because the opposite is a perfectly reasonable
	 * preference: somebody who opens Sift to watch one thing wants the close button to mean closed.
	 * The help on the screen says which way it is set, so nobody has to discover it by pressing it.
	 */
	keepRunningWhenClosed: boolean;
	/**
	 * Every library this copy has opened, most recently first.
	 *
	 * IN THE SHELL'S SETTINGS AND NOWHERE ELSE, for the reason `dataDir` is here: this is a list of
	 * folders on THIS machine, and the only thing that can read it is the process that decides which
	 * one a backend is started on. A list kept inside a library would name the other libraries from
	 * inside one of them, which is a circular definition, and would be unreadable while the
	 * database it lives in is the one being switched away from.
	 *
	 * The list is a record of what was OPENED, never of what was chosen. See `remember` in
	 * libraries.ts: a folder joins it once a backend has actually started on it, so a path that was
	 * picked and then refused never becomes an entry offering to go back to it.
	 */
	libraries: KnownLibrary[];
}

/** One library this copy has opened. */
export type { KnownLibrary };

/** The settings of a copy that has never been set up. Exported so a test's double starts from the
 *  real defaults rather than a hand-kept list of fields that falls behind the next one added. */
export function fresh(): DesktopSettings {
	return {
		version: 1,
		mode: null,
		dataDir: null,
		cacheDir: null,
		servers: [],
		lastServer: null,
		feedUrl: null,
		shareOnNetwork: false,
		browser: null,
		downloadDir: null,
		keepRunningWhenClosed: true,
		libraries: []
	};
}

/* A missing or damaged file is not an error worth stopping for: it means first run, which is a
 * screen we already have. A file we cannot parse is treated the same way rather than thrown,
 * because the alternative is an application that will not start and cannot tell the person why. */
/**
 * Whether this copy may share on the network: only a copy set up as a server has a library to
 * share. Accepted in client mode, the setting would survive a change of mode, and a machine used
 * as a client and then set up as a server would boot on every address unasked.
 */
export function mayShare(mode: Mode | null): boolean {
	return mode === 'standalone';
}

/**
 * The settings with the mode unsaid: the mode, the server last joined, and the sharing switch,
 * which is a server's setting and must not come back listening on the network from a previous
 * answer. Three places ask this question (the settings screen's "set this up again", the back
 * button on the setup screens, and the offer made when the backend cannot start), and one answer
 * keeps them from disagreeing about what unsaying the mode touches.
 */
export function withoutMode(settings: DesktopSettings): DesktopSettings {
	return { ...settings, mode: null, lastServer: null, shareOnNetwork: false };
}

export function load(): DesktopSettings {
	try {
		const raw = fs.readFileSync(settingsFile(), 'utf8');
		const parsed = JSON.parse(raw) as Partial<DesktopSettings>;
		return {
			...fresh(),
			...parsed,
			// Trust nothing about the shape: this file is hand-editable and a wrong type here would
			// surface much later as an unreadable error somewhere else.
			servers: Array.isArray(parsed.servers) ? parsed.servers.filter(isServer) : [],
			/* Anything that is not exactly `true` means off. This file is hand-editable, and a
			 * setting that opens a machine to its network is the last one that should be talked into
			 * turning itself on by a truthy string. */
			shareOnNetwork: parsed.shareOnNetwork === true,
			/* Anything that is not a string is "no choice made", for the same reason: the value is a
			 * path this application will later start as a program, and a hand-edited file must not
			 * be able to make that anything but a string somebody chose from the list. */
			browser: typeof parsed.browser === 'string' && parsed.browser !== '' ? parsed.browser : null,
			/* Same rule as `browser`: a hand-edited file must not be able to make a path anything
			   but a string somebody chose. It is checked again where it is USED, because a folder
			   that existed when it was chosen can be gone by the time something is saved into it. */
			downloadDir:
				typeof parsed.downloadDir === 'string' && parsed.downloadDir !== ''
					? parsed.downloadDir
					: null,
			/* Anything that is not exactly `false` means on, which is the OPPOSITE rule to
			 * `shareOnNetwork` above and deliberately so. The defensive reading always goes towards
			 * the setting's own default: for a switch that opens a machine to its network that is
			 * off, and for one that decides whether a scan survives a closed window it is on. A
			 * hand-edited file holding a truthy string must not be able to change either. */
			keepRunningWhenClosed: parsed.keepRunningWhenClosed !== false,
			/* Trust nothing about the shape, for the reason `servers` is filtered: each entry names
			 * two folders this application will later start a backend on, and a hand-edited file
			 * must not be able to make either of them anything but a string somebody chose. */
			libraries: Array.isArray(parsed.libraries) ? parsed.libraries.filter(isLibrary) : [],
			version: 1
		};
	} catch {
		return fresh();
	}
}

function isLibrary(v: unknown): v is KnownLibrary {
	if (typeof v !== 'object' || v === null) return false;
	const o = v as Record<string, unknown>;
	return (
		typeof o.dataDir === 'string' &&
		o.dataDir !== '' &&
		typeof o.cacheDir === 'string' &&
		o.cacheDir !== '' &&
		typeof o.name === 'string' &&
		typeof o.lastOpened === 'number'
	);
}

function isServer(v: unknown): v is SavedServer {
	if (typeof v !== 'object' || v === null) return false;
	const o = v as Record<string, unknown>;
	return typeof o.label === 'string' && typeof o.origin === 'string';
}

/* Written temp-then-rename, never in place. A crash or a full disk halfway through an in-place
 * write leaves a truncated file, and a truncated settings file is indistinguishable from a fresh
 * install, which would silently send somebody back through first run and re-point their library. */
export function save(settings: DesktopSettings): void {
	const target = settingsFile();
	fs.mkdirSync(path.dirname(target), { recursive: true });
	const tmp = `${target}.tmp`;
	fs.writeFileSync(tmp, JSON.stringify(settings, null, 2), 'utf8');
	fs.renameSync(tmp, target);
}

/** The data folders in use, falling back to the defaults until first run has settled them. */
export function locations(settings: DesktopSettings): DataLocations {
	const fallback = defaultDataLocations();
	return {
		dataDir: settings.dataDir ?? fallback.dataDir,
		cacheDir: settings.cacheDir ?? fallback.cacheDir
	};
}
