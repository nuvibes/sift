/* The questions that have to be answered before a backend can exist. */

import { dialog } from 'electron';
import * as fs from 'node:fs';
import * as path from 'node:path';

import { isRemoteDrive, KEEP_IT_HERE, type DriveKindAsker } from './drives';
import { DATABASE_FILENAME } from './libraries';
import { defaultDataLocations } from './paths';
import type { DataLocations } from './paths';
import type { DesktopSettings } from './settings';
import { rememberedDataLocation } from './uninstall';

/** Where the library would go if nobody said otherwise. What the screen offers. */
export function suggestedDataLocation(): DataLocations {
	return defaultDataLocations();
}

/** What the folder screen offers: a library an earlier installation left, or the default. */
export interface Offered {
	locations: DataLocations;
	/** True when a database is already there: the offer is to carry on with it. */
	existing: boolean;
}

/** The folder the screen offers first. */
export async function offeredDataLocation(
	remembered: () => Promise<DataLocations | null> = rememberedDataLocation,
	exists: (file: string) => boolean = fs.existsSync
): Promise<Offered> {
	const kept = await remembered();
	if (kept !== null) return { locations: kept, existing: true };
	const suggested = suggestedDataLocation();
	return {
		locations: suggested,
		existing: exists(path.join(suggested.dataDir, DATABASE_FILENAME))
	};
}

/** The machine's own folder dialog, for somebody who wants the library somewhere else. */
export async function pickDataLocation(): Promise<DataLocations | null> {
	const picked = await dialog.showOpenDialog({
		title: 'Choose where Sift keeps your library',
		properties: ['openDirectory', 'createDirectory', 'dontAddToRecent'],
		buttonLabel: 'Keep my library here'
	});
	const chosen = picked.filePaths[0];
	if (picked.canceled || chosen === undefined) return null;
	return { dataDir: `${chosen}\\data`, cacheDir: `${chosen}\\cache` };
}

/* Proving it is writable NOW, rather than letting the backend fail on it later. */
export function assertWritable(locations: DataLocations): string | null {
	for (const dir of [locations.dataDir, locations.cacheDir]) {
		try {
			fs.mkdirSync(dir, { recursive: true });
			fs.accessSync(dir, fs.constants.W_OK);
		} catch (err) {
			return `Sift cannot write to ${dir}.\n\n${err instanceof Error ? err.message : String(err)}`;
		}
	}
	return null;
}

/** Everything that can be said against a chosen folder before the backend is started on it: on a
 * network drive, where the database cannot live, or not writable. */
export async function refuseLocation(
	locations: DataLocations,
	ask?: DriveKindAsker
): Promise<string | null> {
	if (await isRemoteDrive(locations.dataDir, ask)) return KEEP_IT_HERE;
	return assertWritable(locations);
}

/** True when first run still has questions outstanding. */
export function needsFirstRun(settings: DesktopSettings): boolean {
	return settings.mode === null || (settings.mode === 'standalone' && settings.dataDir === null);
}

/** Which screen answers what is still outstanding, as a route on the shell's own scheme. */
export function firstRunRoute(settings: DesktopSettings): string | null {
	if (settings.mode === null) return '/start';
	if (settings.mode === 'standalone' && settings.dataDir === null) return '/library-location';
	return null;
}
