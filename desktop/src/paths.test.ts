/* Where the application decides to keep somebody's library.
 *
 * These are one-line functions and they are tested anyway, because the line they return is a path
 * on somebody's disk. `%APPDATA%` instead of `%LOCALAPPDATA%` puts a media database and its
 * transcode cache into a roaming profile, which some backup tools and some domain policies then
 * copy between machines: a fault nobody would find by looking at the code.
 */

import * as path from 'node:path';

import { beforeEach, describe, expect, it } from 'vitest';

import { app, paths as stubPaths, resetElectronStub } from '../test/electron-stub';
import {
	appIconFile,
	backendLogFile,
	bundlePaths,
	defaultDataLocations,
	dragAddonFile,
	settingsFile,
	trayIconFile
} from './paths';

beforeEach(() => {
	resetElectronStub();
});

describe('defaultDataLocations', () => {
	it('lands under Local, never Roaming', () => {
		stubPaths.appData = path.join('C:', 'Users', 'someone', 'AppData', 'Roaming');
		const { dataDir, cacheDir } = defaultDataLocations();
		const local = path.join('C:', 'Users', 'someone', 'AppData', 'Local', 'Sift');
		expect(dataDir).toBe(path.join(local, 'data'));
		expect(cacheDir).toBe(path.join(local, 'cache'));
	});

	it('keeps the two apart, because one can be rebuilt and one cannot', () => {
		stubPaths.appData = path.join('C:', 'p', 'Roaming');
		const { dataDir, cacheDir } = defaultDataLocations();
		expect(dataDir).not.toBe(cacheDir);
	});
});

describe('settingsFile', () => {
	/* Not under the data folder on purpose: the data folder's location is one of the things this
	 * file records, so keeping it inside would break the moment somebody moved their library. */
	it('sits in the profile, not in the data folder', () => {
		stubPaths.userData = path.join('C:', 'Users', 'someone', 'AppData', 'Roaming', 'Sift');
		stubPaths.appData = path.join('C:', 'Users', 'someone', 'AppData', 'Roaming');
		expect(settingsFile()).toBe(path.join(stubPaths.userData, 'desktop.json'));
		expect(settingsFile().startsWith(defaultDataLocations().dataDir)).toBe(false);
	});
});

describe('backendLogFile', () => {
	it('is somewhere a person can be told to look', () => {
		stubPaths.userData = path.join('C:', 'profile');
		expect(backendLogFile()).toBe(path.join('C:', 'profile', 'backend.log'));
	});
});

describe('bundlePaths', () => {
	it('reads from the source tree when it is not packaged', () => {
		app.isPackaged = false;
		const { python, vendorBin } = bundlePaths();
		expect(python.endsWith(path.join('.venv', 'Scripts', 'python.exe'))).toBe(true);
		expect(vendorBin.endsWith(path.join('vendor', 'bin'))).toBe(true);
	});

	/* Packaged, the two live beside the executable rather than two folders up from __dirname,
	 * which inside an asar archive is not a real folder at all. */
	it('reads from the installed resources when it is packaged', () => {
		// Only Electron itself sets this, so a test has to. Restored below: it is a property of the
		// whole process, and a leaked value would make the previous test's assertion meaningless.
		const real = process.resourcesPath;
		Object.defineProperty(process, 'resourcesPath', {
			value: path.join('C:', 'Program Files', 'Sift', 'resources'),
			configurable: true
		});
		try {
			app.isPackaged = true;
			const { python, vendorBin } = bundlePaths();
			const resources = path.join('C:', 'Program Files', 'Sift', 'resources');
			/* `runtime/python.exe`, NOT `.venv/Scripts/python.exe`. A packaged Sift carries a real
			   interpreter; a virtual environment's python reads its standard library from wherever
			   its pyvenv.cfg points, which on somebody else's computer is nowhere. Shipping the
			   second kind would leave the backend unstartable on every machine but the one that
			   built it. */
			expect(python).toBe(path.join(resources, 'runtime', 'python.exe'));
			expect(python).not.toContain('.venv');
			expect(vendorBin).toBe(path.join(resources, 'vendor', 'bin'));
		} finally {
			Object.defineProperty(process, 'resourcesPath', { value: real, configurable: true });
		}
	});
});

describe('the window icon', () => {
	/* Only in development. Packaged, the icon is compiled into `Sift.exe` by the installer build
	 * and Windows reads it from there; naming a file as well would be a second copy of the
	 * picture that could disagree with the executable's, and would hide a build whose executable
	 * carries no icon. From a checkout there is no executable to carry one, so the file is named.
	 */
	it('is left to the executable once packaged', () => {
		app.isPackaged = true;
		expect(appIconFile()).toBeUndefined();
	});

	it('is named from the checkout when there is no executable to carry it', () => {
		app.isPackaged = false;
		const named = appIconFile();
		expect(named).toBeDefined();
		expect(named).toContain(path.join('installer', 'icon.ico'));
	});
});

/* A tray icon cannot be read off the executable, so a picture is named in both builds, and nothing
 * is named where the picture is missing: the caller then leaves the close button alone. */
describe('the tray icon', () => {
	it('is the checkout s own picture from a checkout', () => {
		app.isPackaged = false;
		expect(trayIconFile()).toContain(path.join('installer', 'icon.ico'));
	});

	it('is looked for in the installer s resources once packaged, and is nothing when absent', () => {
		const was = process.resourcesPath;
		Object.defineProperty(process, 'resourcesPath', {
			value: path.join(__dirname, 'no-such-resources'),
			configurable: true
		});
		app.isPackaged = true;
		try {
			expect(trayIconFile()).toBeNull();
		} finally {
			Object.defineProperty(process, 'resourcesPath', { value: was, configurable: true });
		}
	});
});

describe('the drag addon', () => {
	/* Two places for one file, because it is built in the source tree and copied flat into the
	 * installer's resources. Naming both here rather than at the call site is the difference
	 * between one fact and two that can disagree. */
	it('comes out of the installer s own resources once packaged', () => {
		/* Electron sets `resourcesPath`; node does not, and `path.join` of an undefined throws,
		   so the packaged half cannot be reached at all without standing it up. */
		const was = process.resourcesPath;
		Object.defineProperty(process, 'resourcesPath', {
			value: path.join('C:', 'Program Files', 'Sift', 'resources'),
			configurable: true
		});
		app.isPackaged = true;
		try {
			expect(dragAddonFile()).toBe(
				path.join('C:', 'Program Files', 'Sift', 'resources', 'native', 'drag.node')
			);
		} finally {
			Object.defineProperty(process, 'resourcesPath', { value: was, configurable: true });
		}
	});

	it('comes out of the build directory from a checkout', () => {
		app.isPackaged = false;
		expect(dragAddonFile()).toContain(path.join('build', 'Release', 'drag.node'));
	});
});
