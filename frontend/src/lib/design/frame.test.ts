/* A screen that lays itself out gets the whole box, and the layout knows which those are. */

import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

const ROUTES = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', 'routes');
const LAYOUT = join(ROUTES, '+layout.svelte');

/** Components that ARE a frame with something particular in them. */
const FRAMES = ['PageFrame', 'AssetGrid', 'EntityGrid'];

/** Every `+page.svelte`, as the route id SvelteKit gives it. */
function routes(): { id: string; body: string }[] {
	const found: { id: string; body: string }[] = [];
	const walk = (at: string) => {
		for (const entry of readdirSync(at, { withFileTypes: true })) {
			const path = join(at, entry.name);
			if (entry.isDirectory()) walk(path);
			else if (entry.name === '+page.svelte') {
				const folder = relative(ROUTES, dirname(path)).replaceAll('\\', '/');
				found.push({
					id: `/${folder}`.replace(/\/$/, '') || '/',
					body: readFileSync(path, 'utf8')
				});
			}
		}
	};
	walk(ROUTES);
	return found;
}

/** The one place a screen is chosen at RUNTIME rather than written into the route. */
const PANEL_REGISTRY = resolve(
	dirname(fileURLToPath(import.meta.url)),
	'..',
	'organize',
	'panels.ts'
);

/** The components one of the registry's two maps can hand a route, as paths on disk. */
function registered(map: 'PANELS' | 'DETAILS'): string[] {
	const body = readFileSync(PANEL_REGISTRY, 'utf8');
	const lib = resolve(dirname(fileURLToPath(import.meta.url)), '..');

	const opens = body.indexOf(`const ${map}: Record<string, Component> = {`);
	const table = body.slice(opens, body.indexOf('};', opens));
	const wanted = new Set([...table.matchAll(/:\s*([A-Z][A-Za-z0-9]*)\b/g)].map((one) => one[1]));

	return [
		...body.matchAll(/import\s+([A-Z][A-Za-z0-9]*)\s+from\s+'\$lib\/(components\/[^']+\.svelte)'/g)
	]
		.filter((match) => wanted.has(match[1]))
		.map((match) => join(lib, match[2]));
}

/** The markup half of a component, with its comments stripped so a mention in prose does not count. */
function markupOf(body: string): string {
	return body.slice(body.lastIndexOf('</script>')).replace(/<!--[\s\S]*?-->/g, '');
}

function holdsAFrame(body: string): boolean {
	const markup = markupOf(body);
	return FRAMES.some((name) => markup.includes(`<${name}`));
}

/** The written judgement for a screen that fills the box WITHOUT a frame. */
const LAYS_ITSELF_OUT = 'LAYS ITSELF OUT:';

/* The component gallery's screens belong to its own repository, nested at `routes/design/` and
 * absent from a clone. */
const GALLERY = '/design';
const inGallery = (id: string) => id === GALLERY || id.startsWith(`${GALLERY}/`);

/** The library components a route draws, as paths on disk. */
function drawn(body: string): string[] {
	const lib = resolve(dirname(fileURLToPath(import.meta.url)), '..');
	const markup = markupOf(body);
	return [...body.matchAll(/import\s+([A-Z][A-Za-z0-9]*)\s+from\s+'\$lib\/([^']+\.svelte)'/g)]
		.filter((match) => new RegExp(`<${match[1]}\\b`).test(markup))
		.map((match) => join(lib, match[2]));
}

/** Whether this route draws a frame at all. Deliberately "contains one" rather than "opens with
 * one". */
function drawsAFrame(body: string): boolean {
	if (holdsAFrame(body)) return true;
	if (drawn(body).some((path) => holdsAFrame(readFileSync(path, 'utf8')))) return true;

	const markup = markupOf(body);
	if (!body.includes('$lib/organize/panels')) return false;

	const map = /<Detail\b/.test(markup) ? 'DETAILS' : /<Panel\b/.test(markup) ? 'PANELS' : undefined;
	if (!map) return false;

	const panels = registered(map);
	return panels.length > 0 && panels.every((path) => holdsAFrame(readFileSync(path, 'utf8')));
}

/** The route ids the layout gives the whole box to. */
function declared(): Set<string> {
	const body = readFileSync(LAYOUT, 'utf8');
	const block = body.slice(body.indexOf('const FULL_BLEED_ROUTES'));
	// To `]);` and not to the first `]`. Half these ids hold one (`/organize/[queue]`), so stopping
	// at the first `]` would read the list only as far as the third entry.
	const list = block.slice(block.indexOf('['), block.indexOf(']);') + 1);
	/* Comments stripped first. */
	const written = list.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/\/\/[^\n]*/g, ' ');
	return new Set([...written.matchAll(/'([^']+)'/g)].map((match) => match[1]));
}

describe('the layout and the frames agree about which screens lay themselves out', () => {
	it('every screen that draws a frame is given the whole box', () => {
		const missing = routes()
			.filter((route) => drawsAFrame(route.body))
			.map((route) => route.id)
			.filter((id) => !declared().has(id));

		expect(
			missing,
			'These screens draw their own frame and the layout is still padding and scrolling around\n' +
				'them, so their content starts twice as far in and there are two scrollbars, one inside\n' +
				'the other. Add them to FULL_BLEED_ROUTES in routes/+layout.svelte:\n' +
				missing.map((id) => `  ${id}`).join('\n')
		).toEqual([]);
	});

	/** The ids on the list that draw no frame, on one side of the gallery's line. */
	function unframedOnTheList(gallery: boolean): string[] {
		const framed = new Set(
			routes()
				.filter((route) => drawsAFrame(route.body) || route.body.includes(LAYS_ITSELF_OUT))
				.map((route) => route.id)
		);
		return [...declared()].filter((id) => inGallery(id) === gallery && !framed.has(id));
	}

	const unframed = (extra: string[]) =>
		'These are on FULL_BLEED_ROUTES and do not open with a frame, so they get no padding from\n' +
		'the layout and none of their own, which puts their content against the edge of the window\n' +
		'on one route while every other route looks right:\n' +
		extra.map((id) => `  ${id}`).join('\n');

	it('and nothing is on the list that does not draw one', () => {
		const extra = unframedOnTheList(false);
		expect(extra, unframed(extra)).toEqual([]);
	});

	it.skipIf(!routes().some((route) => route.id === GALLERY))(
		'nor any of the gallery screens, where the gallery is in the tree',
		() => {
			const extra = unframedOnTheList(true);
			expect(extra, unframed(extra)).toEqual([]);
		}
	);
});
