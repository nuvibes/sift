/* Where Sift keeps its own two folders, and moving them somewhere else. */

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
	/** What the two folders hold, in bytes, as last measured (`StorageSizes`). */
	dataBytes: number;
	cacheBytes: number;
	/** When those sizes were measured, in ms since 1970; null before the first walk has finished. */
	measuredAt: number | null;
	/** Whether a walk is running now. */
	measuring: boolean;
}

export type MoveResult =
	{ ok: true; locations: DataLocations; renamed: boolean } | { ok: false; reason: string };

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
				/* A file that went between the listing and the stat is a file that is not there
				   to count. */
			}
		}
	}
	return total;
}

/** The folders under the data folder that hold one face crop per file, all near one size: sized
 *  from their count and a sample, where a stat each is a hundred thousand reads on a large library. */
const CROPS = [path.join('faces', 'detected'), path.join('faces', 'references')];
const SAMPLE = 256;
/** Stats asked together, so a folder of thumbnails is not one disk read after another. */
const LANES = 32;

async function sizesOf(files: string[]): Promise<number> {
	let total = 0;
	let next = 0;
	const lane = async (): Promise<void> => {
		while (next < files.length) {
			const file = files[next++] as string;
			try {
				const { size } = await fsp.stat(file);
				total += size;
			} catch {
				/* Gone since the listing: not there to count. */
			}
		}
	};
	await Promise.all(Array.from({ length: Math.min(LANES, files.length) }, lane));
	return total;
}

/** Everything under `dir` in bytes for the screen, a crops folder by its count times a sampled
 *  mean. A move checks its copy with the exact `sizeOf`. */
async function measured(dir: string, crops: ReadonlySet<string>, inCrops = false): Promise<number> {
	let entries: fs.Dirent[];
	try {
		entries = await fsp.readdir(dir, { withFileTypes: true });
	} catch {
		return 0;
	}
	const files = entries.filter((one) => one.isFile()).map((one) => path.join(dir, one.name));
	let total = 0;
	if (inCrops && files.length > SAMPLE) {
		/* In name order, so a folder that has not changed gives the same figure every walk. */
		files.sort();
		const step = files.length / SAMPLE;
		const sample = Array.from(
			{ length: SAMPLE },
			(_, at) => files[Math.floor(at * step)] as string
		);
		total = Math.round(((await sizesOf(sample)) / SAMPLE) * files.length);
	} else total = await sizesOf(files);
	for (const entry of entries.filter((one) => one.isDirectory())) {
		const inner = path.join(dir, entry.name);
		total += await measured(inner, crops, inCrops || crops.has(inner));
	}
	return total;
}

/** The two folders' sizes, walked now. */
export async function measure(
	locations: DataLocations
): Promise<{ dataBytes: number; cacheBytes: number }> {
	const crops = new Set(CROPS.map((one) => path.join(locations.dataDir, one)));
	return {
		dataBytes: await measured(locations.dataDir, crops),
		cacheBytes: await measured(locations.cacheDir, crops)
	};
}

export async function describe(locations: DataLocations): Promise<StorageReport> {
	return { ...locations, ...(await measure(locations)), measuredAt: Date.now(), measuring: false };
}

/** How old a measurement may be before an ask walks again. */
export const SIZES_KEPT_MS = 10 * 60_000;

/** The storage sizes, answered immediately from the last walk. */
export class StorageSizes {
	private last: { key: string; dataBytes: number; cacheBytes: number; measuredAt: number } | null =
		null;
	private walk: Promise<void> | null = null;

	constructor(
		private readonly walkFolders: typeof measure = measure,
		private readonly now: () => number = Date.now
	) {}

	read(locations: DataLocations): StorageReport {
		const key = `${locations.dataDir}\n${locations.cacheDir}`;
		const known = this.last?.key === key ? this.last : null;
		const stale = known === null || this.now() - known.measuredAt >= SIZES_KEPT_MS;
		if (stale && this.walk === null) this.walk = this.measure(locations, key);
		return {
			...locations,
			dataBytes: known?.dataBytes ?? 0,
			cacheBytes: known?.cacheBytes ?? 0,
			measuredAt: known?.measuredAt ?? null,
			measuring: this.walk !== null
		};
	}

	/** Settles when the walk running now does: for a caller that wants the fresh figure. */
	settled(): Promise<void> {
		return this.walk ?? Promise.resolve();
	}

	private async measure(locations: DataLocations, key: string): Promise<void> {
		try {
			const sizes = await this.walkFolders(locations);
			this.last = { key, ...sizes, measuredAt: this.now() };
		} catch {
			/* Left as it was: the next ask walks again. */
		} finally {
			this.walk = null;
		}
	}
}

/** Whether `inner` is `outer` or sits inside it, compared as paths rather than by asking the
 * disk. */
export function isInside(outer: string, inner: string): boolean {
	const a = path.resolve(outer).toLowerCase();
	const b = path.resolve(inner).toLowerCase();
	return a === b || b.startsWith(a + path.sep);
}

/** Why this folder cannot be used, or null. Every one of these is decided before anything is
 * touched. */
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

/** Copy a tree, reporting bytes as they land. A folder that cannot be listed throws rather than
 * being stepped over. */
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
		/* Anything else (a symlink, a socket) is skipped rather than followed. */
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

/** Move both folders under `target`, which becomes their new parent. */
export async function move(
	current: DataLocations,
	target: string,
	onProgress: (progress: MoveProgress) => void = () => {}
): Promise<MoveResult> {
	const reason = await refuse(current, target);
	if (reason !== null) return { ok: false, reason };

	/* The per-device model store and the other libraries sit beside the data folder, and a
	   library moved without them arrives with no models (2.3 GB fetched again) and no way to
	   open the libraries it had. */
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
		/* The CACHE first, and the order is the whole of the safety here. */
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
