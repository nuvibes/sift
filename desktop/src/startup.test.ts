/* Starting Sift at sign-in: the switch over Windows' Run key, and how that start appears. */

import type { LoginItemSettings, Settings } from 'electron';
import { beforeEach, describe, expect, it, vi } from 'vitest';

/* The log writes a file under the shell's settings folder; here it only records what was said. */
const logged = vi.hoisted(() => [] as string[]);
vi.mock('./log', () => ({
	log: { warning: (event: string) => logged.push(event) }
}));

import {
	AT_SIGN_IN,
	ENTRY_NAME,
	howToAppear,
	startedAtSignIn,
	startWithWindows,
	type LoginItemApi
} from './startup';

const EXE = 'C:\\Users\\someone\\AppData\\Local\\Programs\\Sift\\Sift.exe';

/** One value in the current user's Run key, and whether Task Manager has it turned on. */
interface RunValue {
	path: string;
	args: string[];
	enabled: boolean;
	scope: 'user' | 'machine';
}

/* The registry as Electron reads it: `executableWillLaunchAtLogin` is any enabled value for this
   executable, whatever it is named and whatever it passes. */
function aWindows(isPackaged = true) {
	const run = new Map<string, RunValue>();
	const writes: Settings[] = [];
	let broken: Error | null = null;
	const api: LoginItemApi = {
		isPackaged,
		getLoginItemSettings: (options) => {
			if (broken !== null) throw broken;
			const items = [...run.entries()].map(([name, value]) => ({ name, ...value }));
			return {
				openAtLogin: false,
				openAsHidden: false,
				wasOpenedAtLogin: false,
				wasOpenedAsHidden: false,
				restoreState: false,
				status: 'not-registered',
				executableWillLaunchAtLogin: items.some(
					(one) => one.path.toLowerCase() === (options?.path ?? '').toLowerCase() && one.enabled
				),
				launchItems: items
			} satisfies LoginItemSettings;
		},
		setLoginItemSettings: (settings) => {
			if (broken !== null) throw broken;
			writes.push(settings);
			const name = settings.name ?? 'electron.app.Sift';
			if (settings.openAtLogin === true) {
				run.set(name, {
					path: settings.path ?? '',
					args: settings.args ?? [],
					enabled: settings.enabled ?? true,
					scope: 'user'
				});
			} else run.delete(name);
		}
	};
	return {
		api,
		run,
		writes,
		breakWith: (error: Error) => {
			broken = error;
		}
	};
}

let windows: ReturnType<typeof aWindows>;

beforeEach(() => {
	windows = aWindows();
	logged.length = 0;
});

describe('the switch', () => {
	it('is off until somebody turns it on', () => {
		expect(startWithWindows(windows.api, EXE, 'win32').read()).toBe(false);
		expect(windows.writes).toEqual([]);
	});

	it('registers THIS executable, under Sift, starting in the notification area', () => {
		expect(startWithWindows(windows.api, EXE, 'win32').write(true)).toBe(true);

		expect(windows.writes).toEqual([
			{ openAtLogin: true, path: EXE, args: [AT_SIGN_IN], name: ENTRY_NAME, enabled: true }
		]);
		expect(ENTRY_NAME).toBe('Sift');
	});

	/* The rule the verb is built on: what comes back is a fresh read of Windows, never the value
	   that was asked for. */
	it('answers what Windows holds after the write, not the word that was sent', () => {
		const lying: LoginItemApi = { ...windows.api, setLoginItemSettings: () => {} };

		expect(startWithWindows(lying, EXE, 'win32').write(true)).toBe(false);
	});

	/* The one case this setting really has: somebody turned Sift off in Task Manager, and the
	   switch must follow Windows rather than the "on" it last wrote. */
	it('reads off when Task Manager has the entry turned off', () => {
		const startup = startWithWindows(windows.api, EXE, 'win32');
		startup.write(true);
		(windows.run.get(ENTRY_NAME) as RunValue).enabled = false;

		expect(startup.read()).toBe(false);
	});

	it('turning it on again clears a Task Manager "disabled"', () => {
		const startup = startWithWindows(windows.api, EXE, 'win32');
		windows.run.set(ENTRY_NAME, { path: EXE, args: [AT_SIGN_IN], enabled: false, scope: 'user' });

		expect(startup.write(true)).toBe(true);
	});

	it('turns off', () => {
		const startup = startWithWindows(windows.api, EXE, 'win32');
		startup.write(true);

		expect(startup.write(false)).toBe(false);
		expect(windows.run.size).toBe(0);
	});

	/* A switch that springs back on the moment it is turned off. */
	it('turns off every entry of the person\'s that starts Sift, and nothing else', () => {
		const quoted = `"${EXE.toUpperCase()}"`;
		windows.run.set('electron.app.Sift', { path: quoted, args: [], enabled: true, scope: 'user' });
		windows.run.set('Other', { path: 'C:\\Other\\o.exe', args: [], enabled: true, scope: 'user' });

		expect(startWithWindows(windows.api, EXE, 'win32').write(false)).toBe(false);
		expect([...windows.run.keys()]).toEqual(['Other']);
	});

	it('never writes the machine-wide key, and then says honestly that Sift still starts', () => {
		windows.run.set('Machine', { path: EXE, args: [], enabled: true, scope: 'machine' });

		expect(startWithWindows(windows.api, EXE, 'win32').write(false)).toBe(true);
		expect(windows.writes.map((one) => one.name)).toEqual([ENTRY_NAME]);
	});
});

/* The rule every shell-only control keeps: an unpackaged shell never answers a confident wrong
   value. */
describe('where it cannot answer', () => {
	it('registers nothing from a checkout, and answers null both ways', () => {
		const checkout = aWindows(false);
		const startup = startWithWindows(checkout.api, EXE, 'win32');

		expect(startup.read()).toBeNull();
		expect(startup.write(true)).toBeNull();
		expect(checkout.writes).toEqual([]);
	});

	it('answers null off Windows', () => {
		const startup = startWithWindows(windows.api, EXE, 'linux');

		expect(startup.read()).toBeNull();
		expect(startup.write(true)).toBeNull();
		expect(windows.writes).toEqual([]);
	});

	it('answers null, not off, when the registry cannot be read', () => {
		windows.breakWith(new Error('access denied'));

		expect(startWithWindows(windows.api, EXE, 'win32').read()).toBeNull();
		expect(startWithWindows(windows.api, EXE, 'win32').write(true)).toBeNull();
		expect(logged).toEqual(['startup.read_failed', 'startup.write_failed', 'startup.read_failed']);
	});
});

describe('how a start appears', () => {
	it('an ordinary start shows the window', () => {
		expect(howToAppear(false, true)).toBe('show');
		expect(howToAppear(false, false)).toBe('show');
	});

	it('a sign-in start goes to the notification area', () => {
		expect(howToAppear(true, true)).toBe('tray');
	});

	/* With close-to-tray off there is no icon, and a hidden window with no icon is an
	   application nobody can find. */
	it('a sign-in start with no icon to go to waits on the taskbar instead', () => {
		expect(howToAppear(true, false)).toBe('taskbar');
	});

	it('knows a sign-in start by the argument the entry carries', () => {
		expect(startedAtSignIn([EXE, AT_SIGN_IN])).toBe(true);
		expect(startedAtSignIn([EXE])).toBe(false);
	});
});
