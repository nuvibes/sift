/* Whether Sift starts when this person signs in to Windows, and how it appears when it does. */

import type { App } from 'electron';

import { log } from './log';

/* The argument a sign-in start carries, which `main` reads to start in the notification area
 * rather than in front of whatever somebody opened first. */
export const AT_SIGN_IN = '--hidden';

/* The name of the Run value. */
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

/** How the window appears once the library has loaded. */
export function howToAppear(startedAtSignIn: boolean, hasTray: boolean): Appearing {
	if (!startedAtSignIn) return 'show';
	return hasTray ? 'tray' : 'taskbar';
}

/** Whether this start is the one Windows made at sign-in. */
export function startedAtSignIn(argv: readonly string[]): boolean {
	return argv.includes(AT_SIGN_IN);
}

/** The switch, over Electron's login-item calls. */
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
				/* `enabled` is what clears a "Disabled" somebody set in Task Manager: turning it
				   on here means it starts, not that a value exists which Windows then skips. */
				api.setLoginItemSettings({
					openAtLogin: true,
					path: executable,
					args: [AT_SIGN_IN],
					name: ENTRY_NAME,
					enabled: true
				});
			} else {
				/* Every value in the current user's Run key that starts THIS executable, not
				   only the one this shell wrote: the read above answers yes for any of them, so
				   leaving one behind would be a switch that springs back on the moment it is
				   turned off. */
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
