/* Telling the uninstaller where this installation put things.
 *
 * THE UNINSTALLER CANNOT WORK IT OUT FOR ITSELF, and that is the whole reason this file exists.
 * Somebody removing Sift reasonably expects the option to remove everything Sift wrote, but the
 * library folder is CHOSEN at first run and is only `%LOCALAPPDATA%\Sift` when nobody changed it.
 * An uninstaller that deletes the default would leave a moved library behind while claiming it had
 * removed everything, which is worse than not offering at all.
 *
 * So the application writes the two folders it was actually given into the registry, under its own
 * key, and the uninstaller reads them there. The registry rather than a file beside the settings
 * because NSIS reads a registry value in one line and a JSON file not at all, and this has to be
 * readable by a program that runs after the application it describes has been deleted.
 *
 * WRITTEN ON EVERY START, not once at install. The location can change (somebody moves their
 * library, or reinstalls over the top), and a value recorded once is a value that goes stale
 * without anything noticing. Rewriting it costs one process launch, off the startup path.
 *
 * NOTHING HERE DELETES ANYTHING. It records three paths and nothing else; the deleting is the
 * uninstaller's, behind a checkbox that is off by default. See installer/installer.nsh.
 *
 * THE THIRD IS THE LIBRARIES FOLDER, found the way the backend finds it. A library opened from
 * inside that folder has its data at `<folder>\<name>\data`, so "the folder beside the data" is a
 * different place for every library, and an uninstaller that looked there would find only the one
 * it was standing in while its page promised every library. The rule that finds the folder from any
 * member is `librariesFolderOf`, the backend's own rule in this shell; the uninstaller lists what
 * that folder holds on its page, before the box is ticked, and deletes exactly that.
 */

import { execFile } from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';

import { librariesFolderOf } from './libraries';
import type { DataLocations } from './paths';

/** Sift's own key in the current user's registry. Per-user, like the installation itself. */
export const REGISTRY_KEY = 'HKCU\\Software\\Sift';

/** The two values the uninstaller reads. Named separately, and never a parent folder (see below). */
export const DATA_VALUE = 'LibraryData';
export const CACHE_VALUE = 'CacheData';
/** The folder libraries are made in. Recorded only where the folder above it is Sift's own. */
export const LIBRARIES_VALUE = 'LibrariesFolder';

/** How a command is run. Injected so a test can read the arguments instead of touching a registry. */
export type Runner = (file: string, args: string[]) => void;

const run: Runner = (file, args) => {
	execFile(file, args, () => {
		/* Deliberately ignored. A registry write that fails means the uninstaller will not offer to
		 * remove the library. It does not mean Sift cannot run, and there is nobody to tell. */
	});
};

/**
 * Record where this installation keeps its library, or clear the record.
 *
 * `null` in client mode, where there is no library on this machine at all: the folders belong to
 * whichever computer is running the server, and offering to delete anything here would be offering
 * to delete a cache that is not the library somebody is thinking of.
 *
 * THE TWO FOLDERS ARE NAMED, NOT THEIR PARENT. `%LOCALAPPDATA%\Sift\data` and `...\cache` share a
 * parent that is Sift's own, but a folder chosen at first run does not: choose `D:\` and the parent
 * of `D:\data` is the whole drive. An uninstaller handed a parent would one day be handed that one.
 */
export function recordForUninstaller(
	where: DataLocations | null,
	exec: Runner = run,
	librariesOf: (dataDir: string) => string = librariesFolderOf
): void {
	if (where === null) {
		exec('reg.exe', ['delete', REGISTRY_KEY, '/f']);
		return;
	}
	exec('reg.exe', [
		'add',
		REGISTRY_KEY,
		'/v',
		DATA_VALUE,
		'/t',
		'REG_SZ',
		'/d',
		where.dataDir,
		'/f'
	]);
	exec('reg.exe', [
		'add',
		REGISTRY_KEY,
		'/v',
		CACHE_VALUE,
		'/t',
		'REG_SZ',
		'/d',
		where.cacheDir,
		'/f'
	]);
	/* The libraries folder, and only where the folder above it is not a drive's root: that is the
	 * folder the models and the first library sit in, and on a drive's root it is the whole drive.
	 * The uninstaller holds the same line on its side; this one means it is never even handed the
	 * value. An older value is deleted rather than left, so a record never outlives its reason. */
	const libraries = librariesOf(where.dataDir);
	// Windows' own path rules whatever runs this: the registry and the uninstaller are Windows'.
	const device = path.win32.dirname(libraries);
	if (path.win32.parse(device).root === device) {
		exec('reg.exe', ['delete', REGISTRY_KEY, '/v', LIBRARIES_VALUE, '/f']);
		return;
	}
	exec('reg.exe', [
		'add',
		REGISTRY_KEY,
		'/v',
		LIBRARIES_VALUE,
		'/t',
		'REG_SZ',
		'/d',
		libraries,
		'/f'
	]);
}

/** How a command is run and READ. Injected so a test never touches a registry. */
export type Reader = (file: string, args: string[]) => Promise<string | null>;

const read: Reader = (file, args) =>
	new Promise((resolve) => {
		execFile(file, args, { windowsHide: true }, (error, stdout) => resolve(error ? null : stdout));
	});

/* `reg.exe query` prints the value as a line of three tab-or-space-separated columns; only the
 * last is wanted and it may itself hold spaces, so it is split at the type column. */
function valueIn(printed: string | null, name: string): string | null {
	if (printed === null) return null;
	for (const line of printed.split(/\r?\n/)) {
		const found = new RegExp(`^\\s*${name}\\s+REG_SZ\\s+(.+?)\\s*$`).exec(line);
		if (found?.[1] !== undefined && found[1] !== '') return found[1];
	}
	return null;
}

/**
 * Where an earlier installation kept its library, when that library is still there.
 *
 * The uninstaller leaves the record in place unless the person asked for the database to go, so
 * a Sift installed again finds the library it had and offers to carry on with it rather than
 * starting an empty one beside it. Only a folder that still holds a database counts: a record
 * pointing at a folder somebody has since cleared out is a record of nothing.
 */
export async function rememberedDataLocation(
	reader: Reader = read,
	exists: (file: string) => boolean = fs.existsSync
): Promise<DataLocations | null> {
	const dataDir = valueIn(
		await reader('reg.exe', ['query', REGISTRY_KEY, '/v', DATA_VALUE]),
		DATA_VALUE
	);
	const cacheDir = valueIn(
		await reader('reg.exe', ['query', REGISTRY_KEY, '/v', CACHE_VALUE]),
		CACHE_VALUE
	);
	if (dataDir === null || cacheDir === null) return null;
	if (!exists(path.join(dataDir, 'sift.sqlite3'))) return null;
	return { dataDir, cacheDir };
}
