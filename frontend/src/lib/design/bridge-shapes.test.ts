/* THE SHAPES THAT CROSS THE PROCESS BOUNDARY, AND WHETHER THE TWO SIDES AGREE ABOUT THEM. */

import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

/** `frontend/src`, and the repository root above it: the shell is a sibling of `frontend`. */
const SOURCE = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const REPO = resolve(SOURCE, '..', '..');

const SHELL = join(REPO, 'desktop', 'src');
const PAGE = join(SOURCE, 'lib', 'bridge');
const SHARED = join(REPO, 'shared');

/** The most shapes that may still be written out by hand on both sides. */
const STILL_WRITTEN_TWICE = 11;

function everyModule(dir: string): string[] {
	const found: string[] = [];
	for (const entry of readdirSync(dir)) {
		if (entry === 'node_modules') continue;
		const path = join(dir, entry);
		if (statSync(path).isDirectory()) found.push(...everyModule(path));
		else if (entry.endsWith('.ts') && !entry.endsWith('.test.ts')) found.push(path);
	}
	return found;
}

/** One declaration: where it is, what it extends if anything, and the text between its braces. */
interface Declared {
	where: string;
	extends: string;
	body: string;
}

/** EVERY `interface Name { ... }` in a tree, by name. */
function interfacesIn(root: string): Map<string, Declared[]> {
	const found = new Map<string, Declared[]>();
	for (const path of everyModule(root)) {
		const source = readFileSync(path, 'utf8');
		for (const match of source.matchAll(/(?:export\s+)?interface\s+([A-Z]\w*)([^{;]*)\{/g)) {
			let depth = 1;
			// `matchAll` always sets it; the type says it may not, and a `!` in a test file is the
			// smaller of the two evils against a `?? 0` that would silently read from the top.
			let at = match.index! + match[0].length;
			const from = at;
			while (at < source.length && depth > 0) {
				if (source[at] === '{') depth += 1;
				else if (source[at] === '}') depth -= 1;
				at += 1;
			}
			const declarations = found.get(match[1]) ?? [];
			declarations.push({
				where: relative(REPO, path).split('\\').join('/'),
				extends: match[2].trim(),
				body: source.slice(from, at - 1)
			});
			found.set(match[1], declarations);
		}
	}
	return found;
}

/** The field names an interface body declares, with a `?` kept on the optional ones. */
function fieldsOf(body: string): string[] {
	const text = body.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\n]*/g, '');
	const names: string[] = [];
	let depth = 0;
	let line = '';
	const take = () => {
		const match = /^\s*(?:readonly\s+)?([A-Za-z_$][\w$]*)(\?)?\s*:/.exec(line);
		if (match) names.push(match[1] + (match[2] ?? ''));
		line = '';
	};
	for (const character of text) {
		if ('{(['.includes(character)) depth += 1;
		else if ('})]'.includes(character)) depth -= 1;
		if (depth === 0 && (character === ';' || character === '\n')) take();
		else line += character;
	}
	take();
	return [...new Set(names)].sort();
}

describe('the shapes that cross the process boundary', () => {
	const shell = interfacesIn(SHELL);
	const page = interfacesIn(PAGE);
	const shared = interfacesIn(SHARED);
	const twice = [...shell.keys()].filter((name) => page.has(name)).sort();

	/** Every copy of a name, in both trees, as one list to compare against itself. */
	const copiesOf = (name: string): Declared[] => [
		...(shell.get(name) ?? []),
		...(page.get(name) ?? [])
	];

	it('is reading all three trees rather than an empty one', () => {
		/* The way a scan like this fails is by finding nothing and passing for ever. */
		expect(shell.size, 'nothing was read from desktop/src').toBeGreaterThan(10);
		expect(page.size, 'nothing was read from lib/bridge').toBeGreaterThan(5);
		expect(
			shared.size,
			'nothing was read from shared/, so the durable answer is gone'
		).toBeGreaterThan(0);
	});

	it('is not growing a new hand-written pair', () => {
		expect(
			twice.length,
			`${twice.length} shapes are written out on both sides of the process boundary, and the ` +
				`ratchet is ${STILL_WRITTEN_TWICE}. There is one place for a shape that crosses: ` +
				`shared/bridge.d.ts, which both trees import and neither owns. Put it there instead ` +
				`of writing it out a second time.`
		).toBeLessThanOrEqual(STILL_WRITTEN_TWICE);
	});

	it('does not write out a shape that has already moved', () => {
		/* Once a shape is shared, a copy of it in either tree is worse than the copy that was
		   there before: it compiles, it shadows, and it is the one thing nothing compares. */
		for (const name of shared.keys()) {
			expect(
				copiesOf(name).map((one) => one.where),
				`${name} is declared in shared/bridge.d.ts and written out again. Import it.`
			).toEqual([]);
		}
	});

	it.each([...shell.keys()].filter((name) => page.has(name)).sort())(
		'%s says the same fields in every copy of it',
		(name) => {
			const copies = copiesOf(name).filter((one) => one.extends === '');
			const first = copies[0]!;
			for (const other of copies.slice(1)) {
				expect(
					fieldsOf(other.body),
					`${name} is written out in ${first.where} and again in ${other.where}, and the two ` +
						`no longer agree. There is no schema between the shell and the page unless the ` +
						`shape is in shared/bridge.d.ts: whichever side is behind reads undefined for a ` +
						`field the other one sends, which for a boolean is false: a screen saying the ` +
						`opposite of what the shell answered. Move it to shared/bridge.d.ts.`
				).toEqual(fieldsOf(first.body));
			}
		}
	);

	it('says which copies it cannot check at all', () => {
		/* A declaration that extends another gets some of its fields from somewhere else, and
		   this is a text scan. */
		const unreachable = twice
			.flatMap((name) => copiesOf(name).map((one) => ({ name, one })))
			.filter(({ one }) => one.extends !== '')
			.map(({ name, one }) => `${name} in ${one.where} (${one.extends})`);
		expect(unreachable).toEqual([
			'StorageReport in desktop/src/storage.ts (extends DataLocations)'
		]);
	});

	it('finds the fields of a shape rather than nothing at all', () => {
		/* The known positive. */
		expect(fieldsOf('a: string;\n/** b */ b?: number;\nc: { d: string };')).toEqual([
			'a',
			'b?',
			'c'
		]);
	});
});
