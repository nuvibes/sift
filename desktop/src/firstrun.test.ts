/* The two questions asked before a backend exists, and what each answer does.
 *
 * The questions are Sift's own screens, served under the shell's scheme like `/connect`, so what
 * is tested here is the part that is not a screen: where the library would go, the machine's own
 * folder dialog, whether a folder can be written to, and which question is still outstanding.
 *
 * `firstRunRoute` is the one with teeth. It decides, twice per launch, whether setup is finished,
 * and a wrong answer either loops somebody on a screen they have already answered or starts a
 * backend with nowhere to put a database.
 */

import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { folderAnswers, paths as stubPaths, resetElectronStub } from '../test/electron-stub';
import {
	assertWritable,
	firstRunRoute,
	needsFirstRun,
	offeredDataLocation,
	pickDataLocation,
	refuseLocation,
	suggestedDataLocation
} from './firstrun';
import { load } from './settings';

let profile: string;

beforeEach(() => {
	resetElectronStub();
	profile = fs.mkdtempSync(path.join(os.tmpdir(), 'sift-firstrun-'));
	stubPaths.userData = profile;
	stubPaths.appData = path.join(profile, 'Roaming');
});

afterEach(() => {
	fs.rmSync(profile, { recursive: true, force: true });
});

describe('suggestedDataLocation', () => {
	it('puts the library under this machine\'s own local application data', () => {
		expect(suggestedDataLocation().dataDir.endsWith(path.join('Local', 'Sift', 'data'))).toBe(true);
	});

	/* Beside it rather than inside it. The cache can be deleted safely and the data cannot, so a
	 * cache nested under the data folder would make "delete the cache" a gesture that could take the
	 * library with it. */
	it('keeps the cache beside the data rather than inside it', () => {
		const { dataDir, cacheDir } = suggestedDataLocation();
		expect(path.dirname(cacheDir)).toBe(path.dirname(dataDir));
		expect(cacheDir.startsWith(dataDir)).toBe(false);
	});
});

describe('pickDataLocation', () => {
	it('puts data and cache side by side under a folder somebody picks', async () => {
		folderAnswers.push(['D:\\Media\\Sift']);
		expect(await pickDataLocation()).toEqual({
			dataDir: 'D:\\Media\\Sift\\data',
			cacheDir: 'D:\\Media\\Sift\\cache'
		});
	});

	/* Cancelling is not the same as agreeing to the default. Somebody who opened the picker wanted a
	 * different folder, and silently keeping the suggestion would put their library where they had
	 * just declined to put it, so this answers nothing and the screen stays where it was. */
	it('treats a cancelled picker as no answer at all', async () => {
		folderAnswers.push(null);
		expect(await pickDataLocation()).toBeNull();
	});
});

describe('firstRunRoute', () => {
	it('asks which way to run first, on a fresh install', () => {
		expect(firstRunRoute(load())).toBe('/start');
	});

	it('asks where the library goes once standalone is chosen', () => {
		expect(firstRunRoute({ ...load(), mode: 'standalone' })).toBe('/library-location');
	});

	/* Client mode has no local library to place, so there is no second question. A route here would
	 * be a screen asking where to put a database that is never going to exist. */
	it('has nothing more to ask in client mode', () => {
		expect(firstRunRoute({ ...load(), mode: 'client' })).toBeNull();
	});

	it('has nothing more to ask once standalone has both answers', () => {
		expect(firstRunRoute({ ...load(), mode: 'standalone', dataDir: 'D:\\d' })).toBeNull();
	});

	/* The two have to agree in both directions: a route means there is a question, and no route
	 * means there is not. They are read at different moments (one at startup, one after each
	 * answer), and a disagreement is either a loop or a backend started with nowhere to write. */
	it('answers a route exactly when first run is not finished', () => {
		const settings = [
			load(),
			{ ...load(), mode: 'standalone' as const },
			{ ...load(), mode: 'client' as const },
			{ ...load(), mode: 'standalone' as const, dataDir: 'D:\\d' }
		];
		for (const one of settings) {
			expect(firstRunRoute(one) !== null).toBe(needsFirstRun(one));
		}
	});
});

describe('assertWritable', () => {
	it('says nothing when both folders can be written', () => {
		const where = path.join(profile, 'lib');
		expect(
			assertWritable({ dataDir: path.join(where, 'data'), cacheDir: path.join(where, 'cache') })
		).toBeNull();
	});

	it('creates the folders rather than requiring them to exist', () => {
		const where = path.join(profile, 'made', 'on', 'demand');
		assertWritable({ dataDir: where, cacheDir: `${where}-cache` });
		expect(fs.existsSync(where)).toBe(true);
	});

	/* A path that cannot be a folder because a FILE is already sitting on the name. Reachable, and
	 * platform-independent, unlike a permissions test, which Windows does not honour. */
	it('names the folder it could not write to', () => {
		const blocked = path.join(profile, 'in-the-way');
		fs.writeFileSync(blocked, 'not a folder', 'utf8');
		const complaint = assertWritable({ dataDir: blocked, cacheDir: profile });
		expect(complaint).toContain(blocked);
	});
});

describe('offeredDataLocation', () => {
	it('offers the library an earlier installation kept, as one to carry on with', async () => {
		const kept = { dataDir: 'D:\\Sift\\data', cacheDir: 'D:\\Sift\\cache' };
		expect(await offeredDataLocation(async () => kept)).toEqual({ locations: kept, existing: true });
	});

	it('offers the default when nothing was kept', async () => {
		const offered = await offeredDataLocation(async () => null);
		expect(offered.existing).toBe(false);
		expect(offered.locations).toEqual(suggestedDataLocation());
	});

	/* A library in the default folder with no record of it. Offered as "Default", the next screen
	   is that library's sign-in, which reads as a fresh install landing on a login. */
	it('offers a library already in the default folder as the library it is', async () => {
		const { dataDir } = suggestedDataLocation();
		fs.mkdirSync(dataDir, { recursive: true });
		fs.writeFileSync(path.join(dataDir, 'sift.sqlite3'), '');
		const offered = await offeredDataLocation(async () => null);
		expect(offered).toEqual({ locations: suggestedDataLocation(), existing: true });
	});
});

describe('refuseLocation', () => {
	it('refuses a library on a network drive before looking at the folder', async () => {
		const where = path.join(profile, 'shared');
		const said = await refuseLocation(
			{ dataDir: path.join(where, 'data'), cacheDir: path.join(where, 'cache') },
			async () => 'Network'
		);
		expect(said).toMatch(/own drive/i);
		expect(fs.existsSync(where)).toBe(false);
	});

	it('is the writable check for a folder on this computer', async () => {
		const where = path.join(profile, 'lib');
		expect(
			await refuseLocation(
				{ dataDir: path.join(where, 'data'), cacheDir: path.join(where, 'cache') },
				async () => 'Fixed'
			)
		).toBeNull();
	});
});

describe('needsFirstRun', () => {
	it('is true on a fresh install', () => {
		expect(needsFirstRun(load())).toBe(true);
	});

	it('is true when standalone was chosen but no folder was settled', () => {
		expect(needsFirstRun({ ...load(), mode: 'standalone' })).toBe(true);
	});

	/* Client mode never needs a data folder (there is no local backend to give one to), so
	 * asking for one would be asking a question with no consequence. */
	it('is false for client mode with no data folder', () => {
		expect(needsFirstRun({ ...load(), mode: 'client' })).toBe(false);
	});

	it('is false once standalone has both answers', () => {
		expect(needsFirstRun({ ...load(), mode: 'standalone', dataDir: 'D:\\d' })).toBe(false);
	});
});
