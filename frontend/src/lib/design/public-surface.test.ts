/*
 * A module's public surface is its interface. A wide one nobody uses is a promise nobody asked for.
 *
 * A name on the way out of a module is a claim that somebody else may depend on it, and a module
 * claiming forty things and meaning six cannot be read at a glance or changed without checking all
 * forty. So a name exported while every reference to it is inside its own file is refused.
 *
 * It also finds dead code, which is the part worth having: an export can be the only thing keeping
 * a function alive to anything that looks for callers, and such a function is deleted rather than
 * quietly made private.
 *
 * Everywhere a name could be read from is counted: the interface, the browser suite, the build
 * scripts and the guard-rail suite, which reads this source as text. A constant a guard-rail greps
 * for is genuinely public even though no code imports it.
 */

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it, vi } from 'vitest';

const HERE = dirname(fileURLToPath(import.meta.url));
/** `frontend/src`, and the repository root above it. */
const SOURCE = resolve(HERE, '..', '..');
const REPO = resolve(SOURCE, '..', '..');

/** Everywhere a name could be read from, not just where code imports it. */
const READERS = ['frontend/src', 'frontend/e2e', 'frontend/scripts', 'tests'];

/**
 * Exports that are read by something this scan cannot see, with the reason each one is here.
 *
 * The framework reads these two off the module by name to decide how the app is built and served.
 * Nothing imports them and nothing ever will; taking the marker off changes how Sift is served.
 */
const READ_BY_THE_FRAMEWORK: Record<string, string> = {
	'routes/+layout.ts:ssr': 'SvelteKit reads it to know this app is not rendered on a server',
	'routes/+layout.ts:prerender': 'SvelteKit reads it to know the pages are built ahead of time'
};

function everyFile(dir: string, extensions: string[]): string[] {
	const found: string[] = [];
	for (const entry of readdirSync(dir)) {
		if (entry === 'node_modules' || entry === '.svelte-kit') continue;
		const path = join(dir, entry);
		if (statSync(path).isDirectory()) found.push(...everyFile(path, extensions));
		else if (extensions.some((extension) => entry.endsWith(extension))) found.push(path);
	}
	return found;
}

/**
 * The component gallery: its own repository, nested at `routes/design/` and absent from a clone. It
 * is not counted as a reader, so this scan finds the same names in either tree; what only the
 * gallery reads is named in READ_BY_THE_GALLERY and checked against the gallery where it is here.
 */
const GALLERY = 'frontend/src/routes/design/';

const scanned = READERS.flatMap((where) =>
	everyFile(join(REPO, where), ['.ts', '.svelte', '.js', '.mjs', '.py']).map((path) => ({
		where: relative(REPO, path).split('\\').join('/'),
		source: readFileSync(path, 'utf8')
	}))
);
/** This file names every name it excuses, and a name written in an excuse is not a reader. */
const SELF = relative(REPO, fileURLToPath(import.meta.url))
	.split('\\')
	.join('/');
const everywhere = scanned.filter((file) => !file.where.startsWith(GALLERY) && file.where !== SELF);
const gallery = scanned.filter((file) => file.where.startsWith(GALLERY));

/** Exports only the gallery reads, with what it reads each one for. */
const READ_BY_THE_GALLERY: Record<string, string> = {
	'lib/design/entry.ts:DesignCategory': 'the gallery groups its specimens by the category declared',
	'lib/design/entry.ts:specimenId': 'the gallery anchors each specimen at the id its name gives',
	'lib/design/fixtures.svelte.ts:EDITABLE':
		'the gallery opens the edit dialog on this stand-in file',
	'lib/design/fixtures.svelte.ts:lookupInMemory': 'the gallery answers its music lookup from memory'
};

/** The interface's own modules, which are the ones whose surface is being judged. */
const ours = everywhere.filter(
	(file) =>
		file.where.startsWith('frontend/src/') &&
		file.where.endsWith('.ts') &&
		!file.where.endsWith('schema.d.ts') &&
		!file.where.endsWith('fontkit.d.ts')
);

const DECLARED =
	/(?:^|\n)export\s+(?:async\s+)?(?:const|let|function|class|interface|type|enum)\s+(\w+)/g;

// Every test here reads the whole client tree: a couple of seconds on a fast machine, over the
// default five on a two-processor runner.
vi.setConfig({ testTimeout: 30_000 });

describe('nothing is marked shareable that nobody shares', () => {
	it('surveys the interface and everything that reads it', () => {
		// Without this every rule below passes by finding nothing.
		expect(ours.length).toBeGreaterThan(100);
		expect(everywhere.length).toBeGreaterThan(ours.length);
		expect(everywhere.some((file) => file.where.startsWith('tests/'))).toBe(true);
	});

	it('finds a reader for every exported name', () => {
		const unread: string[] = [];
		for (const file of ours) {
			for (const [, name] of file.source.matchAll(DECLARED)) {
				const key = `${file.where.slice('frontend/src/'.length)}:${name}`;
				if (READ_BY_THE_FRAMEWORK[key] || READ_BY_THE_GALLERY[key]) continue;
				const read = everywhere.some(
					(other) => other.where !== file.where && new RegExp(`\\b${name}\\b`).test(other.source)
				);
				if (!read) unread.push(key);
			}
		}

		expect(unread).toEqual([]);
	});

	it('names a reason against every exception', () => {
		// An exception with no explanation is how a list turns into a record of what happened to be
		// true. And an exception for a name that is gone would be inherited by the next name.
		for (const [key, reason] of Object.entries({
			...READ_BY_THE_FRAMEWORK,
			...READ_BY_THE_GALLERY
		})) {
			expect(reason.length, key).toBeGreaterThan(20);
			const [where, name] = key.split(':');
			const file = ours.find((one) => one.where === `frontend/src/${where}`);
			expect(file?.source, key).toMatch(
				new RegExp(
					`export\\s+(?:async\\s+)?(?:const|let|function|class|interface|type|enum)\\s+${name}\\b`
				)
			);
		}
		// Excused for the gallery and read here as well: the excuse is no longer what keeps it.
		const readHere = Object.keys(READ_BY_THE_GALLERY).filter((key) => {
			const [where, name] = key.split(':');
			return everywhere.some(
				(other) =>
					other.where !== `frontend/src/${where}` && new RegExp(`\\b${name}\\b`).test(other.source)
			);
		});
		expect(readHere).toEqual([]);
	});

	it.skipIf(gallery.length === 0)('each name excused for the gallery is one it reads', () => {
		const unread = Object.keys(READ_BY_THE_GALLERY).filter((key) => {
			const name = key.split(':')[1];
			return !gallery.some((file) => new RegExp(`\\b${name}\\b`).test(file.source));
		});
		expect(unread).toEqual([]);
	});
});
