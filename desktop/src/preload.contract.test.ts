/* What the preload may import, and it is nothing but `electron`. */

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

const HERE = dirname(fileURLToPath(import.meta.url));

/** Names that only exist in the main process. Reaching any of them from a preload is the bug. */
const MAIN_ONLY = ['ipcMain', 'BrowserWindow', 'app', 'dialog', 'shell', 'session', 'Menu', 'Tray'];

/** What `electron` may be asked for in a preload. Both of these exist in an isolated one. */
const ALLOWED_FROM_ELECTRON = ['contextBridge', 'ipcRenderer'];

function source(module: string): string {
	return readFileSync(join(HERE, `${module}.ts`), 'utf8');
}

/** The local modules a file imports, by name, ignoring package imports. */
function localImports(text: string): string[] {
	return [...text.matchAll(/from\s+'\.\/([\w-]+)'/g)].map((match) => match[1] as string);
}

function electronImports(text: string): string[] {
	const line = /import\s+\{([^}]+)\}\s+from\s+'electron'/.exec(text);
	return line === null
		? []
		: (line[1] as string).split(',').map((one) => one.trim()).filter(Boolean);
}

describe('the preload', () => {
	it('asks electron only for what a sandboxed preload has', () => {
		const asked = electronImports(source('preload'));

		expect(asked.length).toBeGreaterThan(0);
		for (const one of asked) expect(ALLOWED_FROM_ELECTRON).toContain(one);
	});

	/* The rule that matters: not "no main-process API" but no local import at all, because an
	 * isolated preload has nothing to resolve one with. */
	it('imports none of the application own modules', () => {
		expect(localImports(source('preload'))).toEqual([]);
	});

	/* The price of the rule above: every channel name exists TWICE. */
	it('gives every shared constant the same value the main process uses', () => {
		const declared = (text: string, prefix: string): Map<string, string> =>
			new Map(
				[...text.matchAll(new RegExp(`${prefix}const (\\w+) = '([^']+)'`, 'g'))].map((match) => [
					match[1] as string,
					match[2] as string
				])
			);

		const inPreload = declared(source('preload'), '');
		const inChannels = declared(source('channels'), 'export ');

		const shared = [...inPreload.keys()].filter((name) => inChannels.has(name));
		/* A floor, so this cannot quietly become a test of nothing: the constants are inlined by
		 * hand, and a refactor that renamed them all would otherwise leave an empty list passing. */
		expect(shared.length).toBeGreaterThanOrEqual(5);
		for (const name of shared) expect(inPreload.get(name)).toBe(inChannels.get(name));
	});

	/* Every channel the main process listens on is either exposed by the preload or deliberately
	 * not. */
	it('exposes every channel the main process answers', () => {
		const channels = [...source('channels').matchAll(/export const (\w+) = 'sift:[^']+'/g)].map(
			(match) => match[1] as string
		);
		const preload = source('preload');

		for (const name of channels) expect(preload).toContain(`const ${name} = `);
	});

	/* The check proved to fail rather than assumed to: `verbs` is the module that legitimately
	 * imports a main-process API, so its graph is what a broken preload's would look like. */
	it('would catch a module that reached the main process', () => {
		const asked = electronImports(source('verbs'));

		expect(asked.some((one) => MAIN_ONLY.includes(one))).toBe(true);
	});
});
