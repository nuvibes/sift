/*
 * What `AssetGrid` needs from whoever puts it on a page, checked across every page that does.
 *
 * The grid sizes itself with `flex: 1` rather than `height: 100%`, because on Browse it is one
 * child of a column that also holds the recently-viewed strip and a hundred percent of the column
 * is more than what is left over. That makes its parent part of its contract: **`flex: 1` inside a
 * BLOCK parent does nothing at all.** The grid then lays out at its content height, the scroller it
 * virtualises against never gets a bounded height, and it runs off the bottom of a frame that clips
 * instead of scrolling, so the files are not below the fold, they are unreachable.
 *
 * A test rather than a comment, because a page that wraps the grid in a plain div looks correct in
 * the markup, and the failure needs a real browser and enough files to be visible at all.
 *
 * Static, and honest about being static: it reads the stylesheets rather than measuring a rendered
 * page, so what it proves is that the declaration is there, not that the result looks right. The
 * looking is still a person's job. What it stops is the silent case.
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

/** The class on the element the grid is written inside, if it is wrapped in one. */
function wrapperClass(source: string): string | null {
	const before = source.slice(0, source.indexOf('<AssetGrid'));
	const opens = [...before.matchAll(/<(div|section)\s[^>]*class="([a-z-]+)"[^>]*>/g)];
	const closes = (before.match(/<\/(div|section)>/g) ?? []).length;
	// The innermost element still open where the grid is written. Good enough for these pages,
	// which nest shallowly; a page that defeats it fails loudly rather than passing quietly.
	return opens.length > closes ? (opens[opens.length - 1]?.[2] ?? null) : null;
}

function ruleFor(source: string, className: string): string | null {
	const match = source.match(new RegExp(`\\.${className}\\s*\\{([^}]*)\\}`));
	return match ? match[1] : null;
}

/**
 * The grid's own opening tag, from `<AssetGrid` to the `>` that closes it.
 *
 * Scanned rather than matched with one expression, because an attribute value is an arbitrary
 * Svelte expression and `{{}}`, `'>'` and `"a > b"` all appear in these tags. Brace depth and
 * quote state are tracked so the first `>` OUTSIDE both is the end: a regexp stopping at the
 * first `>` finds one inside `query={{ sort: 'a>b' }}` and reads half a tag.
 */
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

/** Whether this page hands the grid a pin, as a bare prop rather than as a computed one. */
function offersPin(source: string): boolean {
	return /(?:^|\s)pinnable(?:\s|=\{true\}|>|\/>)/.test(gridTag(source));
}

/* `join` uses a backslash on Windows, so a list of paths written the way they are read in the
   repository matches nothing there. One spelling, here. */
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
			// Unwrapped: the grid is the page's own root and the app frame supplies the column.
			// See `main.full-bleed` in the root layout. Nothing for this page to declare.
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

/*
 * Which screens offer Pin, written down.
 *
 * A ledger rather than a rule: whether a screen curates is a product decision, and recording it
 * here makes changing one a line in a diff rather than a side effect. A screen joining or leaving
 * fails here; move its name in the same commit.
 */
const CURATES = [
	'src/routes/favorites/+page.svelte',
	'src/routes/hidden/+page.svelte',
	'src/routes/loops/+page.svelte',
	'src/routes/people/[id]/+page.svelte',
	'src/routes/photo-sets/[id]/+page.svelte',
	'src/routes/sites/[id]/+page.svelte',
	// A song's page, as every other entity's: its own files, pinned to the top where somebody wants.
	'src/routes/songs/[id]/+page.svelte',
	'src/routes/tags/[id]/+page.svelte'
];

/* Browse is the whole library in the order it was asked for, and Recently viewed is a record of
   what happened: a pin in front of either would be answering a different question from the one
   the screen asks. */
const DOES_NOT = ['src/routes/browse/+page.svelte', 'src/routes/recent/+page.svelte'];

/* The related wall decides per KIND at runtime (`pinnableOf`), so it belongs in neither list and
   its absence from both is the assertion. */
const DECIDES_FOR_ITSELF = ['src/lib/components/entity/RelatedWall.svelte'];

describe('which screens offer to pin a file is a decision, and it is written down', () => {
	const pages = hosts.filter((path) => !path.endsWith('.test.ts')).map(normalise);

	it('every page that hosts the grid is in exactly one of the three lists', () => {
		// The positive control, and it is the one that matters: a page added to neither list is a
		// screen that quietly gained or lost the verb, which is the whole thing this holds.
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
 * WHAT A TILE READS, AND WHETHER EVERY WALL ACTUALLY SENDS IT.
 *
 * `Tile.svelte` is one component and it draws every wall in Sift. It takes its marks off the row it
 * is handed: the view tally, the vault eye, whether that eye is filled, the torn page for a file
 * whose bytes have gone, the sharing glyph. And `TileItem` declares them all optional, which is
 * honest: a row may be a file or may be a mark of one.
 *
 * The trouble is what "absent" looks like. A row that omits a field does not draw a WRONG mark, it
 * draws none, and nothing anywhere says so. The Loops wall, the only wall whose rows are not
 * `AssetSummary`, is where a missing field would hide.
 *
 * So this reads the tile's own list and requires both row shapes to carry it. It reads the SCHEMA
 * the server publishes rather than the hand-written types, because that is the thing a server
 * change actually moves: add a field to the media grid's row, forget the wall of marks, and this
 * fails on the name before any screen is opened.
 */
const SCHEMA = JSON.parse(readFileSync('openapi.json', 'utf8')) as {
	components: { schemas: Record<string, { properties?: Record<string, unknown> }> };
};

/** Every field name `TileItem` names, read from the component rather than copied beside it. */
function factsATileReads(): string[] {
	const source = readFileSync('src/lib/components/Tile.svelte', 'utf8');
	const at = source.indexOf('export type TileItem');
	const declaration = source.slice(at, source.indexOf('>;', at));
	// Not preceded by `[`, which is what skips the `['schemas']` in the type's own path into the
	// generated schema. Those are plumbing; the Pick list is the thing being read.
	return [...declaration.matchAll(/(?<!\[)'([a-z_]+)'/g)].map((found) => found[1]);
}

function fieldsOf(schema: string): string[] {
	return Object.keys(SCHEMA.components.schemas[schema]?.properties ?? {});
}

describe('every wall sends what the shared tile reads', () => {
	const facts = factsATileReads();

	it('finds the list, so an empty sweep cannot pass for a clean one', () => {
		// The same positive control the wrapper sweep above carries, and for the same reason: a
		// regexp that stops matching would turn this whole suite into an assertion about nothing.
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
 * ROWS AND FILES ARE DIFFERENT IDS, AND ONE CALL MUST NOT BE HANDED THE OTHER.
 *
 * Every shared verb acts on a FILE, so the ids reaching `FileVerbs` have been through `picked`:
 * row ids mapped to `asset_id` and de-duplicated, because two marks of one video must not ask the
 * server to delete it twice. A verb a WALL declares is about the row: forgetting a mark is not an
 * act on the video, and two marks of one video are two things to forget.
 *
 * The two are both `string[]` and both in scope at the same two call sites, so nothing in the type
 * system separates them and nothing on screen looks wrong when they are swapped: the Loops wall
 * sending asset ids as loop ids gets every row back skipped, and says "2 loops could not be
 * included. Sift could not find the files."
 *
 * Static, and honest about it: what this proves is that the call sites still name the rows. It is
 * here rather than as a behavioural test because reaching the bar needs a selection and reaching
 * the menu needs a context menu, and neither is worth a harness to assert one identifier.
 */
describe('a wall-declared verb is handed rows, never picked files', () => {
	const source = readFileSync('src/lib/components/AssetGrid.svelte', 'utf8');

	it('calls it in both places: the bar and the menu', () => {
		// The positive control. A renamed helper would otherwise make the rule below vacuous.
		expect([...source.matchAll(/wallVerbs\(/g)]).toHaveLength(3); // two calls, one declaration
	});

	it('passes the rows at every call', () => {
		const passed = [...source.matchAll(/wallVerbs\(([^)]*)\)/g)]
			.map((found) => found[1])
			// The declaration itself, which names its parameter with a type.
			.filter((argument) => !argument.includes(':'));

		expect(passed).toEqual(['rows', 'rows']);
	});
});
