/* Nothing the registry imports imports the registry back. */
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join, normalize } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const LIB = join(dirname(fileURLToPath(import.meta.url)), '..');
const REGISTRY = join(LIB, 'organize', 'panels.ts');

/* A value import (not `import type`, which is erased and cannot make a cycle), single or multi-line. */
const IMPORT = /^\s*import\s+(?!type\b)(?:[^'";]*?\s+from\s+)?['"]([^'"]+)['"]/gm;

/** The file an import names, or null for a package or anything outside the tree. */
function resolveImport(from: string, spec: string): string | null {
	let base: string;
	if (spec.startsWith('$lib/')) base = join(LIB, spec.slice('$lib/'.length));
	else if (spec.startsWith('.')) base = normalize(join(dirname(from), spec));
	else return null;
	for (const file of [
		base,
		`${base}.ts`,
		`${base}.svelte.ts`,
		`${base}.js`,
		join(base, 'index.ts')
	]) {
		if (existsSync(file) && !file.endsWith('/') && /\.(ts|js|svelte)$/.test(file)) return file;
	}
	return null;
}

function importsOf(file: string): string[] {
	const text = readFileSync(file, 'utf8');
	return [...text.matchAll(IMPORT)]
		.map((match) => resolveImport(file, match[1]))
		.filter((one): one is string => one !== null);
}

/** Every path of imports from the registry that comes back to it. */
function loopsBack(): string[][] {
	const loops: string[][] = [];
	const seen = new Set<string>();
	const stack: string[][] = [[REGISTRY]];
	while (stack.length > 0) {
		const path = stack.pop()!;
		for (const next of importsOf(path.at(-1)!)) {
			if (next === REGISTRY) {
				loops.push([...path, next].map((file) => file.slice(LIB.length + 1)));
				continue;
			}
			if (seen.has(next)) continue;
			seen.add(next);
			stack.push([...path, next]);
		}
	}
	return loops;
}

describe('the registry of panels', () => {
	it('is imported by nothing it imports, so a panel can be evaluated first', () => {
		expect(loopsBack()).toEqual([]);
	});

	it('reads the imports it is walking: the wall of groups reaches the addresses', () => {
		/* A reader that followed nothing would find no loop anywhere. */
		const wall = join(LIB, 'components', 'faces', 'FaceGroups.svelte');
		expect(importsOf(wall)).toContain(join(LIB, 'organize', 'addresses.ts'));
		expect(importsOf(REGISTRY)).toContain(
			join(LIB, 'components', 'organize', 'FaceGroupsPanel.svelte')
		);
	});
});
