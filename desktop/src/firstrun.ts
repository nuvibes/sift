/* The questions that have to be answered before a backend can exist.
 *
 * There are exactly two, and they are asked once: which lifecycle, and where the library lives.
 * They cannot be part of Sift's own first-run wizard, because that wizard is served by the
 * backend, and in client mode there is no local backend at all, while in standalone the backend
 * cannot start until it has been told where to put things.
 *
 * They are Sift routes, not operating-system dialogs: `shellpage.ts` serves the built client
 * under a scheme of the shell's own, so a screen with no server behind it is still drawn from the
 * real tokens, and the mode question can lay out what a server and a client are in a card each.
 *
 * The folder picker stays in the operating system, and not for its looks: a page cannot open it,
 * drive it, read what is in it or pre-fill it, so the folder that comes back is one a person
 * physically pointed at. It is the same dialog a library root is granted through later.
 */

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

/**
 * The folder the screen offers first. A library an earlier installation kept (recorded by it
 * and still holding a database) comes before the default, so installing Sift again is carrying
 * on rather than starting an empty library beside the one somebody already had.
 *
 * The default folder is looked in too. A library can be there with nothing recorded about it (an
 * earlier installation that kept its folder and not its record). Offered as an empty default,
 * the screen says "Default" and the next one asks for that library's sign-in, which reads as a
 * fresh install landing on a login. Holding a database, it is offered as the library it is.
 */
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

/**
 * The machine's own folder dialog, for somebody who wants the library somewhere else.
 *
 * Null when they closed it without choosing, which is not a failure and has nothing to say: the
 * screen simply stays where it was, still offering the suggested folder.
 *
 * `dontAddToRecent` keeps the choice out of the Windows recent-places list, which every other
 * application on the machine can read.
 */
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

/* Proving it is writable NOW, rather than letting the backend fail on it later. The backend does
 * check, and says so clearly, but by then a window is open and the failure looks like Sift being
 * broken rather than a folder being wrong. */
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

/**
 * Everything that can be said against a chosen folder before the backend is started on it: on a
 * network drive, where the database cannot live, or not writable. One sentence or nothing.
 */
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

/**
 * Which screen answers what is still outstanding, as a route on the shell's own scheme.
 *
 * One function rather than a chain of `if`s in `main`, because it is asked twice (once at
 * startup and again after each answer, to decide whether there is another question), and two
 * copies of that reasoning would be two answers to "is setup finished".
 */
export function firstRunRoute(settings: DesktopSettings): string | null {
	if (settings.mode === null) return '/start';
	if (settings.mode === 'standalone' && settings.dataDir === null) return '/library-location';
	return null;
}
