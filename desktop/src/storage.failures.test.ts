/* What `storage.ts` does when the filesystem answers badly: the half `storage.test.ts` cannot
 * reach, because it works against real directories that behave. */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as os from 'node:os';
import * as path from 'node:path';

const fake = vi.hoisted(() => ({
	/** The error `rename` should throw, or null to let the real one run. */
	renameCode: null as string | null,
	/** The error `readdir` should throw, or null to let the real one run. */
	readdirCode: null as string | null,
	/** Which path `readdirCode` applies to, matched as a substring. */
	readdirFailsOn: 'other-drive',
	/** True to make the write probe fail, as a folder somebody cannot write to would. */
	refuseWrites: false,
	/** A file name whose `stat` should fail, as one deleted between the listing and the stat. */
	vanishing: null as string | null,
	/** A file name whose copy should land SHORT, to make the whole copy come up short. */
	truncateCopyOf: null as string | null,
	/** Every file the move copied, in the order it copied them. */
	copied: [] as string[]
}));

vi.mock('node:fs/promises', async (importOriginal) => {
	const actual = await importOriginal<typeof import('node:fs/promises')>();
	return {
		...actual,
		default: actual,
		async rename(from: string, to: string) {
			if (fake.renameCode === null) return actual.rename(from, to);
			const error = new Error('rename refused') as NodeJS.ErrnoException;
			error.code = fake.renameCode;
			throw error;
		},
		async readdir(where: string, options?: unknown) {
			if (fake.readdirCode !== null && where.includes(fake.readdirFailsOn)) {
				const error = new Error('readdir refused') as NodeJS.ErrnoException;
				error.code = fake.readdirCode;
				throw error;
			}
			return actual.readdir(where, options as never);
		},
		async writeFile(where: string, what: string) {
			if (fake.refuseWrites) throw new Error('read-only');
			return actual.writeFile(where, what);
		},
		async stat(where: string) {
			if (fake.vanishing !== null && where.endsWith(fake.vanishing)) {
				const error = new Error('gone') as NodeJS.ErrnoException;
				error.code = 'ENOENT';
				throw error;
			}
			return actual.stat(where);
		},
		async copyFile(from: string, to: string) {
			fake.copied.push(from);
			/* A short write rather than no write at all. */
			if (fake.truncateCopyOf !== null && from.endsWith(fake.truncateCopyOf)) {
				return actual.writeFile(to, 'short');
			}
			return actual.copyFile(from, to);
		}
	};
});

const fs = await vi.importActual<typeof import('node:fs/promises')>('node:fs/promises');
const { move, refuse, sizeOf } = await import('./storage');

let root: string;
let current: { dataDir: string; cacheDir: string };

beforeEach(async () => {
	fake.renameCode = 'EXDEV';
	fake.truncateCopyOf = null;
	fake.readdirCode = null;
	fake.readdirFailsOn = 'other-drive';
	fake.refuseWrites = false;
	fake.vanishing = null;
	fake.copied.length = 0;
	root = await fs.mkdtemp(path.join(os.tmpdir(), 'sift-xdev-'));
	current = { dataDir: path.join(root, 'live', 'data'), cacheDir: path.join(root, 'live', 'cache') };
	await fs.mkdir(path.join(current.dataDir, 'quarantine'), { recursive: true });
	await fs.mkdir(current.cacheDir, { recursive: true });
	await fs.writeFile(path.join(current.dataDir, 'sift.sqlite3'), 'x'.repeat(500));
	await fs.writeFile(path.join(current.dataDir, 'quarantine', 'bad.mp4'), 'y'.repeat(50));
	await fs.writeFile(path.join(current.cacheDir, 'thumb.jpg'), 'z'.repeat(100));
});

afterEach(async () => {
	await fs.rm(root, { recursive: true, force: true });
	vi.clearAllMocks();
});

async function emptyFolder(name: string): Promise<string> {
	const made = path.join(root, name);
	await fs.mkdir(made, { recursive: true });
	return made;
}

async function there(where: string): Promise<boolean> {
	return fs
		.stat(where)
		.then(() => true)
		.catch(() => false);
}

describe('a move to another drive', () => {
	it('copies both folders, keeps what is inside them, and takes the old ones away', { timeout: 30_000 }, async () => {
		const target = await emptyFolder('other-drive');

		const outcome = await move(current, target);

		expect(outcome).toMatchObject({ ok: true, renamed: false });
		expect(await there(path.join(target, 'data', 'sift.sqlite3'))).toBe(true);
		expect(await there(path.join(target, 'data', 'quarantine', 'bad.mp4'))).toBe(true);
		expect(await there(path.join(target, 'cache', 'thumb.jpg'))).toBe(true);
		expect(await there(current.dataDir)).toBe(false);
		expect(await there(current.cacheDir)).toBe(false);
	});

	it('says a copy happened rather than claiming the rename it could not do', { timeout: 30_000 }, async () => {
		const target = await emptyFolder('other-drive');

		const outcome = await move(current, target);

		expect(outcome).toEqual({
			ok: true,
			renamed: false,
			locations: { dataDir: path.join(target, 'data'), cacheDir: path.join(target, 'cache') }
		});
	});

	it('reports bytes as they land, up to what it measured before it started', async () => {
		const target = await emptyFolder('other-drive');
		const seen: { copied: number; total: number }[] = [];

		await move(current, target, (progress) => seen.push({ ...progress }));

		expect(seen[0]).toEqual({ copied: 0, total: 650 });
		expect(seen.at(-1)).toEqual({ copied: 650, total: 650 });
		/* Never backwards, or a bar would jump about. */
		for (let step = 1; step < seen.length; step += 1) {
			expect(seen[step].copied).toBeGreaterThanOrEqual(seen[step - 1].copied);
		}
	});

	it('moves the rebuildable half first, so a failure costs thumbnails and not the database', async () => {
		const target = await emptyFolder('other-drive');

		await move(current, target);

		/* The order is the whole of the safety here. Moving the database first would put the
		   irreplaceable half in the risky position for no reason at all. */
		const which = fake.copied.map((one) => (one.includes(`${path.sep}cache${path.sep}`) ? 'cache' : 'data'));
		expect(which[0]).toBe('cache');
		expect(which.lastIndexOf('cache')).toBeLessThan(which.indexOf('data'));
	});
});

describe('when it cannot finish', () => {
	it('refuses when the copy is SMALLER than what it came from, and keeps the original', async () => {
		const target = await emptyFolder('other-drive');
		fake.truncateCopyOf = 'sift.sqlite3';

		const outcome = await move(current, target);

		expect(outcome).toMatchObject({ ok: false });
		expect((outcome as { reason: string }).reason).toContain('the copy is smaller than what it came from');
		expect((outcome as { reason: string }).reason).toContain('Nothing was lost');
		/* The database is still where it was, which is the whole promise. */
		expect(await there(path.join(current.dataDir, 'sift.sqlite3'))).toBe(true);
		expect(await sizeOf(current.dataDir)).toBe(550);
	});

	it('does not turn a rename that failed for another reason into a copy', async () => {
		const target = await emptyFolder('other-drive');
		fake.renameCode = 'EACCES';

		const outcome = await move(current, target);

		expect(outcome).toMatchObject({ ok: false });
		expect(await there(path.join(current.cacheDir, 'thumb.jpg'))).toBe(true);
		expect(await there(path.join(target, 'cache', 'thumb.jpg'))).toBe(false);
	});
});

describe('refusals that need the disk to be in a bad mood', () => {
	it('tells somebody who picked a FILE that it is a file', async () => {
		const file = path.join(root, 'a-file.txt');
		await fs.writeFile(file, 'x');

		expect(await refuse(current, file)).toBe('That is a file, not a folder.');
	});

	it('says it could not read the folder, rather than naming an errno', async () => {
		const target = await emptyFolder('other-drive');
		fake.readdirCode = 'EACCES';

		expect(await refuse(current, target)).toBe('Sift could not read that folder.');
	});

	it('refuses a folder it cannot write to, BEFORE anything has been stopped', async () => {
		/* The write test is the last of the refusals and the only one that finds out by trying. */
		const target = await emptyFolder('other-drive');
		fake.refuseWrites = true;

		expect(await refuse(current, target)).toBe('Sift cannot write to that folder.');
	});

	it('counts a file that went between the listing and the stat as nothing rather than failing', async () => {
		/* The number is a size on screen, not an invariant. */
		fake.vanishing = 'thumb.jpg';

		expect(await sizeOf(current.cacheDir)).toBe(0);
	});
});

describe('a source folder that cannot be listed', () => {
	it('refuses and leaves the originals where they are, rather than deleting what it never copied', async () => {
		/* A folder that cannot be listed must fail the move. */
		const target = await emptyFolder('other-drive');
		fake.readdirCode = 'EACCES';
		fake.readdirFailsOn = `${path.sep}quarantine`;

		const outcome = await move(current, target);

		expect(outcome).toMatchObject({ ok: false });
		expect((outcome as { reason: string }).reason).toContain('Nothing was lost');
		expect(await there(path.join(current.dataDir, 'quarantine', 'bad.mp4'))).toBe(true);
		expect(await there(path.join(current.dataDir, 'sift.sqlite3'))).toBe(true);
	});
});
