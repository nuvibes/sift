/* The shell's own settings. */

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
	/** Where releases are looked for. Null means the built-in address. */
	feedUrl: string | null;
	/** Whether the library is offered to the rest of the network. */
	shareOnNetwork: boolean;
	/** Which browser links open in, as the path to its executable. */
	browser: string | null;
	/** Where a file saved out of Sift lands, as a folder on this machine. */
	downloadDir: string | null;
	/** Whether closing the window leaves Sift running in the notification area. */
	keepRunningWhenClosed: boolean;
	/** Every library this copy has opened, most recently first. */
	libraries: KnownLibrary[];
}

/** One library this copy has opened. */
export type { KnownLibrary };

/** The settings of a copy that has never been set up. */
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

/* A missing or damaged file means first run, which is a screen we already have. */
/** Whether this copy may share: only a copy set up as a server has a library to share. */
export function mayShare(mode: Mode | null): boolean {
	return mode === 'standalone';
}

/** The settings with the mode unsaid; the sharing switch must not come back listening. */
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
			// Trust nothing about the shape: this file is hand-editable.
			servers: Array.isArray(parsed.servers) ? parsed.servers.filter(isServer) : [],
			/* Anything that is not exactly `true` means off. */
			shareOnNetwork: parsed.shareOnNetwork === true,
			/* Not a string is "no choice made": the value is a path later started as a program. */
			browser: typeof parsed.browser === 'string' && parsed.browser !== '' ? parsed.browser : null,
			/* Same rule as `browser`. */
			downloadDir:
				typeof parsed.downloadDir === 'string' && parsed.downloadDir !== ''
					? parsed.downloadDir
					: null,
			/* Anything not exactly `false` means on: the OPPOSITE rule to `shareOnNetwork`. */
			keepRunningWhenClosed: parsed.keepRunningWhenClosed !== false,
			/* Trust nothing, as for `servers`: each entry names two folders a backend starts on. */
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

/* Written temp-then-rename, never in place. */
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
