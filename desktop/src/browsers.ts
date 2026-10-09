/* Which browsers this machine has, and opening a link in the one somebody chose. */

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
	// reg.exe prints `    (Default)    REG_SZ    the value`.
	const line = printed.split(/\r?\n/).find((one) => /\(Default\)/.test(one));
	if (line === undefined) return null;
	const match = line.match(/REG_(?:SZ|EXPAND_SZ)\s+(.*)$/);
	const value = match?.[1]?.trim();
	return value ? value : null;
}

/** The executable out of a registry command line. */
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
	// Keyed on the executable in lower case, because Windows paths are case-insensitive and the
	// same browser comes back from two hives and two views with whatever casing each one recorded.
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
				// Keyed on the executable, so the same browser found in two hives or two views is
				// one entry rather than four.
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

/** Open a link in the chosen browser. False when it could not be, so the caller can fall back. */
export function openIn(browser: string, url: string): boolean {
	if (!isWebAddress(url)) return false;
	// Asked BEFORE spawning, and that is the whole reason this returns a boolean at all.
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
