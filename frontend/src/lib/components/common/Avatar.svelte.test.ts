/*
 * The ground under a picture that is a mark.
 *
 * A site's logo is drawn at the letter's measure on the tint the letter would have stood on, so a
 * wall of sites reads as one thing whether a row has a logo or an initial. Both halves live in this
 * file's stylesheet, aimed at a class on the root, so what a test can hold is that the root wears
 * it; the measure itself is checked in a browser.
 */

import { readFileSync } from 'node:fs';

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import Avatar from './Avatar.svelte';
import { composite, contrastRatio, parseColour, type Rgba } from '$lib/design/colour';

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

beforeEach(() => {
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
});

function draw(props: Record<string, unknown>): HTMLElement {
	drawn = mount(Avatar, {
		target: host,
		props: { name: 'Quillhouse', ...props }
	}) as Record<string, unknown>;
	flushSync();
	const root = host.querySelector<HTMLElement>('.avatar');
	if (!root) throw new Error('the avatar drew no root element');
	return root;
}

it('says on the root that this picture is a mark, which is what puts it on the name tint', () => {
	const root = draw({ src: '/api/sites/x/cover', shape: 'portrait', mark: true });
	expect(root.classList.contains('mark')).toBe(true);
	expect(root.classList.contains('portrait')).toBe(true);
});

it('says nothing of the sort for a still out of a clip, which is cropped to the box', () => {
	const root = draw({ src: '/api/assets/x/thumb', shape: 'portrait' });
	expect(root.classList.contains('mark')).toBe(false);
});

it('carries the name hue on the root, so the letter and a mark stand on one colour', () => {
	const root = draw({ src: null, shape: 'portrait', mark: false });
	expect(root.style.getPropertyValue('--hue')).not.toBe('');
});

/*
 * Every mark at one measure, the letter's.
 *
 * Read out of the stylesheet rather than measured: `30cqi` is a container query unit, and jsdom
 * lays nothing out, so a computed style would be empty whatever the rule said. What is held is that
 * the box is a size and not a ceiling, at the same measure as the monogram's type.
 */
it("draws a mark at the letter's own measure, never at the size the picture happens to be", () => {
	const source = readFileSync('src/lib/components/common/Avatar.svelte', 'utf8');
	const box = source.slice(source.indexOf('.avatar.portrait .picture.mark'));
	const rule = box.slice(0, box.indexOf('}'));

	expect(rule).toContain('inline-size: 30cqi');
	expect(rule).toContain('block-size: 30cqi');
	// A ceiling would make a mark's size a fact about the site's web server.
	expect(rule).not.toContain('max-inline-size');
	expect(rule).not.toContain('max-block-size');
	// The letter is set at the same measure, which is what "the same size as the letter" means.
	expect(source).toContain('font-size: 30cqi');
	// And it is fitted into that box whole rather than cropped to it: a logo has no spare edges.
	expect(source).toContain('object-fit: contain');
});

/*
 * The ground is the mark's own colour, the way an entity page's backdrop is its cover's. The blur
 * and the scrim are the page's own tokens, so a tile and a page cannot wash a picture at two
 * strengths.
 */
it('lays the mark down again, blurred, as the tile it stands on', () => {
	const root = draw({ src: '/api/sites/x/cover', shape: 'portrait', mark: true });
	const ground = root.querySelector<HTMLImageElement>('img.ground');

	expect(ground).not.toBeNull();
	// The same address, so the browser serves it from what it already has.
	expect(ground?.getAttribute('src')).toBe('/api/sites/x/cover');
	// It is the picture below, out of focus, and there is nothing in it to read out.
	expect(ground?.getAttribute('aria-hidden')).toBe('true');
	expect(ground?.getAttribute('alt')).toBe('');
	expect(root.querySelector('.ground-scrim')).not.toBeNull();

	const source = readFileSync('src/lib/components/common/Avatar.svelte', 'utf8');
	const rule = source.slice(source.indexOf('.avatar.portrait.mark .ground {'));
	expect(rule.slice(0, rule.indexOf('}'))).toContain('blur(var(--band-blur))');
	const scrim = source.slice(source.indexOf('.avatar.portrait.mark .ground-scrim {'));
	// The page's own scrim, and no other.
	expect(scrim.slice(0, scrim.indexOf('}'))).toContain('background: var(--band-scrim)');
});

it('lays no ground under a still out of a clip, which fills the tile on its own', () => {
	const root = draw({ src: '/api/assets/x/thumb', shape: 'portrait' });
	expect(root.querySelector('img.ground')).toBeNull();
});

/*
 * One ground for every mark: nothing about the mark's colour reaches the tile. The pack's tone is
 * not an input here, and the stylesheet names no ground but the tint and no scrim but the page's; a
 * separate ground for dark marks would make two kinds of tile.
 */
function source(): string {
	return readFileSync('src/lib/components/common/Avatar.svelte', 'utf8');
}

function redraw(props: Record<string, unknown>): HTMLElement {
	if (drawn) unmount(drawn);
	drawn = null;
	host.replaceChildren();
	return draw(props);
}

it('lays the same blurred copy and the same scrim under every portrait mark', () => {
	const root = redraw({ src: '/api/sites/x/cover', shape: 'portrait', mark: true });
	expect(root.querySelector('img.ground')).not.toBeNull();
	expect(root.querySelector('.ground-scrim')).not.toBeNull();
	// No class on the root that a second ground could hang from.
	const classes = [...root.classList].filter((one) => !one.startsWith('svelte-'));
	expect(classes.sort()).toEqual(['avatar', 'mark', 'portrait']);

	const text = source();
	// Nothing in the stylesheet or the props varies the ground by what the mark looks like.
	expect(text).not.toMatch(/\btone\b\s*[?:=]|tone-|--tone-ground|--tint-light|--mark-scrim/);
	const tile = text.slice(text.indexOf('.avatar.portrait.mark {'));
	expect(tile.slice(0, tile.indexOf('}'))).toContain('background: var(--tint);');
});

it('lays no ground under a mark in a face box, which the mark fills on its own', () => {
	const root = redraw({ src: '/api/site-art/x', shape: 'face', mark: true });
	expect(root.querySelector('img.ground')).toBeNull();
	expect(root.querySelector('.ground-scrim')).toBeNull();
});

/*
 * The arithmetic, from the two stylesheets rather than from a comment: every hue a name can give,
 * in every base, a one-colour mark on the thinnest and thickest ground the blur can make under it
 * (its blurred copy transparent, and solid).
 *
 * A light mark is held to the floors: 4.5:1 for pure white, because a wordmark is words, and 3:1
 * for one at the edge of the `light` tone (level 190, `scripts/site_icon_art.py tone_of`).
 *
 * A plain black mark is not held to a floor: on this ground it reads at 1.03:1 at the worst hue,
 * and one ground for every tile is preferred over it. That number is what is held, so a change to
 * the tint or scrim that makes the dark case worse fails here.
 */
it('holds a light mark above the floor on the one ground, and pins the black mark where it was decided', () => {
	const avatar = source();
	const css = readFileSync('src/app.css', 'utf8');
	const [, sat, light] = avatar.match(/--tint: hsl\(var\(--hue\) (\d+)% (\d+)%\)/) ?? [];
	if (!sat) throw new Error('the tint is no longer an hsl of the hue');
	const found = css.match(
		/--band-scrim: color-mix\(in srgb, var\(--sift-bg\) (\d+)%, transparent\)/
	);
	if (!found) throw new Error('the page scrim is no longer a mix of the canvas');
	const scrim = Number(found[1]) / 100;
	const grounds = [...css.matchAll(/--p-bg: (#[0-9a-f]{6})/gi)].map((m) => parseColour(m[1]));
	expect(grounds).toHaveLength(4);

	const grey = (level: number): Rgba => ({ r: level / 255, g: level / 255, b: level / 255, a: 1 });
	const hsl = (hue: number): Rgba => {
		const s = Number(sat) / 100;
		const l = Number(light) / 100;
		const k = (n: number) => (n + hue / 30) % 12;
		const a = s * Math.min(l, 1 - l);
		const f = (n: number) => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
		return { r: f(0), g: f(8), b: f(4), a: 1 };
	};

	const worst = { white: Infinity, lightEdge: Infinity, black: Infinity };
	for (const bg of grounds) {
		for (let hue = 0; hue < 360; hue++) {
			const thin = composite({ ...bg, a: scrim }, hsl(hue));
			const on = (mark: Rgba) =>
				Math.min(
					contrastRatio(mark, thin),
					contrastRatio(mark, composite({ ...bg, a: scrim }, mark))
				);
			worst.white = Math.min(worst.white, on(grey(255)));
			worst.lightEdge = Math.min(worst.lightEdge, on(grey(190)));
			worst.black = Math.min(worst.black, on(grey(0)));
		}
	}
	expect(worst.white).toBeGreaterThanOrEqual(4.5);
	expect(worst.lightEdge).toBeGreaterThanOrEqual(3);
	// The floor is the accepted value (1.025:1, on obsidian, the darkest ground; 1.057:1 on
	// midnight), not a standard it meets. White reads 11.03:1 and the light edge 6.94:1, both on
	// chrome.
	expect(worst.black).toBeGreaterThanOrEqual(1.025);
});

/*
 * A bare mark: in a row listing sites, the face box's ground would read as a plate behind every
 * logo, since most of the pack is a disc or shape on a transparent square. Bare, the logo stands on
 * nothing; the letter keeps its tile.
 */
it('draws a bare mark on no ground, and keeps the tile for the letter', () => {
	const root = redraw({ src: '/api/sites/icons/for?host=x.test', mark: true, bare: true });
	expect(root.classList.contains('bare')).toBe(true);

	const text = source();
	const rule = text.slice(text.indexOf('.avatar.bare {'));
	const body = rule.slice(0, rule.indexOf('}'));
	expect(body).toContain('background: none');
	// Nothing clipped: the holder's radius would otherwise take the corners off the logo.
	expect(body).toContain('overflow: visible');
	// The letter is drawn on its own tint whatever the box does, and takes the corner itself.
	const letter = text.slice(text.indexOf('.avatar.bare .monogram {'));
	expect(letter.slice(0, letter.indexOf('}'))).toContain('border-radius: inherit');
	const monogram = text.slice(text.indexOf('\t.monogram {'));
	expect(monogram.slice(0, monogram.indexOf('}'))).toContain('background: var(--tint)');
});

it('is never bare off a face mark: a portrait keeps its ground, a still is not a mark', () => {
	const bare = (props: Record<string, unknown>) => redraw(props).classList.contains('bare');
	expect(bare({ src: '/api/sites/x/cover', shape: 'portrait', mark: true, bare: true })).toBe(
		false
	);
	expect(bare({ src: '/api/assets/x/thumb', bare: true })).toBe(false);
	expect(bare({ src: '/api/sites/x/cover', mark: true })).toBe(false);
});

/*
 * The letter is the size of a mark. A mark on a portrait is a `30cqi` square; the letter's capital is
 * made that tall by sizing the face by its capital rather than by the em, which is taller than any
 * capital. Read off the stylesheet: jsdom lays nothing out.
 */
it("sizes the letter by its capital, so it is as tall as a site's mark beside it", () => {
	const css = readFileSync('src/lib/components/common/Avatar.svelte', 'utf8');
	const rule = css.match(/\n\t\.monogram \{([^}]*)\}/);
	expect(rule, 'the letter has no rule of its own').not.toBeNull();
	expect(rule?.[1]).toContain('font-size: 30cqi;');
	expect(rule?.[1]).toContain('font-size-adjust: cap-height 1;');
	expect(css).toMatch(/\.avatar\.portrait \.picture\.mark \{[^}]*inline-size: 30cqi;/);
});

it("draws a glyph in the letter's place where it is given one and nothing loads", () => {
	/* A song with no cover: the music glyph on the name's tint, never the song's first letter. */
	const root = draw({ name: 'Lantern Hum', glyph: 'music_note_2' });
	const monogram = root.querySelector('.monogram');
	expect(monogram?.querySelector('.icon')).not.toBeNull();
	expect(monogram?.textContent).not.toContain('L');
});

it('draws the letter where no glyph is given', () => {
	const root = draw({ name: 'Lantern Hum' });
	expect(root.querySelector('.monogram .icon')).toBeNull();
	expect(root.querySelector('.monogram')?.textContent?.trim()).toBe('L');
});

/*
 * A picture on its way is a blank box, never the letter. The letter says nobody chose a picture,
 * so drawn while a cover loads it would read as a person with no cover until the row was drawn again.
 * The browser's loader is stood in for, because the test document never loads a picture.
 */
class Answering {
	static answers: Record<string, 'load' | 'error'> = {};
	onload: (() => void) | null = null;
	onerror: (() => void) | null = null;
	set src(address: string) {
		const said = Answering.answers[address];
		if (said) queueMicrotask(() => (said === 'load' ? this.onload?.() : this.onerror?.()));
	}
}

async function settled(): Promise<void> {
	for (let turn = 0; turn < 3; turn += 1) {
		await new Promise((done) => setTimeout(done, 0));
		flushSync();
	}
}

function answering(answers: Record<string, 'load' | 'error'>): void {
	Answering.answers = answers;
	vi.stubGlobal('Image', Answering);
}

afterEach(() => {
	vi.unstubAllGlobals();
});

it('keeps the letter back while a picture is on its way', async () => {
	answering({});
	const root = draw({ name: 'Lantern Hum', src: '/cover/one' });
	await settled();
	expect(root.querySelector('.monogram')?.classList.contains('waiting')).toBe(true);
});

it('draws the letter at once where there is no picture to wait for', () => {
	const root = draw({ name: 'Lantern Hum' });
	expect(root.querySelector('.monogram')?.classList.contains('waiting')).toBe(false);
});

it('waits on the second address while it loads after the first failed', async () => {
	answering({ '/cover/one': 'error' });
	draw({ name: 'Lantern Hum', src: '/cover/one', instead: '/thumb/one' });
	await settled();
	const root = host.querySelector<HTMLElement>('.avatar');
	expect(root?.querySelector('img')?.getAttribute('src')).toBe('/thumb/one');
	expect(root?.querySelector('.monogram')?.classList.contains('waiting')).toBe(true);
});

it('draws the letter once every address has failed', async () => {
	answering({ '/cover/one': 'error', '/thumb/one': 'error' });
	draw({ name: 'Lantern Hum', src: '/cover/one', instead: '/thumb/one' });
	await settled();
	const root = host.querySelector<HTMLElement>('.avatar');
	expect(root?.querySelector('.monogram')?.classList.contains('waiting')).toBe(false);
});
