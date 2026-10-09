/*
 * What `AssetGrid` needs from every page that hosts it: `flex: 1` does nothing in a BLOCK parent,
 * and the grid then runs off a clipping frame. Static: it proves the declaration, not the look.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const ROOT = 'src';

function everySvelteFile(dir: string): string[] {
	const found: string[] = [];
	for (const entry of readdirSync(dir)) {
		const path = join(dir, entry);
		if (statSync(path).isDirectory()) found.push(...everySvelteFile(path));
		else if (entry.endsWith('.svelte')) found.push(path);
	}
	return found;
}

function wrapperClass(source: string): string | null {
	const before = source.slice(0, source.indexOf('<AssetGrid'));
	const opens = [...before.matchAll(/<(div|section)\s[^>]*class="([a-z-]+)"[^>]*>/g)];
	const closes = (before.match(/<\/(div|section)>/g) ?? []).length;
	// Good enough for these shallow pages; a page that defeats it fails loudly.
	return opens.length > closes ? (opens[opens.length - 1]?.[2] ?? null) : null;
}

function ruleFor(source: string, className: string): string | null {
	const match = source.match(new RegExp(`\\.${className}\\s*\\{([^}]*)\\}`));
	return match ? match[1] : null;
}

/** Scanned, not matched: attribute values hold `>` inside braces and quotes. */
function gridTag(source: string): string {
	const start = source.indexOf('<AssetGrid');
	let depth = 0;
	let quote = '';
	for (let at = start; at < source.length; at += 1) {
		const char = source[at];
		if (quote) {
			if (char === quote) quote = '';
			continue;
		}
		if (char === '"' || char === "'" || char === '`') quote = char;
		else if (char === '{') depth += 1;
		else if (char === '}') depth -= 1;
		else if (char === '>' && depth === 0) return source.slice(start, at + 1);
	}
	return source.slice(start);
}

function offersPin(source: string): boolean {
	return /(?:^|\s)pinnable(?:\s|=\{true\}|>|\/>)/.test(gridTag(source));
}

/* `join` uses a backslash on Windows. */
function normalise(path: string): string {
	return path.split('\\').join('/');
}

const hosts = everySvelteFile(ROOT).filter((path) =>
	readFileSync(path, 'utf8').includes('<AssetGrid')
);

describe('every page that hosts the asset grid gives it a column to size against', () => {
	it('finds the pages, so an empty sweep cannot pass for a clean one', () => {
		// An absence check over an empty list passes on nothing.
		expect(hosts.length).toBeGreaterThanOrEqual(4);
	});

	it.each(hosts)('%s', (path) => {
		const source = readFileSync(path, 'utf8');
		const wrapper = wrapperClass(source);
		if (wrapper === null) {
			// The grid is the page's own root (`main.full-bleed`).
			return;
		}
		const rule = ruleFor(source, wrapper);
		expect(rule, `.${wrapper} wraps the grid but has no rule in this file`).not.toBeNull();
		expect(rule, `.${wrapper} is a block, so the grid's own flex: 1 does nothing`).toMatch(
			/display:\s*flex/
		);
		expect(rule, `.${wrapper} is a row, so the grid gets no height to scroll within`).toMatch(
			/flex-direction:\s*column/
		);
	});
});

/* Which screens offer Pin: a ledger, so a change is a line in a diff. */
const CURATES = [
	'src/routes/favorites/+page.svelte',
	'src/routes/hidden/+page.svelte',
	'src/routes/loops/+page.svelte',
	'src/routes/people/[id]/+page.svelte',
	'src/routes/photo-sets/[id]/+page.svelte',
	'src/routes/sites/[id]/+page.svelte',
	'src/routes/songs/[id]/+page.svelte',
	'src/routes/tags/[id]/+page.svelte'
];

const DOES_NOT = ['src/routes/browse/+page.svelte', 'src/routes/recent/+page.svelte'];

/* Decided per kind at runtime (`pinnableOf`), so in neither list. */
const DECIDES_FOR_ITSELF = ['src/lib/components/entity/RelatedWall.svelte'];

describe('which screens offer to pin a file is a decision, and it is written down', () => {
	const pages = hosts.filter((path) => !path.endsWith('.test.ts')).map(normalise);

	it('every page that hosts the grid is in exactly one of the three lists', () => {
		// The positive control: a page in neither list quietly gained or lost the verb.
		const declared = [...CURATES, ...DOES_NOT, ...DECIDES_FOR_ITSELF].sort();
		expect(pages.sort()).toEqual(declared);
	});

	it.each(CURATES)('%s offers it', (path) => {
		expect(offersPin(readFileSync(path, 'utf8'))).toBe(true);
	});

	it.each(DOES_NOT)('%s does not', (path) => {
		expect(offersPin(readFileSync(path, 'utf8'))).toBe(false);
	});

	it('the related wall works it out per kind rather than declaring it', () => {
		const source = readFileSync(DECIDES_FOR_ITSELF[0], 'utf8');
		expect(source).toContain('pinnableOf(showing)');
		expect(offersPin(source), 'it hands the grid a computed value, not a bare prop').toBe(false);
	});
});

/*
 * Every field `TileItem` reads must be in both row shapes the server publishes, or a missing one
 * draws no mark and says nothing (the Loops wall).
 */
const SCHEMA = JSON.parse(readFileSync('openapi.json', 'utf8')) as {
	components: { schemas: Record<string, { properties?: Record<string, unknown> }> };
};

function factsATileReads(): string[] {
	const source = readFileSync('src/lib/components/Tile.svelte', 'utf8');
	const at = source.indexOf('export type TileItem');
	const declaration = source.slice(at, source.indexOf('>;', at));
	// Not preceded by `[`, which skips the schema path.
	return [...declaration.matchAll(/(?<!\[)'([a-z_]+)'/g)].map((found) => found[1]);
}

function fieldsOf(schema: string): string[] {
	return Object.keys(SCHEMA.components.schemas[schema]?.properties ?? {});
}

describe('every wall sends what the shared tile reads', () => {
	const facts = factsATileReads();

	it('finds the list, so an empty sweep cannot pass for a clean one', () => {
		// The positive control.
		expect(facts.length).toBeGreaterThanOrEqual(10);
		expect(facts).toContain('views');
	});

	it.each(['AssetSummary', 'LoopSummary'])('%s carries all of them', (schema) => {
		const sent = fieldsOf(schema);
		expect(sent.length, `${schema} is not in the published schema`).toBeGreaterThan(0);
		expect(facts.filter((fact) => !sent.includes(fact))).toEqual([]);
	});
});

/*
 * A wall-declared verb is handed ROWS, never the picked files: both are `string[]` in scope at the
 * same call sites, and swapping them skips every row.
 */
describe('a wall-declared verb is handed rows, never picked files', () => {
	const source = readFileSync('src/lib/components/AssetGrid.svelte', 'utf8');

	it('calls it in both places: the bar and the menu', () => {
		expect([...source.matchAll(/wallVerbs\(/g)]).toHaveLength(3); // two calls, one declaration
	});

	it('passes the rows at every call', () => {
		const passed = [...source.matchAll(/wallVerbs\(([^)]*)\)/g)]
			.map((found) => found[1])
			.filter((argument) => !argument.includes(':'));

		expect(passed).toEqual(['rows', 'rows']);
	});
});
