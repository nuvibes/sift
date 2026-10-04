/* What the uninstaller is told, and what it is never told.
 *
 * The consequence of getting this wrong is not a broken build: it is an uninstaller that deletes a
 * folder somebody did not mean, or one that claims to have removed everything and did not. Both
 * failures are silent at the moment they happen and only discovered afterwards, which is why these
 * assert the exact arguments rather than that something was run.
 */

import { describe, expect, it } from 'vitest';

import * as path from 'node:path';

import {
	CACHE_VALUE,
	DATA_VALUE,
	LIBRARIES_VALUE,
	recordForUninstaller,
	REGISTRY_KEY,
	rememberedDataLocation
} from './uninstall';

/* The libraries folder the way `librariesFolderOf` finds it, with the mark faked: a data folder at
 * `<folder>\<name>\data` under a folder called `libraries` is a member, anything else is a library
 * of its own with `libraries` beside it. */
function librariesOf(dataDir: string): string {
	const above = path.win32.dirname(path.win32.dirname(dataDir));
	if (path.win32.basename(above) === 'libraries') return above;
	return path.win32.join(path.win32.dirname(dataDir), 'libraries');
}

function record(where: { dataDir: string; cacheDir: string } | null) {
	const calls: { file: string; args: string[] }[] = [];
	recordForUninstaller(where, (file, args) => calls.push({ file, args }), librariesOf);
	return calls;
}

function librariesRecorded(calls: { file: string; args: string[] }[]): string | null {
	const found = calls.find((call) => call.args[0] === 'add' && call.args.includes(LIBRARIES_VALUE));
	return found ? (found.args[found.args.indexOf('/d') + 1] ?? null) : null;
}

describe('recordForUninstaller', () => {
	it('writes both folders, exactly as they were given', () => {
		const calls = record({ dataDir: 'D:\\Sift\\data', cacheDir: 'D:\\Sift\\cache' });
		expect(calls).toHaveLength(3);
		expect(calls[0].args).toEqual([
			'add',
			REGISTRY_KEY,
			'/v',
			DATA_VALUE,
			'/t',
			'REG_SZ',
			'/d',
			'D:\\Sift\\data',
			'/f'
		]);
		expect(calls[1].args).toContain('D:\\Sift\\cache');
		expect(calls[1].args).toContain(CACHE_VALUE);
	});

	/* The one that matters. A parent is one `RMDir /r` away from being a drive root, and the person
	 * who chose `D:\` at first run is exactly the person this would happen to. */
	it('never records a parent folder', () => {
		const calls = record({ dataDir: 'D:\\data', cacheDir: 'D:\\cache' });
		const recorded = calls.flatMap((call) => call.args);
		expect(recorded).toContain('D:\\data');
		expect(recorded).not.toContain('D:\\');
		expect(recorded).not.toContain('D:');
	});

	/* Client mode has no library here. Leaving a stale value behind would offer to delete the cache
	 * of a library that lives on another computer, under a sentence saying "your library". */
	it('clears the record in client mode rather than leaving the last one', () => {
		const calls = record(null);
		expect(calls).toHaveLength(1);
		expect(calls[0].file).toBe('reg.exe');
		expect(calls[0].args).toEqual(['delete', REGISTRY_KEY, '/f']);
	});

	/* The page promises every library in the libraries folder, so the uninstaller has to be told
	 * where that folder is, found the same way from the first library and from any member. A member
	 * standing in for the folder would be deleted alone, and its siblings would stay. */
	it('records the libraries folder, found the same from the first library and from a member', () => {
		const first = record({
			dataDir: 'C:\\Users\\someone\\Sift\\data',
			cacheDir: 'C:\\Users\\someone\\Sift\\cache'
		});
		const member = record({
			dataDir: 'C:\\Users\\someone\\Sift\\libraries\\Holiday clips\\data',
			cacheDir: 'C:\\Users\\someone\\Sift\\libraries\\Holiday clips\\cache'
		});

		expect(librariesRecorded(first)).toBe('C:\\Users\\someone\\Sift\\libraries');
		expect(librariesRecorded(member)).toBe('C:\\Users\\someone\\Sift\\libraries');
	});

	/* On a drive's root the folder above the libraries folder is the whole drive, and the
	 * uninstaller deletes the first library and the models from there. So it is never recorded, and
	 * a value an earlier start wrote is taken away. */
	it('records no libraries folder where the folder above it is a drive root', () => {
		const calls = record({ dataDir: 'D:\\data', cacheDir: 'D:\\cache' });

		expect(librariesRecorded(calls)).toBeNull();
		expect(calls.at(-1)?.args).toEqual(['delete', REGISTRY_KEY, '/v', LIBRARIES_VALUE, '/f']);
	});

	it('writes under the current user, never the machine', () => {
		const calls = record({ dataDir: 'C:\\a', cacheDir: 'C:\\b' });
		expect(REGISTRY_KEY.startsWith('HKCU\\')).toBe(true);
		expect(calls.every((call) => call.args.includes(REGISTRY_KEY))).toBe(true);
	});
});

describe('rememberedDataLocation', () => {
	const printed = (name: string, value: string) =>
		`\r\nHKEY_CURRENT_USER\\Software\\Sift\r\n    ${name}    REG_SZ    ${value}\r\n\r\n`;
	const registry = async (_file: string, args: string[]) =>
		args.includes(DATA_VALUE)
			? printed(DATA_VALUE, 'D:\\My Sift\\data')
			: printed(CACHE_VALUE, 'D:\\My Sift\\cache');

	it('answers the recorded folders when the database is still there', async () => {
		const kept = await rememberedDataLocation(registry, (file) => file.endsWith('sift.sqlite3'));
		expect(kept).toEqual({ dataDir: 'D:\\My Sift\\data', cacheDir: 'D:\\My Sift\\cache' });
	});

	it('answers nothing for a record whose folder no longer holds a database', async () => {
		expect(await rememberedDataLocation(registry, () => false)).toBeNull();
	});

	/* reg.exe answered, but with no line of the shape a value is printed in. */
	it('answers nothing for a printout that carries no value', async () => {
		expect(
			await rememberedDataLocation(
				async () => '\r\nHKEY_CURRENT_USER\\Software\\Sift\r\n\r\n',
				() => true
			)
		).toBeNull();
	});

	it('answers nothing when there is no record', async () => {
		expect(
			await rememberedDataLocation(
				async () => null,
				() => true
			)
		).toBeNull();
	});
});
