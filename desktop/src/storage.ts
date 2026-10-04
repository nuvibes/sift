/* Where Sift keeps its own two folders, and moving them somewhere else.
 *
 * A library outgrows the disk it was started on (the ordinary case, not an edge one), so the
 * folders chosen at first run can be moved.
 *
 * ## The two folders are moved together
 *
 * They could be separated: the cache is rebuildable and the data is not. It is not offered
 * because it doubles every failure path (two moves, two rollbacks, four half-finished states)
 * for a choice almost nobody makes, and the shape below takes a second folder without changing if
 * it is ever wanted. What is offered is the question people ask, which is "this drive is full".
 *
 * ## What makes this safe
 *
 * Nothing here runs while the backend is up: the caller stops it first, and the database's files
 * are open until it does. Then, in order:
 *
 *   1. every refusal is decided BEFORE anything is touched, so a bad target costs nothing;
 *   2. a rename is tried first: on the same volume it is atomic and instant, and the old path
 *      simply stops existing;
 *   3. across volumes it is a copy, then a check that the copy is all there, and only then the
 *      delete. The old copy is what survives if anything goes wrong, because the old copy is the
 *      one with the library in it.
 *
 * The settings file is written LAST, after the bytes are in place. A crash before that leaves a
 * shell pointing at the old folder, which still exists in the copy case and has been renamed in
 * the other, so the one state that has to be impossible is "settings say the new place and the
 * files are in the old one", and writing last is what makes it impossible.
 */

import * as fs from 'node:fs';
import * as fsp from 'node:fs/promises';
import * as path from 'node:path';

import { isRemoteDrive, KEEP_IT_HERE, type DriveKindAsker } from './drives';
import type { DataLocations } from './paths';

/** What sits beside a library's data folder and moves with it: the per-device model store and
 *  the other libraries. */
const SIBLINGS = ['models', 'libraries'] as const;

/** What a move is up to, so a screen can say something other than "working". */
export interface MoveProgress {
	/** Bytes copied so far. Zero for a rename, which does not copy. */
	copied: number;
	/** Bytes to copy in total, as measured before it started. */
	total: number;
}

export interface StorageReport extends DataLocations {
	/** What the two folders hold, in bytes. Measured, not stored: it changes constantly. */
	dataBytes: number;
	cacheBytes: number;
}

export type MoveResult =
	| { ok: true; locations: DataLocations; renamed: boolean }
	| { ok: false; reason: string };

/** Everything under a folder, in bytes. Missing is zero rather than an error: a cache folder that
 *  has not been made yet is an ordinary state, not a fault. */
export async function sizeOf(dir: string): Promise<number> {
	let total = 0;
	let entries: fs.Dirent[];
	try {
		entries = await fsp.readdir(dir, { withFileTypes: true });
	} catch {
		return 0;
	}
	for (const entry of entries) {
		const full = path.join(dir, entry.name);
		if (entry.isDirectory()) {
			total += await sizeOf(full);
		} else if (entry.isFile()) {
			try {
				total += (await fsp.stat(full)).size;
			} catch {
				/* A file that went between the listing and the stat is a file that is not there to
				   count. The number is a size on screen, not an invariant. */
			}
		}
	}
	return total;
}

export async function describe(locations: DataLocations): Promise<StorageReport> {
	return {
		...locations,
		dataBytes: await sizeOf(locations.dataDir),
		cacheBytes: await sizeOf(locations.cacheDir)
	};
}

/** Whether `inner` is `outer` or sits inside it, compared as paths rather than by asking the disk.
 *
 * Lexical on purpose. `fs.realpath` would follow a junction, and Windows makes junctions freely:
 * a target that resolves outside itself would then pass a check that is supposed to be about where
 * the files END UP. Case-insensitively, because Windows is. */
export function isInside(outer: string, inner: string): boolean {
	const a = path.resolve(outer).toLowerCase();
	const b = path.resolve(inner).toLowerCase();
	return a === b || b.startsWith(a + path.sep);
}

/**
 * Why this folder cannot be used, or null.
 *
 * Every one of these is decided before anything is touched. A refusal after the backend has been
 * stopped is a Sift that is down for a reason the person could have been told a moment earlier.
 */
export async function refuse(
	current: DataLocations,
	target: string,
	ask?: DriveKindAsker
): Promise<string | null> {
	if (!path.isAbsolute(target)) return 'That is not a full path to a folder.';
	/* Before anything on the disk is looked at: the database cannot live on a network drive, and
	 * that is true of an empty writable folder there as much as of any other. */
	if (await isRemoteDrive(target, ask)) return KEEP_IT_HERE;

	const holder = path.dirname(path.resolve(current.dataDir));
	if (path.resolve(target).toLowerCase() === holder.toLowerCase()) {
		return 'Sift already keeps its files there.';
	}
	/* Into itself, which a rename would either refuse or half-perform depending on the platform. */
	if (isInside(current.dataDir, target) || isInside(current.cacheDir, target)) {
		return 'That folder is inside the one Sift is using now. Choose one outside it.';
	}

	let entries: string[];
	try {
		entries = await fsp.readdir(target);
	} catch (error) {
		const code = (error as NodeJS.ErrnoException).code;
		if (code === 'ENOENT') return 'That folder does not exist.';
		if (code === 'ENOTDIR') return 'That is a file, not a folder.';
		return 'Sift could not read that folder.';
	}
	if (entries.length > 0) {
		/* An empty folder, and nothing else. Anything already in there is somebody's, and a move
		   that merges into it cannot be undone by moving back out. */
		return 'That folder is not empty. Choose an empty one, or make a new one.';
	}

	try {
		const probe = path.join(target, '.sift-write-test');
		await fsp.writeFile(probe, '');
		await fsp.rm(probe);
	} catch {
		return 'Sift cannot write to that folder.';
	}
	return null;
}

/**
 * Copy a tree, reporting bytes as they land.
 *
 * A folder that cannot be listed throws rather than being stepped over. `sizeOf` reports an
 * unlistable folder as nought (rightly, for a size on screen), so a skipped folder would pass
 * the "is the copy all there" check with nought against nought, and the delete afterwards would
 * take the originals.
 *
 * The folder is known to exist: its parent listed it as a directory a moment ago. So a refusal here
 * is a real fault (a permission, a failing disk, a folder pulled out from under the move), and
 * every one of those is a reason to stop with the original still in place.
 */
async function copyTree(from: string, to: string, seen: (bytes: number) => void): Promise<void> {
	await fsp.mkdir(to, { recursive: true });
	const entries: fs.Dirent[] = await fsp.readdir(from, { withFileTypes: true });
	for (const entry of entries) {
		const source = path.join(from, entry.name);
		const destination = path.join(to, entry.name);
		if (entry.isDirectory()) {
			await copyTree(source, destination, seen);
		} else if (entry.isFile()) {
			await fsp.copyFile(source, destination);
			seen((await fsp.stat(destination)).size);
		}
		/* Anything else (a symlink, a socket) is skipped rather than followed. Sift puts none
		   there, and following one would copy something from outside the folder being moved. */
	}
}

async function moveOne(
	from: string,
	to: string,
	seen: (bytes: number) => void
): Promise<'renamed' | 'copied'> {
	await fsp.mkdir(path.dirname(to), { recursive: true });
	try {
		await fsp.rename(from, to);
		return 'renamed';
	} catch (error) {
		if ((error as NodeJS.ErrnoException).code !== 'EXDEV') throw error;
	}
	await copyTree(from, to, seen);
	if ((await sizeOf(to)) < (await sizeOf(from))) {
		throw new Error('the copy is smaller than what it came from');
	}
	await fsp.rm(from, { recursive: true, force: true });
	return 'copied';
}

/**
 * Move both folders under `target`, which becomes their new parent.
 *
 * The caller has stopped the backend and will start it again. On any failure the old folders are
 * left where they were and the settings are not written, so starting the backend again puts things
 * back exactly as they were.
 */
export async function move(
	current: DataLocations,
	target: string,
	onProgress: (progress: MoveProgress) => void = () => {}
): Promise<MoveResult> {
	const reason = await refuse(current, target);
	if (reason !== null) return { ok: false, reason };

	/* The per-device model store and the other libraries sit beside the data folder, and a
	   library moved without them arrives with no models (2.3 GB fetched again) and no way to
	   open the libraries it had. They move with it, when they exist. */
	const beside = SIBLINGS.filter((name) =>
		fs.existsSync(path.join(path.dirname(current.dataDir), name))
	);
	let total = (await sizeOf(current.dataDir)) + (await sizeOf(current.cacheDir));
	for (const name of beside) total += await sizeOf(path.join(path.dirname(current.dataDir), name));
	let copied = 0;
	const seen = (bytes: number) => {
		copied += bytes;
		onProgress({ copied, total });
	};

	const wanted: DataLocations = {
		dataDir: path.join(target, 'data'),
		cacheDir: path.join(target, 'cache')
	};
	onProgress({ copied: 0, total });
	try {
		/* The CACHE first, and the order is the whole of the safety here. It is the rebuildable
		   half: a failure part way through costs thumbnails, which Sift makes again. Moving the
		   database first would put the irreplaceable half in the risky position for no reason. */
		const cache = await moveOne(current.cacheDir, wanted.cacheDir, seen);
		let allRenamed = cache === 'renamed';
		for (const name of beside) {
			const how = await moveOne(
				path.join(path.dirname(current.dataDir), name),
				path.join(target, name),
				seen
			);
			allRenamed = allRenamed && how === 'renamed';
		}
		const data = await moveOne(current.dataDir, wanted.dataDir, seen);
		return { ok: true, locations: wanted, renamed: allRenamed && data === 'renamed' };
	} catch (error) {
		return {
			ok: false,
			reason:
				`Sift could not move its files: ${(error as Error).message}. ` +
				'Nothing was lost \u2014 it is still using the folder it was.'
		};
	}
}
