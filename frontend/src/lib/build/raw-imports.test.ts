/**
 * A test that reads a file as text through `?raw` reads what is in it. Under this runner a
 * stylesheet asked for that way arrives as an empty string, so a check over it passes on nothing:
 * a test reads a stylesheet off the disk instead, and every other file a test asks for as text is
 * shown here to arrive with something in it.
 */
import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join, posix, relative, resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

const ROOT = resolve('src');

/** Every test file under `src`, as a path relative to the client folder and its text. */
function testFiles(dir = ROOT): { file: string; text: string }[] {
	return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
		const path = join(dir, entry.name);
		if (entry.isDirectory()) return testFiles(path);
		if (!entry.name.endsWith('.test.ts')) return [];
		return [
			{ file: relative(resolve('.'), path).split('\\').join('/'), text: readFileSync(path, 'utf8') }
		];
	});
}

const STATIC = /from\s+'([^']+)\?raw'|import\('([^']+)\?raw'\)/g;

/** Each file a test imports by name with `?raw`, as a path from the client folder. */
function rawImports(file: string, text: string): string[] {
	return [...text.matchAll(STATIC)].map((match) => {
		const named = match[1] ?? match[2];
		const fromSrc = named.startsWith('$lib/') ? `src/lib/${named.slice(5)}` : named;
		return named.startsWith('.') ? posix.join(dirname(file), named) : fromSrc;
	});
}

/** The glob patterns a test hands `import.meta.glob` with `query: '?raw'`. */
function rawGlobs(text: string): string[] {
	return [
		...text.matchAll(/import\.meta\.glob\(\s*(\[[^\]]*\]|'[^']*')[^)]*?query:\s*'\?raw'/g)
	].flatMap((match) =>
		[...match[1].matchAll(/'([^']+)'/g)].map((one) => one[1]).filter((one) => !one.startsWith('!'))
	);
}

const ASKED = testFiles().filter((one) => one.file !== 'src/lib/build/raw-imports.test.ts');

/* Every file under `src` a test could name, loaded only when it is asked for. */
const LOADERS = import.meta.glob(['/src/**/*.{svelte,ts,js,json,html}', '!/src/**/*.test.ts'], {
	query: '?raw',
	import: 'default'
}) as Record<string, () => Promise<string>>;

describe('a file a test reads as text', () => {
	it('is never a stylesheet asked for through ?raw', () => {
		const sheets = ASKED.flatMap(({ file, text }) =>
			[
				...rawImports(file, text).filter((one) => one.endsWith('.css')),
				...rawGlobs(text).filter((one) => /css/.test(one))
			].map((one) => `${file}: ${one}`)
		);
		expect(sheets).toEqual([]);
	});

	it('arrives with something in it', async () => {
		const named = [...new Set(ASKED.flatMap(({ file, text }) => rawImports(file, text)))];
		expect(named.length).toBeGreaterThan(50);
		const empty: string[] = [];
		for (const one of named) {
			const load = LOADERS[`/${one}`];
			expect(load, one).toBeDefined();
			if (!(await load()).trim()) empty.push(one);
		}
		expect(empty).toEqual([]);
	});

	it('finds a stylesheet planted in an import and in a glob', () => {
		expect(rawImports('src/lib/x.test.ts', "import a from '../app.css?raw';")).toEqual([
			'src/app.css'
		]);
		expect(rawGlobs("import.meta.glob(['/src/**/*.{svelte,css}'], { query: '?raw' })")).toEqual([
			'/src/**/*.{svelte,css}'
		]);
	});
});
