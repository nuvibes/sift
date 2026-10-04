/*
 * THE FIVE ENTITY WALLS CARRY OUT THEIR VERBS THROUGH THE ONE REGISTRY, AND DRAW NO SHEET OF THEIR OWN.
 *
 * A wall that kept its own handlers beside the registry would give every verb two answers: a
 * delete that says something different on the wall from on a tab, a menu that shows the pressed
 * row's stars over a selection of forty. This is what stops a copy growing back.
 *
 * Read from the source rather than by mounting each route, and deliberately: a route mounts only
 * with its store, its address, its paging and its bar all stood in for, and what is being held here
 * is a property of the FILE (which handlers it hands over and which sheets it imports) that the
 * source states outright. The registry's behaviour is proved for real in `wall-verbs.svelte.test.ts`.
 */

import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const WALLS = [
	{ route: 'people', kind: 'person' },
	{ route: 'sites', kind: 'site' },
	{ route: 'tags', kind: 'tag' },
	{ route: 'collections', kind: 'collection' },
	{ route: 'photo-sets', kind: 'photo_set' }
] as const;

/**
 * The sheets `EntityWallFlows` draws for every one of these walls; no wall may import them itself.
 */
const LOCAL_SHEETS = [
	'ConfirmDialog',
	'ShareDialog',
	'VisibilityDialog',
	'HiddenDialog',
	'MergeEntities',
	'PickDialog'
];

/** The writes those sheets were carried out with. A wall calling one is a verb written twice. */
const LOCAL_WRITES = ['tagMany', 'tagPickerFor', 'setHidden', 'setKeptLocal', 'enrichMany'];

function source(route: string): string {
	return readFileSync(`src/routes/${route}/+page.svelte`, 'utf8');
}

/** Every import line's named things and default import, as one list of words. */
function imported(text: string): string[] {
	const words: string[] = [];
	for (const match of text.matchAll(/import\s+(?:type\s+)?([\s\S]*?)\s+from\s+'[^']+'/g)) {
		words.push(
			...match[1]
				.replace(/[{}]/g, ' ')
				.split(/[\s,]+/)
				.filter(Boolean)
		);
	}
	return words;
}

/* Named with %s rather than $route, which vitest quotes: a name to run one wall by with -t. */
describe.each(WALLS.map(({ route, kind }) => [route, kind]))('the %s wall', (route, kind) => {
	const text = source(route);

	it('builds the registry for its own kind', () => {
		expect(text).toMatch(new RegExp(`new WallVerbs\\(\\{\\s*kind: \\(\\) => '${kind}'`));
	});

	it('hands the registry-s handlers to the card menu AND the selection bar, and nothing else', () => {
		/* Both surfaces from one object, so a card's menu and the bar over a selection offer the
		   same verbs with the same words. Any other `handlers` here would be a second list. */
		const handed = [...text.matchAll(/\bhandlers(?:=\{|:\s*)([\w.]+)/g)].map((m) => m[1]);
		expect(handed.length).toBeGreaterThanOrEqual(2);
		expect(new Set(handed)).toEqual(new Set(['verbs.handlers']));
		expect(text).toMatch(/<EntitySelectionBar[\s\S]*?handlers=\{verbs\.handlers\}/);
		expect(text).toMatch(/entityVerbs\(\{[\s\S]*?handlers: verbs\.handlers/);
	});

	it('draws the registry-s sheets once, and imports none of its own', () => {
		expect(text.split('<EntityWallFlows {verbs} />').length - 1).toBe(1);
		const words = imported(text);
		for (const sheet of LOCAL_SHEETS) expect(words, sheet).not.toContain(sheet);
		for (const write of LOCAL_WRITES) expect(words, write).not.toContain(write);
	});

	it('offers Pin, because a pin reorders this very wall', () => {
		// The registry's Pin is opt-in, and every entity wall opts in.
		expect(text).toMatch(/\bpins: true\b/);
		expect(text).toMatch(/pinned=\{verbs\.allPinned\(pickedIds\)\}/);
	});

	it('opens the card-s sharing and Hidden marks through the registry', () => {
		expect(text).toMatch(/onsharing=\{[^}]*verbs\.askToShare\(\[/);
		expect(text).toMatch(/onhidden=\{\(\) => verbs\.askAboutHidden\(/);
	});

	it('takes the menu-s tag list from the registry, where the kind can carry a tag', () => {
		// A wall's own `tagPickerFor(..., () => selection.clear())` would clear the selection after
		// a pick, which a right press that keeps the flyout up for the next pick must not do.
		const lists = [...text.matchAll(/tagPick(?::|\s*\?)\s*([\w.]+)/g)].map((m) => m[1]);
		for (const list of lists) expect(list).toBe('verbs.tagPick');
	});
});

/* The walls on an entity's tabs: the same verbs, and its cards reach the same two panels. */
describe('the wall on an entity-s tabs', () => {
	const text = readFileSync('src/lib/components/entity/RelatedWall.svelte', 'utf8');

	it('pins through the registry, with no pin of its own', () => {
		expect(text).toMatch(/get pins\(\)/);
		expect(imported(text)).not.toContain('pinAll');
		const handed = [...text.matchAll(/\bhandlers(?:=\{|:\s*)([\w.]+)/g)].map((m) => m[1]);
		expect(new Set(handed)).toEqual(new Set(['verbs.handlers']));
	});

	it('opens the card-s sharing and Hidden marks through the registry', () => {
		expect(text).toMatch(/onsharing=\{[^}]*verbs\.askToShare\(\[/);
		expect(text).toMatch(/onhidden=\{[^}]*verbs\.askAboutHidden\(/);
		expect(text).toMatch(/showingHidden: verbs\.allHidden\(/);
	});
});

describe('the sheets the registry draws for all six walls', () => {
	const flows = readFileSync('src/lib/components/entity/EntityWallFlows.svelte', 'utf8');

	it('include the Hidden panel, so no wall draws its own', () => {
		expect(flows).toContain(
			'<HiddenDialog bind:open={verbs.hiddenOpen} target={verbs.hiddenAbout} />'
		);
	});

	it('let the picks go when a share is applied or a merge lands', () => {
		// The rule for a verb over a selection: a share applied from a tab must not keep its
		// picks.
		expect(flows).toContain('onapplied={() => verbs.applied()}');
		expect(flows).toMatch(/onmerged=\{\(\) => \{[\s\S]*?verbs\.applied\(\);/);
	});

	it('stop the rename box where the kind-s route stops', () => {
		expect(flows).toContain('maxlength={verbs.facts.nameLimit}');
	});
});
