/* Telling the uninstaller where this installation put things. */

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

/** Record where this installation keeps its library, or clear the record. */
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
	/* The libraries folder, and only where the folder above it is not a drive's root: that is
	 * the folder the models and the first library sit in, and on a drive's root it is the whole
	 * drive. */
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

/** Where an earlier installation kept its library, when that library is still there. */
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
