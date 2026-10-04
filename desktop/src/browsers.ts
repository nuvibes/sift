/* Which browsers this machine has, and opening a link in the one somebody chose.
 *
 * Sift sends every link out of the window: a page that is not Sift never becomes Sift, so a link
 * to a source site goes to a real browser where the person's extensions and their own judgement
 * apply. The choice matters to anyone whose default browser is not the one they keep their logins
 * in.
 *
 * ## Where the list comes from
 *
 * `Software\Clients\StartMenuInternet`, which is the only place Windows itself keeps this. A
 * browser registers there when it is installed; nothing else writes to it, and nothing a page can
 * reach writes to it at all. Both hives are read (machine-wide installs land in HKLM and
 * per-user ones in HKCU), and both registry views, because a 32-bit browser on a 64-bit Windows
 * is under `WOW6432Node` and is invisible to a default query.
 *
 * ## What is not done, and why
 *
 * **The command line is not run.** What the registry holds is a command such as
 * `"C:\...\chrome.exe" "%1"`, and handing that to a shell to expand would be handing a shell a
 * string with a URL in it. Only the executable is taken out of it; the address is passed as its
 * own argument to a process started directly, so there is no shell and no command line for an
 * address to break out of.
 *
 * **A URL that is not http or https is never opened at all.** The guard lives here rather than at
 * the caller: a page must not be able to ask Windows to open a `file:` path or a registered
 * protocol handler, and every real use of this is a link to a website.
 */

import { execFile, spawn } from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';

/** One browser this machine has. `id` is the executable, which is what makes two entries the same. */
export interface Browser {
	id: string;
	name: string;
}

/** Where Windows keeps the list. Relative to a hive, because both hives are asked. */
const CLIENTS = 'Software\\Clients\\StartMenuInternet';

/** How long a registry query may take before it is given up on. */
const QUERY_TIMEOUT_MS = 5000;

/** How a registry query is made. Injected so a test can answer without touching a registry. */
export type Ask = (args: string[]) => Promise<string>;

const askRegistry: Ask = (args) =>
	new Promise((answer) => {
		execFile('reg.exe', args, { timeout: QUERY_TIMEOUT_MS, windowsHide: true }, (error, out) =>
			answer(error ? '' : out)
		);
	});

/** The default value of a key, which is where both the display name and the command live. */
export function defaultValue(printed: string): string | null {
	// reg.exe prints `    (Default)    REG_SZ    the value`. The value may itself contain spaces, so
	// it is everything after the type rather than the last field.
	const line = printed.split(/\r?\n/).find((one) => /\(Default\)/.test(one));
	if (line === undefined) return null;
	const match = line.match(/REG_(?:SZ|EXPAND_SZ)\s+(.*)$/);
	const value = match?.[1]?.trim();
	return value ? value : null;
}

/**
 * The executable out of a registry command line.
 *
 * Quoted is the ordinary form and the easy one. Unquoted is answered by taking everything up to and
 * including the first `.exe`, which is what Windows itself does, and NOT by splitting on spaces,
 * which would stop at `C:\Program` and hand back a path that does not exist.
 */
export function executableFrom(command: string): string | null {
	const trimmed = command.trim();
	if (trimmed.startsWith('"')) {
		const end = trimmed.indexOf('"', 1);
		return end > 1 ? trimmed.slice(1, end) : null;
	}
	const exe = trimmed.match(/^(.*?\.exe)/i);
	return exe?.[1] ?? null;
}

/** Every browser this machine has, by display name, with no duplicates. */
export async function installed(ask: Ask = askRegistry): Promise<Browser[]> {
	if (process.platform !== 'win32') return [];
	// Keyed on the executable in lower case, because Windows paths are case-insensitive and the same
	// browser comes back from two hives and two views with whatever casing each one recorded. The
	// VALUE keeps the real path: it is what gets started, and what a person would recognise.
	const found = new Map<string, Browser>();

	for (const hive of ['HKLM', 'HKCU']) {
		for (const view of ['/reg:64', '/reg:32']) {
			const listing = await ask(['query', `${hive}\\${CLIENTS}`, view]);
			for (const line of listing.split(/\r?\n/)) {
				const subkey = line.trim();
				if (!subkey.toLowerCase().includes(CLIENTS.toLowerCase())) continue;
				// The container itself is in its own listing. Only what is UNDER it is a browser.
				if (subkey.toLowerCase().endsWith(CLIENTS.toLowerCase())) continue;

				const command = defaultValue(
					await ask(['query', `${subkey}\\shell\\open\\command`, '/ve', view])
				);
				const exe = command === null ? null : executableFrom(command);
				if (exe === null) continue;

				const shown = defaultValue(await ask(['query', subkey, '/ve', view]));
				// The key's own name is the fallback, and it is a real one: several browsers leave
				// the display name unset and are known only by `firefox.exe` or `chrome.exe`.
				const name = shown ?? subkey.split('\\').pop() ?? path.basename(exe);
				// Keyed on the executable, so the same browser found in two hives or two views is one
				// entry rather than four.
				if (!found.has(exe.toLowerCase())) found.set(exe.toLowerCase(), { id: exe, name });
			}
		}
	}

	return [...found.values()].sort((a, b) => a.name.localeCompare(b.name));
}

/** Whether an address is one this may ever be asked to open. */
export function isWebAddress(url: string): boolean {
	try {
		const scheme = new URL(url).protocol;
		return scheme === 'http:' || scheme === 'https:';
	} catch {
		return false;
	}
}

/**
 * Open a link in the chosen browser. False when it could not be, so the caller can fall back.
 *
 * `detached`, because the browser must outlive Sift: started as an ordinary child it would be
 * carried off by a Windows process-tree kill, and closing Sift would take somebody's browser with it
 * on a machine where it was not already running.
 */
export function openIn(browser: string, url: string): boolean {
	if (!isWebAddress(url)) return false;
	// Asked BEFORE spawning, and that is the whole reason this returns a boolean at all. `spawn`
	// reports a missing executable ASYNCHRONOUSLY, on an `error` event, long after the caller has
	// been told the link was opened, so somebody who uninstalled the browser they chose would
	// click a link, be told nothing, and watch nothing happen. Checked first, the caller still has
	// its fallback in hand.
	if (!fs.existsSync(browser)) return false;
	try {
		const child = spawn(browser, [url], { detached: true, stdio: 'ignore' });
		// Nothing is done with a later failure beyond not crashing on it: an unhandled `error` on a
		// child process takes the main process down with it.
		child.on('error', () => {});
		child.unref();
		return true;
	} catch {
		return false;
	}
}
