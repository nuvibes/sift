/* Where the desktop application keeps things.
 *
 * There are no mounts, so the application chooses, and the choice is visible to the person
 * running it, because it is where their library's database lives.
 */

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

/* %LOCALAPPDATA%, not %APPDATA%. The roaming profile is copied between machines by domain policy
 * and by some backup tools, and a media database with its transcode cache is precisely what nobody
 * wants silently synchronised: it is large, it is machine-specific, and half of it is derived. */
export function defaultDataLocations(): DataLocations {
	const base = path.join(app.getPath('appData'), '..', 'Local', 'Sift');
	return {
		dataDir: path.resolve(base, 'data'),
		cacheDir: path.resolve(base, 'cache')
	};
}

/* The shell's own settings: which lifecycle, which data folder, which servers have been saved.
 * Deliberately NOT under the data folder: the data folder's location is one of the things recorded
 * here, so keeping this inside it would be a circular definition that breaks the moment somebody
 * moves their library. */
export function settingsFile(): string {
	return path.join(app.getPath('userData'), 'desktop.json');
}

/** Where the backend's own output is captured, so a failed start can be read rather than guessed. */
export function backendLogFile(): string {
	return path.join(app.getPath('userData'), 'backend.log');
}

/* The SHELL's own log, which is a different thing from the one above.
 *
 * `backend.log` is the Python process's stdout, captured while starting it. This is what the main
 * process itself has to say (the drag, the transfers, the update, the window), and it is written
 * in both modes, which is the whole point: client mode starts no backend, so it has no `backend.log`
 * at all and the Application log screen shows it the SERVER's file from another computer.
 *
 * In `userData` beside `desktop.json` rather than in the data folder, and for the same reason that
 * one is: the data folder is a thing the shell records rather than a thing it can assume, and in
 * client mode it belongs to somebody else's machine.
 */
export function shellLogFile(): string {
	return path.join(app.getPath('userData'), 'shell.log');
}

/** The bundled interpreter and the vendored tools, whether running from source or installed. */
export function bundlePaths(): { python: string; vendorBin: string } {
	// `app.isPackaged` is the only reliable difference: __dirname is inside an asar archive when
	// packaged and a real folder when not.
	//
	// The two shapes are genuinely different, so they are written as two paths rather than one with
	// a swapped root. A checkout has a virtual environment, whose interpreter lives elsewhere on
	// the machine that created it and is found through `pyvenv.cfg`. A release carries a real
	// interpreter, because there is no elsewhere on somebody else's computer (see build_runtime
	// in scripts/release.py).
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

/* The compiled drag addon, wherever this build keeps it.
 *
 * Two places for the same file, because it is built in the source tree and copied flat into the
 * installer's resources (see `extraResources` in electron-builder.yml). Naming both here rather
 * than at the call site is the difference between one fact and two that can disagree.
 */
export function dragAddonFile(): string {
	return app.isPackaged
		? path.join(process.resourcesPath, 'native', 'drag.node')
		: path.resolve(__dirname, '..', 'native', 'drag', 'build', 'Release', 'drag.node');
}

/* Sift's own icon, for the window and the taskbar.
 *
 * Only in development. Once packaged, the icon is compiled into Sift.exe by the installer build
 * and Windows reads it from there: pointing `icon` at a file would be a second copy that could
 * disagree with the one on the executable. Running from a checkout there is no executable to
 * carry it, so the file is named here, or the window would wear Electron's own logo.
 */
export function appIconFile(): string | undefined {
	if (app.isPackaged) return undefined;
	return path.resolve(__dirname, '..', 'installer', 'icon.ico');
}

/* The same drawing, for the notification area, and it is a SEPARATE function from the one above
 * because the answer for a packaged copy is different.
 *
 * A window can take its icon off the executable, which is why `appIconFile` answers nothing once
 * packaged. A tray icon cannot: `new Tray(...)` wants a picture, and there is no executable to read
 * one from. So the file travels in `resources` (see `extraResources` in electron-builder.yml),
 * and this names where it lands.
 *
 * Null rather than a path that is not there, because the caller's answer to "no icon" is to leave
 * the close button alone rather than to draw a gap in the tray nothing can be clicked on.
 */
export function trayIconFile(): string | null {
	const file = app.isPackaged
		? path.join(process.resourcesPath, 'icon.ico')
		: path.resolve(__dirname, '..', 'installer', 'icon.ico');
	return fs.existsSync(file) ? file : null;
}

/* The built client, wherever this build keeps it.
 *
 * Normally the backend serves these files and the shell never touches them. The exception is client
 * mode before an address has been saved: there is no backend to ask and no server to load, and the
 * screen that asks which computer to connect to is a route in this same application. So the shell
 * serves that one screen off the disk itself (see shellpage.ts).
 */
export function clientFiles(): string {
	const root = app.isPackaged ? process.resourcesPath : path.resolve(__dirname, '..', '..');
	return app.isPackaged
		? path.join(root, 'runtime', 'Lib', 'site-packages', 'sift', 'web')
		: path.join(root, 'src', 'sift', 'web');
}
