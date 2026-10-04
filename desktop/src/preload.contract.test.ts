/* What the preload may import, and it is nothing but `electron`.
 *
 * A preload in an isolated renderer has no module loader for the application's own files:
 * `require('./channels')` is not available to it, only `electron` and a few Node built-ins are. A
 * relative import does not fail loudly: the preload throws while loading, nothing surfaces, and
 * `window.sift` is undefined. The window opens, every capability reports false, and the bridge is
 * gone with no other symptom.
 *
 * A unit test cannot see it: Vitest resolves the import and aliases `electron` to a stub, so the
 * import that breaks the real preload succeeds in every test. Moving a constant into a module
 * with no electron imports does not help either, because it is still a relative import.
 *
 * So the rule is about the import graph, and it is absolute: nothing but `electron`. The channel
 * names are consequently written out in both places, and the last test here is what makes that
 * safe.
 */

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
	 * isolated preload has nothing to resolve one with.
	 */
	it('imports none of the application own modules', () => {
		expect(localImports(source('preload'))).toEqual([]);
	});

	/* The price of the rule above: every channel name exists TWICE. This is what makes that safe:
	 * a rename on one side and not the other would be a verb the page calls and nothing answers, and
	 * nothing else in the build would say a word about it.
	 *
	 * Every shared constant is compared rather than one of them, because the failure this guards
	 * against is exactly "somebody added a fourth verb and only checked the first". */
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
	 * not. Listed rather than derived: a channel the shell answers and nothing can call is dead
	 * code, and one the preload calls and the shell ignores is a verb that silently does nothing. */
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
