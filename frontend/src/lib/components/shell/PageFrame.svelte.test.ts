import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { afterEach, describe, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { createRawSnippet } from 'svelte';

import PageFrame from './PageFrame.svelte';
import { phoneWidth } from '$lib/components/common/phone-width.svelte';
import { pageScroll } from './page-scroll';
import { pageTrail } from './trail.svelte';

const HERE = dirname(fileURLToPath(import.meta.url));

/* The one wire that no test of `page-scroll.ts` can see: that a frame actually says which box it is.
 *
 * Every screen in Sift scrolls inside this frame and nothing else scrolls, so if this effect is
 * removed the position memory goes quiet: it answers nought for every screen, which reads exactly
 * like a person having been at the top. Nothing else in the suite would notice.
 */

const body = createRawSnippet(() => ({ render: () => '<p>a screen</p>' }));

let host: HTMLElement | null = null;
let frame: Record<string, unknown> | null = null;

afterEach(() => {
	if (frame) unmount(frame);
	frame = null;
	host?.remove();
	host = null;
});

function draw(props: Record<string, unknown> = {}) {
	host = document.createElement('div');
	document.body.appendChild(host);
	frame = mount(PageFrame, { target: host, props: { children: body, ...props } });
	flushSync();
	return host.querySelector('.frame-body') as HTMLElement;
}

describe('a frame and the position memory', () => {
	it('says which box a screen scrolls in', () => {
		const scroller = draw();
		expect(scroller).not.toBeNull();
		Object.defineProperty(scroller, 'scrollTop', { value: 406, configurable: true });

		expect(pageScroll.capture()).toBe(406);
	});

	it('stops saying so when the screen goes', () => {
		const scroller = draw();
		Object.defineProperty(scroller, 'scrollTop', { value: 406, configurable: true });

		unmount(frame!);
		frame = null;

		expect(pageScroll.capture()).toBe(0);
	});
});

describe('the trail band', () => {
	/*
	 * On a desk the frame draws no trail of its own: it says the trail to the top bar, which draws
	 * it on the search box's line, so no band pushes a trailed screen's header below an untrailed
	 * one's. On a phone the bar has no room for it and the frame draws it as the page's first line.
	 * One crumb is where you are, not a way back: shown nowhere.
	 */
	const trail = [{ label: 'People', href: '/people' }, { label: 'Somebody' }];

	afterEach(() => {
		phoneWidth.yes = false;
	});

	it('says the trail to the top bar on a desk, and draws no band above the header', () => {
		draw({ crumbs: trail });

		expect(host!.querySelector('.frame-trail')).toBeNull();
		expect(host!.querySelector('nav[aria-label="Breadcrumb"]')).toBeNull();
		expect(host!.querySelector('.frame')?.classList.contains('trailed')).toBe(false);
		expect(pageTrail.crumbs.map((crumb) => crumb.label)).toEqual(['People', 'Somebody']);
	});

	it('takes the trail back when the screen goes, and only its own', () => {
		draw({ crumbs: trail });
		unmount(frame!);
		frame = null;
		expect(pageTrail.crumbs).toEqual([]);

		/* A newer screen's trail outlives the older screen going away after it arrived. */
		const newer = Symbol('newer');
		pageTrail.say(newer, [{ label: 'Tags', href: '/tags' }, { label: 'A tag' }]);
		draw({ crumbs: trail });
		pageTrail.say(newer, [{ label: 'Tags', href: '/tags' }, { label: 'A tag' }]);
		unmount(frame!);
		frame = null;
		expect(pageTrail.crumbs.map((crumb) => crumb.label)).toEqual(['Tags', 'A tag']);
		pageTrail.unsay(newer);
	});

	it('draws the trail as the first line of the page on a phone, above the header', () => {
		phoneWidth.yes = true;
		draw({ crumbs: trail });

		const band = host!.querySelector('.frame-trail');
		expect(band).not.toBeNull();
		expect(band!.querySelector('nav[aria-label="Breadcrumb"]')?.textContent).toContain('People');
		expect(host!.querySelector('.frame')?.classList.contains('trailed')).toBe(true);
		expect(pageTrail.crumbs).toEqual([]);
	});

	it('shows one crumb nowhere', () => {
		draw({ crumbs: [{ label: 'People' }] });
		expect(host!.querySelector('.frame-trail')).toBeNull();
		expect(pageTrail.crumbs).toEqual([]);

		unmount(frame!);
		frame = null;
		host?.remove();
		phoneWidth.yes = true;
		draw({ crumbs: [{ label: 'People' }] });
		expect(host!.querySelector('.frame-trail')).toBeNull();
	});
});

/*
 * Every screen's first content on one line, Theater's. jsdom lays nothing out, so what is held is
 * the rule's text: on a desk the header's top inset is the page's own inset and nothing else, and
 * Theater (a screen with no frame) insets its title by the same amount. The browser measurement is
 * `e2e/page-alignment.spec.ts`.
 */
describe("every screen's first line", () => {
	const stylesheet = readFileSync(resolve(HERE, 'PageFrame.svelte'), 'utf8');
	const pageHeader = readFileSync(resolve(HERE, 'PageHeader.svelte'), 'utf8');
	const appCss = readFileSync(resolve(HERE, '../../../app.css'), 'utf8');

	it('is the inset of the page under the top bar on a desk, with a trail or without', () => {
		const header = stylesheet.match(/\n\t\.frame-header \{\n\t\tpadding-block-start: ([^;]*);/);
		expect(header, 'no desk rule for the header inset in PageFrame').not.toBeNull();
		expect(header![1]).toBe('var(--page-pad)');
		/* The trailed inset is a phone's alone: outside the phone's block, nothing moves a trailed
		   title. */
		const desk = stylesheet.slice(
			0,
			stylesheet.indexOf('@media (max-width: 767px) {\n\t\t.frame-header')
		);
		expect(desk).not.toMatch(/\.frame\.trailed \.frame-header/);
	});

	it('is the line the title of Theater stands on', () => {
		const inset = pageHeader.match(/\.page-header\.inset \{\n\t\tpadding: (\S+)/);
		const pad = appCss.match(/\n\t--page-pad: ([^;]*);/);
		expect(inset).not.toBeNull();
		expect(pad).not.toBeNull();
		expect(inset![1]).toBe(pad![1]);
	});
});

/*
 * The backdrop, and the two things about it jsdom cannot see: the layer covers every row above the
 * body, and the picture fills the band rather than sitting at its own width. jsdom computes no grid
 * and no `object-fit`, so what can be held is that the rules still say so, the same bargain
 * `contrast.test.ts` strikes for the ink lift it cannot measure. Read from the file, narrowly: the
 * rule's text, not its effect.
 */
describe('the picture a screen stands on', () => {
	const stylesheet = readFileSync(resolve(HERE, 'PageFrame.svelte'), 'utf8');

	it('spans the whole page, foot included, not the rows above the body alone', () => {
		const layer = stylesheet.match(/\n\t\.frame-backdrop\s*\{([^}]*)\}/);
		expect(layer, 'no .frame-backdrop block in PageFrame').not.toBeNull();

		/*
		 * First line to last, so the ground covers the whole page, and a row added to the frame
		 * later is under it too.
		 */
		expect(layer![1]).toContain('grid-row: 1 / -1;');
	});

	/*
	 * And edge to edge sideways: the ground is the page, so it has no margin and no corner; any put
	 * back would make it read as a card floating in the page. Written as an absence because that is
	 * what the rule is. jsdom computes no grid, so the file's own text is what is held, on the
	 * bargain described above.
	 */
	it('runs edge to edge rather than being inset like the furniture standing on it', () => {
		const layer = stylesheet.match(/\n\t\.frame-backdrop\s*\{([^}]*)\}/);
		expect(layer, 'no .frame-backdrop block in PageFrame').not.toBeNull();

		expect(layer![1], "the ground is the page's width, so it takes no margin").not.toMatch(
			/margin/
		);
		expect(layer![1], 'a ground with a corner is a card').not.toMatch(/radius/);
	});

	it('sizes the picture to the layer rather than letting it keep its own width', () => {
		const picture = stylesheet.match(/\n\t\.backdrop-picture\s*\{([^}]*)\}/);
		expect(picture, 'no .backdrop-picture block in PageFrame').not.toBeNull();

		/*
		 * An absolutely positioned `<img>` with `width: auto` takes its intrinsic width whatever
		 * its offsets say, so without these two it would sit at its own width against the left of a
		 * wider band. Both axes, written from the blur's own token so they cannot drift from the
		 * inset that opens the margin they fill.
		 */
		for (const axis of ['inline-size', 'block-size'])
			expect(picture![1]).toContain(`${axis}: calc(100% + 4 * var(--band-blur));`);
		expect(picture![1]).toContain('inset: calc(-2 * var(--band-blur));');

		// A portrait cover cropped to a wide band keeps its middle, which is where a subject is.
		expect(picture![1]).toContain('object-fit: cover;');
		expect(picture![1]).toContain('object-position: center;');
	});
});

describe("the trail band's height", () => {
	const stylesheet = readFileSync(resolve(HERE, 'PageFrame.svelte'), 'utf8');

	it('is a floor, so a trail that wraps onto a second line pushes the title down instead of being cut', () => {
		/* At 393 wide three crumbs wrap to two lines, and in a band of one fixed
		   height standing on its floor the first line goes up under the top bar, cut in half. */
		const band = stylesheet.match(/\n\t\.frame-trail\s*\{([^}]*)\}/);
		expect(band, 'no .frame-trail block in PageFrame').not.toBeNull();
		expect(band![1]).toContain('min-block-size: calc(var(--page-pad) + var(--space-3));');
		expect(band![1]).not.toMatch(/(^|[^-])block-size:/);
	});
});

describe('at a phone width the rows above the body scroll with it', () => {
	/*
	 * At a phone's size (393x659, 412x839) a person's page can come to more than the whole window
	 * above its body, so the body's track would be left nothing and the files under the tabs never
	 * drawn. On a phone the trail, the header and the tools are drawn at the top of the one
	 * scrolling box instead of in tracks of their own; on a desktop, and under a body that fills its
	 * box, they stay where they were.
	 */
	const header = createRawSnippet(() => ({ render: () => '<h1>A title</h1>' }));
	const tools = createRawSnippet(() => ({ render: () => '<nav>Tabs</nav>' }));
	const crumbs = [{ label: 'People', href: '/people' }, { label: 'Somebody' }];

	afterEach(() => {
		phoneWidth.yes = false;
	});

	it('draws them inside the scrolling box on a phone', () => {
		phoneWidth.yes = true;
		const scroller = draw({ header, tools, crumbs });
		for (const row of ['.frame-trail', '.frame-header', '.frame-tools'])
			expect(scroller.querySelector(row), `${row} is not in the scrolling box`).not.toBeNull();
		// Drawn once: the phone branch replaces the desktop's rows rather than copying them.
		expect(host!.querySelectorAll('.frame-header')).toHaveLength(1);
	});

	it('keeps them in their own tracks on a desktop', () => {
		const scroller = draw({ header, tools, crumbs });
		expect(scroller.querySelector('.frame-header')).toBeNull();
		expect(host!.querySelector('.frame > .frame-header')).not.toBeNull();
		expect(host!.querySelector('.frame > .frame-tools')).not.toBeNull();
	});

	it('keeps them in their own tracks over a body that fills its box, on a phone too', () => {
		phoneWidth.yes = true;
		const scroller = draw({ header, tools, crumbs, fillBody: true });
		expect(scroller.querySelector('.frame-header')).toBeNull();
		expect(host!.querySelector('.frame > .frame-header')).not.toBeNull();
	});
});
