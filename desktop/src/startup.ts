/* Whether Sift starts when this person signs in to Windows, and how it appears when it does.
 *
 * OFF UNTIL SOMEBODY TURNS IT ON. Nothing in the shell writes the entry on its own (not first run,
 * not an update, not an install) because an application that adds itself to somebody's startup
 * list is the behaviour people uninstall things for. The only writer is the verb the General pane
 * calls when its switch is pressed.
 *
 * WHAT IS WRITTEN. Electron's `setLoginItemSettings` adds one value to the CURRENT USER's
 * `Software\Microsoft\Windows\CurrentVersion\Run` key, named `ENTRY_NAME`, holding the quoted path
 * of this executable followed by `AT_SIGN_IN`. It also sets the matching value under
 * `Explorer\StartupApproved\Run`, which is the switch Task Manager's Startup page and Windows'
 * Settings > Apps > Startup both draw, so the entry appears there under Sift's own name and can
 * be turned off there too. Per-user, like the rest of this installation: no administrator prompt.
 *
 * READ BACK FROM WINDOWS, NEVER ASSUMED FROM THE CALL. The answer the verb gives is a fresh read of
 * the registry after the write, not the value the page asked for. That matters for the one case
 * this setting really has: somebody turns Sift off in Task Manager, and Sift's own switch must then
 * say off rather than the "on" it last wrote. `executableWillLaunchAtLogin` is exactly that
 * question: a Run value for this executable that Windows has NOT been told to skip.
 *
 * AN UNPACKAGED SHELL ANSWERS NOTHING. In a checkout `process.execPath` is Electron's own
 * `electron.exe`, which started without the application folder beside it opens Electron's default
 * page, not Sift. Registering that would put a wrong program in somebody's startup list; answering
 * "off" would be a confident wrong value about a question this copy cannot act on. So both verbs
 * answer null there, and the switch is not drawn, the rule every shell-only control keeps. The
 * same holds off Windows, where this key does not exist.
 */

import type { App } from 'electron';

import { log } from './log';

/* The argument a sign-in start carries, which `main` reads to start in the notification area
 * rather than in front of whatever somebody opened first. Not Chromium's and not Electron's, so
 * nothing else acts on it. */
export const AT_SIGN_IN = '--hidden';

/* The name of the Run value. Given explicitly: Electron's default is the process's AppUserModelID,
 * which nothing in this shell sets, so the value would be named after whatever Electron falls back
 * to rather than after the application. Task Manager draws the executable's own description, which
 * the installer build writes as the product name ("Sift"), so the two agree. */
export const ENTRY_NAME = 'Sift';

/** The part of Electron's `app` this module asks. Passed in, so a test can stand in for Windows. */
export type LoginItemApi = Pick<App, 'isPackaged' | 'getLoginItemSettings' | 'setLoginItemSettings'>;

/** The switch, as the verbs see it. Null in both directions where this copy cannot answer. */
export interface StartWithWindows {
	read(): boolean | null;
	write(on: boolean): boolean | null;
}

/** How the window appears when a start has finished loading. */
export type Appearing = 'show' | 'tray' | 'taskbar';

/**
 * How the window appears once the library has loaded.
 *
 * An ordinary start shows it. A start Windows made at sign-in goes to the notification area (the
 * same place close-to-tray already hides the window), so the backend is running and the icon is
 * the way in. BUT ONLY WHERE THERE IS AN ICON: with close-to-tray off there is no tray, and a hidden
 * window with no icon is an application nobody can find except by starting it a second time. That
 * start goes to the taskbar instead, minimised: running, visible, and out of the way.
 */
export function howToAppear(startedAtSignIn: boolean, hasTray: boolean): Appearing {
	if (!startedAtSignIn) return 'show';
	return hasTray ? 'tray' : 'taskbar';
}

/** Whether this start is the one Windows made at sign-in. */
export function startedAtSignIn(argv: readonly string[]): boolean {
	return argv.includes(AT_SIGN_IN);
}

/**
 * The switch, over Electron's login-item calls.
 *
 * `executable` is the program to register: `process.execPath` in the application, which for an
 * installed copy is `%LOCALAPPDATA%\Programs\Sift\Sift.exe` and survives every update, because the
 * installer puts each version at the same path.
 */
export function startWithWindows(
	api: LoginItemApi,
	executable: string,
	platform: NodeJS.Platform = process.platform
): StartWithWindows {
	const answerable = api.isPackaged && platform === 'win32';

	const read = (): boolean | null => {
		if (!answerable) return null;
		try {
			return api.getLoginItemSettings({ path: executable, args: [AT_SIGN_IN] })
				.executableWillLaunchAtLogin;
		} catch (error) {
			/* A registry that cannot be read is a question with no answer, not an "off". */
			log.warning('startup.read_failed', { reason: (error as Error).message });
			return null;
		}
	};

	const write = (on: boolean): boolean | null => {
		if (!answerable) return null;
		try {
			if (on) {
				/* `enabled` is what clears a "Disabled" somebody set in Task Manager: turning it on
				   here means it starts, not that a value exists which Windows then skips. */
				api.setLoginItemSettings({
					openAtLogin: true,
					path: executable,
					args: [AT_SIGN_IN],
					name: ENTRY_NAME,
					enabled: true
				});
			} else {
				/* Every value in the current user's Run key that starts THIS executable, not only
				   the one this shell wrote: the read above answers yes for any of them, so leaving
				   one behind would be a switch that springs back on the moment it is turned off.
				   The machine-wide key is not the person's to change and is left; the read then
				   says so honestly. */
				const names = new Set([ENTRY_NAME]);
				const found = api.getLoginItemSettings({ path: executable, args: [AT_SIGN_IN] });
				for (const item of found.launchItems ?? []) {
					if (item.scope === 'user' && samePath(item.path, executable)) names.add(item.name);
				}
				for (const name of names) {
					api.setLoginItemSettings({ openAtLogin: false, path: executable, name });
				}
			}
		} catch (error) {
			log.warning('startup.write_failed', { on, reason: (error as Error).message });
		}
		return read();
	};

	return { read, write };
}

/* Windows paths compare without regard to case, and a Run value may carry the path quoted. */
function samePath(a: string, b: string): boolean {
	const bare = (one: string) => one.replace(/^"|"$/g, '').toLowerCase();
	return bare(a) === bare(b);
}
