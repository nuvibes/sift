/* Moving Sift's own two folders.
 *
 * Every refusal is asserted, because a refusal is the only thing standing between a mistyped path
 * and a library that has been merged into somebody's Documents folder. The move itself is exercised
 * against real directories on the temp disk rather than a mocked filesystem: what is being tested
 * is what `rename` and `copyFile` really do, and a stub of those would be testing the stub.
 */

import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import * as fs from 'node:fs/promises';
import * as os from 'node:os';
import * as path from 'node:path';

import { describe as report, isInside, move, refuse, sizeOf } from './storage';

let root: string;
let current: { dataDir: string; cacheDir: string };

beforeEach(async () => {
	root = await fs.mkdtemp(path.join(os.tmpdir(), 'sift-storage-'));
	current = { dataDir: path.join(root, 'live', 'data'), cacheDir: path.join(root, 'live', 'cache') };
	await fs.mkdir(path.join(current.dataDir, 'quarantine'), { recursive: true });
	await fs.mkdir(current.cacheDir, { recursive: true });
	await fs.writeFile(path.join(current.dataDir, 'sift.sqlite3'), 'x'.repeat(500));
	await fs.writeFile(path.join(current.dataDir, 'quarantine', 'bad.mp4'), 'y'.repeat(50));
	await fs.writeFile(path.join(current.cacheDir, 'thumb.jpg'), 'z'.repeat(100));
});

afterEach(async () => {
	await fs.rm(root, { recursive: true, force: true });
});

async function emptyFolder(name: string): Promise<string> {
	const made = path.join(root, name);
	await fs.mkdir(made, { recursive: true });
	return made;
}

describe('what it will not do', () => {
	it('refuses a path that is not a full one', async () => {
		expect(await refuse(current, 'somewhere')).toMatch(/full path/i);
	});

	it('refuses the folder it is already using', { timeout: 30_000 }, async () => {
		expect(await refuse(current, path.join(root, 'live'))).toMatch(/already/i);
	});

	it('refuses a folder inside the one being moved', async () => {
		expect(await refuse(current, path.join(current.dataDir, 'quarantine'))).toMatch(/inside/i);
	});

	it('refuses a folder that does not exist', async () => {
		expect(await refuse(current, path.join(root, 'nowhere'))).toMatch(/does not exist/i);
	});

	it('refuses a folder with anything already in it', async () => {
		const busy = await emptyFolder('busy');
		await fs.writeFile(path.join(busy, 'holiday.jpg'), 'mine');

		expect(await refuse(current, busy)).toMatch(/not empty/i);
	});

	it('refuses a folder on a network drive, however empty', async () => {
		const fresh = await emptyFolder('fresh');
		expect(await refuse(current, fresh, async () => 'Network')).toMatch(/own drive/i);
	});

	it('accepts an empty folder', async () => {
		expect(await refuse(current, await emptyFolder('fresh'), async () => 'Fixed')).toBeNull();
	});
});

describe('moving', () => {
	it('puts both folders under the new one and leaves nothing behind', async () => {
		const to = await emptyFolder('bigger');

		const result = await move(current, to);

		expect(result.ok).toBe(true);
		if (!result.ok) return;
		expect(result.locations).toEqual({
			dataDir: path.join(to, 'data'),
			cacheDir: path.join(to, 'cache')
		});
		expect(await fs.readFile(path.join(to, 'data', 'sift.sqlite3'), 'utf8')).toHaveLength(500);
		// The tree, not only the top level: quarantine sits inside the data folder.
		expect(await fs.readFile(path.join(to, 'data', 'quarantine', 'bad.mp4'), 'utf8')).toHaveLength(50);
		expect(await fs.readFile(path.join(to, 'cache', 'thumb.jpg'), 'utf8')).toHaveLength(100);
		await expect(fs.access(current.dataDir)).rejects.toThrow();
		await expect(fs.access(current.cacheDir)).rejects.toThrow();
	});

	it('leaves everything exactly where it was when the target is refused', async () => {
		const busy = await emptyFolder('busy2');
		await fs.writeFile(path.join(busy, 'theirs.txt'), 'not mine');

		const result = await move(current, busy);

		expect(result.ok).toBe(false);
		// The refusal has to be decided before anything is touched: the backend is already down
		// by the time this runs, and a half-moved library is the one state with no way back.
		expect(await sizeOf(current.dataDir)).toBe(550);
		expect(await fs.readdir(busy)).toEqual(['theirs.txt']);
	});

	it('moves the per-device model store and the other libraries that sit beside the data folder', async () => {
		// The models and the other libraries live beside `data`; a library moved without them
		// arrives with no models and no way to open the libraries it had.
		const live = path.dirname(current.dataDir);
		await fs.mkdir(path.join(live, 'models', 'faces'), { recursive: true });
		await fs.writeFile(path.join(live, 'models', 'faces', 'weights.bin'), 'w'.repeat(40));
		await fs.mkdir(path.join(live, 'libraries', 'other'), { recursive: true });
		await fs.writeFile(path.join(live, 'libraries', 'other', 'sift.sqlite3'), 'z'.repeat(10));
		const to = await emptyFolder('with-siblings');
		const seen: number[] = [];

		const result = await move(current, to, (progress) => seen.push(progress.total));

		expect(result.ok).toBe(true);
		expect(seen[0]).toBe(700);
		expect(await fs.readFile(path.join(to, 'models', 'faces', 'weights.bin'), 'utf8')).toBe(
			'w'.repeat(40)
		);
		expect(await fs.readFile(path.join(to, 'libraries', 'other', 'sift.sqlite3'), 'utf8')).toBe(
			'z'.repeat(10)
		);
		await expect(fs.stat(path.join(live, 'models'))).rejects.toThrow();
		await expect(fs.stat(path.join(live, 'libraries'))).rejects.toThrow();
	});

	it('says how much it has moved', async () => {
		const to = await emptyFolder('watched');
		const seen: number[] = [];

		await move(current, to, (progress) => seen.push(progress.total));

		// A rename copies nothing, so the only promise is that the size it is about to move is
		// known before it starts. 650 is the three files above.
		expect(seen[0]).toBe(650);
	});
});

describe('reading what is there', () => {
	it('adds up a tree', async () => {
		expect(await sizeOf(current.dataDir)).toBe(550);
	});

	it('reports a folder that does not exist as empty rather than failing', async () => {
		expect(await sizeOf(path.join(root, 'never'))).toBe(0);
	});

	it('describes both folders and their sizes', async () => {
		expect(await report(current)).toEqual({ ...current, dataBytes: 550, cacheBytes: 100 });
	});
});

describe('isInside', () => {
	it('is true for the folder itself', () => {
		expect(isInside('C:\\sift\\data', 'C:\\sift\\data')).toBe(true);
	});

	it('is true for something under it, whatever the case', () => {
		expect(isInside('C:\\Sift\\data', 'c:\\sift\\DATA\\quarantine')).toBe(true);
	});

	it('is false for a sibling whose name merely starts the same way', () => {
		// The separator is what makes this false. Without it `data-old` reads as inside `data`.
		expect(isInside(path.join('a', 'data'), path.join('a', 'data-old'))).toBe(false);
	});
});
