/*
 * The generated description of the server is wired to something.
 *
 * The build regenerates a file describing every endpoint's exact inputs and outputs, and refuses to
 * finish if it is out of date. A comment claiming "server paths, from the generated schema, so a
 * typo is a build error" is only true if something reads that file, so this checks that something
 * does.
 *
 * Both halves are checked here, because either one alone is worthless. A file nothing imports is
 * dead weight kept green by a staleness check. A path type that is not built from it is a list
 * somebody maintains by hand, which is the thing being replaced.
 *
 * What this cannot check is that a screen asks for the right THING at the right address, and it is
 * not meant to. The failure it exists for is an endpoint renamed on the server with a screen still
 * asking for the old one, which reaches somebody as an empty screen with no error at all.
 */

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

/** The whole of `src`, resolved from this file rather than from the working directory. */
const SOURCE = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const CLIENT = readFileSync(join(SOURCE, 'lib/api/client.ts'), 'utf8');

function everySourceFile(dir: string): string[] {
	const found: string[] = [];
	for (const entry of readdirSync(dir)) {
		if (entry === 'node_modules') continue;
		const path = join(dir, entry);
		if (statSync(path).isDirectory()) found.push(...everySourceFile(path));
		else if (entry.endsWith('.svelte') || entry.endsWith('.ts')) found.push(path);
	}
	return found;
}

const files = everySourceFile(SOURCE)
	.map((path) => ({
		where: relative(SOURCE, path).split('\\').join('/'),
		source: readFileSync(path, 'utf8')
	}))
	.filter((file) => file.where !== 'lib/api/schema.d.ts');

describe('the generated schema is read by something', () => {
	it('surveys the interface rather than an empty tree', () => {
		expect(files.length).toBeGreaterThan(100);
	});

	it('has an importer', () => {
		const readers = files
			// By either name: the client sits beside it and reaches for it relatively.
			.filter((file) => /from '(\$lib\/api|\.)\/schema'/.test(file.source))
			.map((file) => file.where);

		expect(readers).toContain('lib/api/client.ts');
	});
});

describe("the client's path is the generated type", () => {
	it('builds it from the generated paths and nothing else', () => {
		// Not a hand-kept list that happens to be spelled the same. The type has to be derived, or
		// it is a second description of the server maintained by somebody remembering to.
		expect(CLIENT).toMatch(/import type \{ paths \} from '\.\/schema'/);
		expect(CLIENT).toMatch(/export type ApiPath = \w+<\w+<keyof paths>>/);
	});

	it('is what every verb takes', () => {
		/*
		 * All six, by name. Five of them typed and one left as a plain string is the shape of
		 * the fault: one way in that nothing checks, and every call somebody wants unchecked
		 * quietly moving to it.
		 */
		for (const verb of ['get', 'post', 'put', 'patch', 'del']) {
			expect(CLIENT, `api.${verb}`).toMatch(new RegExp(`\\b${verb}: <T>\\(path: ApiPath[,)]`));
		}
		expect(CLIENT).toMatch(/postForFile: \(path: ApiPath[,)]/);
	});

	it('is what the one function behind them takes', () => {
		expect(CLIENT).toMatch(/export async function request<T>\(\s*method: string,\s*path: ApiPath,/);
	});

	it('leaves no address carrying its own query string', () => {
		/*
		 * A question mark inside the path is how a call escapes the type without looking like it:
		 * the generated names have no query in them, so an address built with one appended can only
		 * be written as a free string.
		 *
		 * It is also a fault in its own right: a call building `?path=` by hand has to remember to
		 * escape the value, and against a folder name a `#` silently asks for a different folder.
		 * The client takes a `query` and does it once.
		 */
		const appending = files
			.filter((file) => /api\.\w+(<[^(]*>)?\(\s*[`'"][^`'"]*\?[^`'"]*[`'"]/.test(file.source))
			.map((file) => file.where);

		expect(appending).toEqual([]);
	});
});
