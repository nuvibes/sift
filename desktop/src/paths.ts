/* Where the desktop application keeps things, visibly: it is where the library's database lives. */

import { app } from 'electron';
import * as fs from 'node:fs';
import * as path from 'node:path';

/** The two folders the backend is given. Confirmed or changed on first run. */
export interface DataLocations {
	/** The database, quarantine, and everything that cannot be regenerated. */
	dataDir: string;
	/** Thumbnails, transcodes, and everything that can be rebuilt from the library. */
	cacheDir: string;
}

/* %LOCALAPPDATA%, not %APPDATA%. */
export function defaultDataLocations(): DataLocations {
	const base = path.join(app.getPath('appData'), '..', 'Local', 'Sift');
	return {
		dataDir: path.resolve(base, 'data'),
		cacheDir: path.resolve(base, 'cache')
	};
}

/* The shell's own settings: which lifecycle, which data folder, which servers have been saved. */
export function settingsFile(): string {
	return path.join(app.getPath('userData'), 'desktop.json');
}

/** Where the backend's own output is captured, so a failed start can be read rather than guessed. */
export function backendLogFile(): string {
	return path.join(app.getPath('userData'), 'backend.log');
}

/* The SHELL's own log, which is a different thing from the one above. */
export function shellLogFile(): string {
	return path.join(app.getPath('userData'), 'shell.log');
}

/** The bundled interpreter and the vendored tools, whether running from source or installed. */
export function bundlePaths(): { python: string; vendorBin: string } {
	// `app.isPackaged` is the only reliable difference: __dirname is inside an asar when packaged.
	if (app.isPackaged) {
		return {
			python: path.join(process.resourcesPath, 'runtime', 'python.exe'),
			vendorBin: path.join(process.resourcesPath, 'vendor', 'bin')
		};
	}
	const root = path.resolve(__dirname, '..', '..');
	return {
		python: path.join(root, '.venv', 'Scripts', 'python.exe'),
		vendorBin: path.join(root, 'vendor', 'bin')
	};
}

/* The compiled drag addon, wherever this build keeps it. */
export function dragAddonFile(): string {
	return app.isPackaged
		? path.join(process.resourcesPath, 'native', 'drag.node')
		: path.resolve(__dirname, '..', 'native', 'drag', 'build', 'Release', 'drag.node');
}

/* Sift's own icon, for the window and the taskbar. */
export function appIconFile(): string | undefined {
	if (app.isPackaged) return undefined;
	return path.resolve(__dirname, '..', 'installer', 'icon.ico');
}

/* The same drawing for the notification area: a packaged copy's answer differs from the above. */
export function trayIconFile(): string | null {
	const file = app.isPackaged
		? path.join(process.resourcesPath, 'icon.ico')
		: path.resolve(__dirname, '..', 'installer', 'icon.ico');
	return fs.existsSync(file) ? file : null;
}

/* The built client, wherever this build keeps it. */
export function clientFiles(): string {
	const root = app.isPackaged ? process.resourcesPath : path.resolve(__dirname, '..', '..');
	return app.isPackaged
		? path.join(root, 'runtime', 'Lib', 'site-packages', 'sift', 'web')
		: path.join(root, 'src', 'sift', 'web');
}
