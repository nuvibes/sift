/*
 * A screen that is a panel is ONLY ever a panel.
 *
 * An asset and settings each have a real address. Answering it two ways (a panel over the screen
 * you were on when reached from inside the app, and a full page of its own when opened directly or
 * refreshed) is one address rendering two different screens, and refreshing while watching
 * something would replace the player with a different one.
 *
 * The behaviour is covered in a browser (`e2e/modal-only.spec.ts`). This is the other half: the
 * shape that makes it possible. A route can only draw a page form if it renders the content itself,
 * so no route may import what a panel draws, and the two routes at panel addresses must do the one
 * thing they are for. Static, because the day somebody adds a third panel and gives it a page, no
 * behavioural test exists yet to catch it.
 */

import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

const SOURCE = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const ROUTES = join(SOURCE, 'routes');

/**
 * What a panel draws, which therefore belongs to the panel and to nothing else.
 *
 * Named by component rather than by address: the failure is a route rendering this content, and
 * that is true whichever address somebody hangs it on.
 */
const PANEL_CONTENT = ['AssetView', 'SettingsShell', 'SettingsPane'];

/** The routes that exist only to put a panel up, and the function each has to call to do it. */
const PANEL_ROUTES: Record<string, string> = {
	'asset/[id]/+page.svelte': 'enterAsset',
	'settings/[[section]]/+page.svelte': 'enterSettings'
};

function pagesUnder(directory: string): string[] {
	const found: string[] = [];
	for (const entry of readdirSync(directory)) {
		const full = join(directory, entry);
		if (statSync(full).isDirectory()) found.push(...pagesUnder(full));
		else if (entry === '+page.svelte') found.push(full);
	}
	return found;
}

const PAGES = pagesUnder(ROUTES);

describe('screens that are panels', () => {
	it('finds the routes at all, so an empty sweep cannot pass', () => {
		expect(PAGES.length).toBeGreaterThan(15);
	});

	it.each(PAGES.map((path) => [relative(ROUTES, path), path]))(
		'%s does not render what a panel draws',
		(_name, path) => {
			const source = readFileSync(path, 'utf8');
			for (const component of PANEL_CONTENT) {
				expect(
					source.includes(`${component}.svelte'`),
					`this route imports ${component}, which is a panel's content: a route that renders it is a page form of a panel`
				).toBe(false);
			}
		}
	);

	it.each(Object.entries(PANEL_ROUTES))('%s puts the panel up instead', (route, enters) => {
		const source = readFileSync(join(ROUTES, route), 'utf8');
		expect(
			source.includes(`${enters}(`),
			`this route runs only on a cold load, and its whole job is to call ${enters}`
		).toBe(true);
	});
});

/*
 * ...AND NOTHING LINKS TO ONE WITH A BARE ANCHOR.
 *
 * The other way to get the page form is to link to it. `/asset/{id}` is a real address and always a
 * panel, but WHAT IS BEHIND IT is decided by how it was reached: a tile pushes the address and the
 * screen stays mounted underneath, while an anchor runs the route, which exists for somebody
 * arriving cold, so it marks the panel as having nothing behind it. The screen underneath is torn
 * down and rebuilt, and closing afterwards goes to the library instead of back: from the
 * downloads queue, or from the source link in a clip's file facts, where it would blank the whole
 * wall behind the panel.
 *
 * Static, and it has to be: the difference is invisible in the markup, both forms render, both
 * navigate, and the only tell is what is left underneath afterwards.
 */

function svelteFilesUnder(directory: string): string[] {
	const found: string[] = [];
	for (const entry of readdirSync(directory)) {
		if (entry === 'node_modules') continue;
		const full = join(directory, entry);
		if (statSync(full).isDirectory()) found.push(...svelteFilesUnder(full));
		else if (entry.endsWith('.svelte')) found.push(full);
	}
	return found;
}

/**
 * Every anchor's own start tag, so the check is PER LINK rather than per file.
 *
 * !! ASKED OF THE FILE, THIS GATE WOULD SURVIVE ITS OWN MUTATION TEST. One component draws two
 * asset links; a bare anchor in place of the second leaves the first one's `AssetLink` still in the
 * file, the substring still matches, and the gate would pass on markup carrying exactly the fault
 * it exists to refuse. A file-level answer to a per-element question is
 * not a weaker gate, it is a gate that stops working the moment a screen has two of anything.
 *
 * Brace depth rather than the first `>`: `onclick={(event) => openAssetInstead(...)}` has an arrow
 * in it, and the address may be a template string with `${...}` in it. Both live inside braces, so
 * counting depth finds the real end of the start tag where a search for `>` finds the arrow.
 */
function anchorTags(source: string): string[] {
	const tags: string[] = [];
	for (let at = source.indexOf('<a'); at !== -1; at = source.indexOf('<a', at + 2)) {
		// `<article`, `<aside`: the tag name has to actually end here.
		if (/[A-Za-z-]/.test(source[at + 2] ?? '')) continue;
		let depth = 0;
		let end = at;
		while (end < source.length) {
			const character = source[end];
			if (character === '{') depth += 1;
			else if (character === '}') depth -= 1;
			else if (character === '>' && depth === 0) break;
			end += 1;
		}
		tags.push(source.slice(at, end));
	}
	return tags;
}

/** Whether one anchor's start tag points at an asset. The plural spelling too: `/assets/{id}`
 *  names no route in this application at all, and a link written that way is still a link. */
function linksToAnAsset(tag: string): boolean {
	return /href=[{"`][^]*?\/assets?\//.test(tag);
}

/**
 * The rule an asset link has to apply, on the anchor itself.
 *
 * `AssetLink` is the component every link that can use one uses; this is for the handful that keep
 * their own markup because the screen around them styles it: a caller's class does not reach
 * inside a component's scoped stylesheet, so moving those into one would have silently unstyled
 * four screens.
 */
const OPENS_IT_PROPERLY = 'openAssetInstead';

const MARKUP = svelteFilesUnder(SOURCE).filter(
	(path) => !path.endsWith(join('common', 'AssetLink.svelte'))
);

describe('links to a file', () => {
	it('finds the markup at all, so an empty sweep cannot pass', () => {
		expect(MARKUP.length).toBeGreaterThan(100);
	});

	it('finds the links it is about, so a parser that stopped matching cannot pass', () => {
		const linking = MARKUP.flatMap((path) => anchorTags(readFileSync(path, 'utf8'))).filter(
			linksToAnAsset
		);
		expect(linking.length).toBeGreaterThan(0);
	});

	it.each(MARKUP.map((path) => [relative(SOURCE, path), path]))(
		'%s opens the panel where it stands',
		(_name, path) => {
			for (const tag of anchorTags(readFileSync(path, 'utf8'))) {
				if (!linksToAnAsset(tag)) continue;
				expect(
					tag.includes(OPENS_IT_PROPERLY),
					`this anchor links to an asset without ${OPENS_IT_PROPERLY}, so it runs the /asset/[id] route and tears down the screen behind it. Use AssetLink, or put the handler on this anchor:\n${tag}`
				).toBe(true);
			}
		}
	);
});
