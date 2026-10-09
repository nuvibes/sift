/*
 * What a Sites wall draws when nobody has chosen a cover.
 *
 * Most sites have no cover of their own, so the wall is mostly the picture Sift ships for each
 * site: a favicon, 128 pixels square, drawn to its own edges. That is a mark, not a photograph, and
 * the card has to say so, or it is cropped to the card's shape and stretched across its width.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { readFileSync } from 'node:fs';

import EntityCard from './EntityCard.svelte';
import cardSource from './EntityCard.svelte?raw';
import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import { creatorArt } from '$lib/entity/creator-art.svelte';
import Icon from '$lib/components/Icon.svelte';
import { cardCells } from './entity-counts';

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

function draw(props: Record<string, unknown>): void {
	drawn = mount(EntityCard, {
		target: host,
		props: { href: '/sites/01ABC', name: 'Quillhouse', ...props }
	}) as Record<string, unknown>;
	flushSync();
}

it('draws the shipped pack picture as a mark rather than as a photograph of the site', () => {
	draw({ siteName: 'Quillhouse', siteIcon: true });
	expect(host.querySelector('img.picture')?.classList.contains('mark')).toBe(true);
});

it("draws a Site's chosen cover as a mark too, at the size the pack's logo is drawn", () => {
	draw({ siteName: 'Quillhouse', siteIcon: true, coverAssetId: '01DEF' });
	expect(host.querySelector('img.picture')?.classList.contains('mark')).toBe(true);
});

/*
 * The pack logo's address names the logo: the row's own token, a dot, and the logo's token
 * (`SiteView.icon`). That exact address is the only one the server lets the browser keep for a week
 * (`kernel/covers.py names_the_shipped`); a card that forgot the token would re-ask every logo on
 * every visit to the Sites wall.
 */
it("puts the logo's token on the pack picture's address, after the row's own", () => {
	draw({ siteName: 'Quillhouse', siteIcon: '0.1.171-abcdef0123456789', art: 'f7' });
	expect(host.querySelector('img.picture')?.getAttribute('src')).toBe(
		'/api/sites/01ABC/cover?v=f7.0.1.171-abcdef0123456789'
	);
});

it('lets a chosen cover name itself rather than the logo it replaced', () => {
	draw({
		siteName: 'Quillhouse',
		siteIcon: '0.1.171-abcdef0123456789',
		art: 'f7',
		coverAssetId: '01DEF'
	});
	expect(host.querySelector('img.picture')?.getAttribute('src')).toBe(
		'/api/sites/01ABC/cover?v=f7.01DEF'
	);
});

it("draws a person's chosen cover as the frame it is, cropped to the card", () => {
	draw({ creatorName: 'Bryn Calloway', coverAssetId: '01DEF' });
	expect(host.querySelector('img.picture')?.classList.contains('mark')).toBe(false);
});

/*
 * One card shape on every wall: a tag's card is the person's card. The picture's box is the one
 * aspect ratio the card's stylesheet declares (the avatar inside it is `auto`, sized by the box), so
 * a second shape cannot come back as a class on some walls without failing here.
 */
it('draws every card at one shape, 2:3, whatever it is a card of', () => {
	const shapes = [...cardSource.matchAll(/^\s*aspect-ratio:\s*([^;]+);/gm)].map((one) => one[1]);
	expect(shapes.filter((one) => one !== 'auto')).toEqual(['2 / 3']);
	draw({});
	expect(host.querySelector('a.face')?.className).not.toMatch(/landscape/);
});

/*
 * The site marks, over the bottom of the cover, drawn even when the cover is a chosen photograph,
 * so a chosen cover never takes the picture people recognise a Site by off the wall.
 */
it('draws a site mark over the cover', () => {
	draw({ coverAssetId: '01DEF', sites: [{ name: 'Quillhouse', src: '/api/site-art/quillhouse' }] });
	const marks = [...host.querySelectorAll('img.site-mark')].map((one) => one.getAttribute('src'));
	expect(marks).toEqual(['/api/site-art/quillhouse']);
});

it('draws no mark row at all when nothing knows a site', () => {
	draw({ coverAssetId: '01DEF' });
	expect(host.querySelector('.sites')).toBeNull();
});

/*
 * The heart in the top-left corner, the rating in the top-right, both outside the anchor (a button
 * inside a link navigates on its way to the button), and no hover corner of other controls at all.
 */
it('puts the heart in the top-left corner and the rating in the top-right, outside the link', () => {
	draw({ favorite: false, rating: null });
	const heart = host.querySelector('.heart-corner button');
	const rating = host.querySelector('.rating-corner button');
	expect(heart).not.toBeNull();
	expect(rating).not.toBeNull();
	expect(heart?.closest('a')).toBeNull();
	expect(rating?.closest('a')).toBeNull();
	expect(host.querySelector('.corner')).toBeNull();
});

it('draws no opinion corners for a thing no opinion can be held about', () => {
	draw({});
	expect(host.querySelector('.judge')).toBeNull();
	expect(host.querySelector('.scrim.held')).toBeNull();
	expect(host.querySelector('.scrim.holds')).toBeNull();
});

/*
 * Both corners wait for the hover, except an opinion that is actually held, which stays on show.
 * `.set` is the class that keeps a corner up at rest.
 */
it('keeps an empty heart and an unrated chip for the hover, and nothing on the scrim for them', () => {
	draw({ favorite: false, rating: null });
	expect(host.querySelector('.heart-corner')?.classList.contains('set')).toBe(false);
	expect(host.querySelector('.rating-corner')?.classList.contains('set')).toBe(false);
	expect(host.querySelector('.scrim')?.classList.contains('holds')).toBe(true);
	expect(host.querySelector('.scrim')?.classList.contains('held')).toBe(false);
});

it('keeps a set heart and a rating with stars on show at rest', () => {
	draw({ favorite: true, rating: 4 });
	expect(host.querySelector('.heart-corner')?.classList.contains('set')).toBe(true);
	expect(host.querySelector('.rating-corner')?.classList.contains('set')).toBe(true);
	expect(host.querySelector('.scrim')?.classList.contains('held')).toBe(true);
});

it('keeps only the corner whose opinion is held', () => {
	draw({ favorite: false, rating: 2 });
	expect(host.querySelector('.heart-corner')?.classList.contains('set')).toBe(false);
	expect(host.querySelector('.rating-corner')?.classList.contains('set')).toBe(true);
});

/*
 * jsdom has no hover and computes no cascade, so the rule itself is what is held: hidden at rest,
 * shown by `.set`, the card's hover and a keyboard focus inside it, always shown where there is no
 * hover, and no fade under reduced motion. Each is a line somebody could delete with every test
 * above green.
 *
 * Keyboard focus and not `:focus-within`: pressing the heart leaves focus on its button, and
 * `:focus-within` would hold the unrated star on show after the pointer had gone.
 */
it('hides the corners at rest and shows them on hover, on keyboard focus, and where there is no hover', () => {
	const source = readFileSync('src/lib/components/entity/EntityCard.svelte', 'utf8');
	const style = source.slice(source.indexOf('<style>'));
	expect(style).toMatch(/\.judge \{\s*opacity: 0;/);
	expect(style).toMatch(
		/\.judge\.set,\s*\.card:hover \.judge,\s*\.card:has\(:focus-visible\) \.judge \{\s*opacity: 1;/
	);
	// No rule may show a corner merely because focus is somewhere in the card.
	expect(style).not.toMatch(/:focus-within/);
	expect(style).toMatch(/@media \(hover: none\) \{\s*\.judge \{\s*opacity: 1;/);
	expect(style).toMatch(/\[data-motion='reduce'\]\) \.judge \{\s*transition: none;/);
});

/* PICKING, on an entity page's tabs: the picture becomes a toggle and the name stays the link. */
it('draws the picture as the link it always was when nothing picks', () => {
	draw({});
	expect(host.querySelector('a.face')).not.toBeNull();
	expect(host.querySelector('button.face')).toBeNull();
	expect(host.querySelector('.pick')).toBeNull();
});

it('draws the picture as a pick toggle, and the name as the link, when the wall picks', () => {
	const onpick = vi.fn();
	draw({ onpick, picked: false });
	const face = host.querySelector('button.face') as HTMLButtonElement;
	expect(face).not.toBeNull();
	expect(face.getAttribute('aria-pressed')).toBe('false');
	expect(host.querySelector('a.face')).toBeNull();
	expect(host.querySelector('a.name')?.getAttribute('href')).toBe('/sites/01ABC');
	face.click();
	expect(onpick).toHaveBeenCalledTimes(1);
	expect(host.querySelector('.pick')).toBeNull();
});

it("hands a press on the card's ground to its picture: the link, or the pick", () => {
	draw({});
	const heard = vi.fn((event: Event) => event.preventDefault());
	host.querySelector('a.face')?.addEventListener('click', heard);
	host.querySelector<HTMLElement>('.body')?.click();
	expect(heard).toHaveBeenCalledTimes(1);
	unmount(drawn!);
	const onpick = vi.fn();
	draw({ onpick, picked: false });
	host.querySelector<HTMLElement>('.body')?.click();
	expect(onpick).toHaveBeenCalledTimes(1);
});

it('lays the one pick look over the picture of a picked card and says so out loud', () => {
	draw({ onpick: () => {}, picked: true });
	expect(host.querySelector('button.face')?.getAttribute('aria-pressed')).toBe('true');
	// Inside the picture, the same look a media tile wears, with the funnel for a filter pick.
	const pick = host.querySelector('button.face .pick') as HTMLElement | null;
	expect(pick?.dataset.purpose).toBe('filter');
	expect(pick?.getAttribute('aria-hidden')).toBe('true');
});

it('in swap mode picks the card for the swap, with the swap mark, instead of filtering', async () => {
	const { swapMode } = await import('$lib/swap/mode.svelte');
	const onpick = vi.fn();
	swapMode.enter();
	try {
		draw({ onpick, picked: false, swapAs: { kind: 'person', id: 'p-ava' } });
		const face = host.querySelector('button.face') as HTMLButtonElement;
		expect(face.getAttribute('aria-label')).toBe('Offer Quillhouse in the swap');
		face.click();
		flushSync();
		expect(onpick).not.toHaveBeenCalled();
		expect(swapMode.has('person', 'p-ava')).toBe(true);
		const pick = host.querySelector('button.face .pick') as HTMLElement | null;
		expect(pick?.dataset.purpose).toBe('swap');
	} finally {
		swapMode.leave();
	}
	flushSync();
	// Out of the mode the card is the wall's again: its own pick, its own filter.
});

it('draws no pick look on a card that is not picked', () => {
	draw({ onpick: () => {}, picked: false });
	expect(host.querySelector('.pick')).toBeNull();
});

it('draws the count cells it is handed as links, each named by its tab', () => {
	draw({
		counts: [
			{
				id: 'photo_sets',
				label: 'Photo Sets',
				icon: 'photo_library',
				href: '/x?show=photo_sets',
				count: 3
			}
		]
	});
	const cell = host.querySelector<HTMLAnchorElement>('nav.counts a.count');
	expect(cell?.getAttribute('href')).toBe('/x?show=photo_sets');
	expect(cell?.getAttribute('aria-label')).toBe('Photo Sets: 3');
});

it('keeps the count row, empty, when it is handed no cells, so every card on a wall is one height', () => {
	draw({});
	const nav = host.querySelector('nav.counts');
	expect(nav).not.toBeNull();
	expect(nav?.querySelectorAll('[data-cell]')).toHaveLength(0);
});

/*
 * The count row's first glyph starts where the words above it start. Each cell keeps its padding
 * (the hover ground and the hit area) and the row is pulled out by it, so the rule itself is what
 * is held: jsdom lays nothing out.
 */
it('pulls the count row out by exactly the padding its cells carry', () => {
	const source = readFileSync('src/lib/components/entity/EntityCounts.svelte', 'utf8');
	const style = source.slice(source.indexOf('<style>'));
	expect(style).toMatch(/\.counts \{[^}]*margin-inline-start: calc\(-1 \* var\(--space-2\)\);/);
	expect(style).toMatch(/\.count \{[^}]*padding: var\(--space-1\) var\(--space-2\);/);
});

/* The address names WHICH cover it is, because that is the only address the server lets the
   browser keep (`kernel/covers.py names_its_cover`). A chosen moment the card was not told about
   would leave the address naming the whole file, and the picture would be re-checked on every
   visit. */
it("puts the chosen moment and the row's token on the cover's address", () => {
	draw({ coverAssetId: '01DEF', coverAtMs: 4200, art: 'stamp' });
	expect(host.querySelector('img.picture')?.getAttribute('src')).toBe(
		'/api/sites/01ABC/cover?v=stamp.01DEF.4200'
	);
});

it("puts the row's token on an uploaded cover's address", () => {
	draw({ coverUploadId: '01UPL', art: 'stamp' });
	expect(host.querySelector('img.picture')?.getAttribute('src')).toBe(
		'/api/sites/01ABC/cover?v=stamp.01UPL'
	);
});

/*
 * The shipped logo stands on the same blurred wash as every other mark: nothing about its colour
 * reaches the tile (one ground for every mark; see `Avatar.svelte`).
 */
it('draws the shipped logo on the blurred wash every mark stands on', () => {
	draw({ siteName: 'Quillhouse', siteIcon: true });
	const root = host.querySelector('.avatar');
	expect(root?.classList.contains('mark')).toBe(true);
	expect(root?.querySelector('img.ground')).not.toBeNull();
});

/*
 * The row of numbers is one line on a card, at the smallest size a card comes in.
 *
 * jsdom lays nothing out, so the three sizes the row reads are handed to it: the row is the
 * smallest card's (200, less 12 of padding a side, plus the 8 it is pulled out by), a cell is 45,
 * and the "+n" is
 * 30. Five cells need 225; what is asserted is that the card draws the three that fit, a "+2"
 *     holding the other two, and a row that does not wrap.
 */
describe('the row of numbers on the smallest card', () => {
	const realRect = Element.prototype.getBoundingClientRect;
	const realClientWidth = Object.getOwnPropertyDescriptor(Element.prototype, 'clientWidth');
	const realObserver = globalThis.ResizeObserver;

	beforeEach(() => {
		Object.defineProperty(Element.prototype, 'clientWidth', {
			configurable: true,
			get(this: Element) {
				return this.classList.contains('counts') ? 200 - 2 * 12 + 8 : 0;
			}
		});
		Element.prototype.getBoundingClientRect = function rect(this: Element) {
			if (this.hasAttribute('data-cell')) return { width: 45 } as DOMRect;
			if (this.hasAttribute('data-more')) return { width: 30 } as DOMRect;
			return realRect.call(this);
		};
		globalThis.ResizeObserver = class {
			observe(): void {}
			unobserve(): void {}
			disconnect(): void {}
		} as unknown as typeof ResizeObserver;
	});

	afterEach(() => {
		Element.prototype.getBoundingClientRect = realRect;
		if (realClientWidth) Object.defineProperty(Element.prototype, 'clientWidth', realClientWidth);
		globalThis.ResizeObserver = realObserver;
	});

	const FIVE = cardCells('person', 'p1', '/people/p1', {
		sites: 3,
		collections: 2,
		photo_sets: 4,
		loops: 1,
		tags: 12
	});

	it('is handed five cells, so the fold is a real one', () => {
		expect(FIVE).toHaveLength(5);
	});

	it('draws the three that fit and folds the other two into "+2", on one line', () => {
		draw({ href: '/people/p1', name: 'Bryn Calloway', counts: FIVE });

		const row = host.querySelector('nav.counts');
		expect(row?.classList.contains('fitted')).toBe(true);
		const standing = [...host.querySelectorAll('[data-cell]:not(.aside)')].map((one) =>
			one.getAttribute('data-cell')
		);
		expect(standing).toEqual(FIVE.slice(0, 3).map((one) => one.id));

		const more = host.querySelector('[data-more]');
		expect(more?.classList.contains('aside')).toBe(false);
		expect(more?.textContent?.trim()).toBe('+2');
		expect(more?.querySelector('a')?.getAttribute('href')).toBe('/people/p1');
		expect(more?.querySelector('a')?.getAttribute('aria-label')).toBe(
			`+2 more: ${FIVE.slice(3)
				.map((one) => `${one.label}: ${one.count}`)
				.join(', ')}`
		);
	});
});

/* A LOCKED TILE: a row whose every file the viewer may see is Hidden, with Hidden shut and "leave a
   locked tile" on. The server sends it with no name (`_LOCKED_TILE`); what is held here is that the
   card draws the locked file tile's picture, says no name, leads nowhere near the row's page, keeps
   its counts, and asks for the PIN when pressed. */
describe('a locked tile', () => {
	const cells = [
		{
			id: 'tags',
			label: 'Tags',
			icon: 'sell',
			href: '/sites/01ABC?show=tags',
			count: 3
		}
	];

	it('draws the Hidden glyph where the picture goes and no name', () => {
		draw({ locked: true, name: 'Quillhouse', siteName: 'Quillhouse', siteIcon: true });
		/* The locked FILE tile's glyph (`Tile.svelte`), drawn here by the same Icon. */
		const glyph = document.createElement('span');
		const icon = mount(Icon, { target: glyph, props: { name: 'visibility_off', filled: true } });
		flushSync();
		expect(host.querySelector('.withheld .icon')?.textContent).toBe(glyph.textContent);
		/* Never the `veil` class: that is the dialog's sheet, fixed to the window on the dialog's
		   layer, and a card face wearing it would rise over the settings panel and file view. */
		expect(host.querySelector('.veil')).toBeNull();
		unmount(icon);
		expect(host.textContent).not.toContain('Quillhouse');
		expect(host.querySelector('img')).toBeNull();
		expect(host.querySelector('.name')?.textContent).toBe('Hidden');
	});

	it("leads nowhere near the row's page, and keeps its counts where nothing can press them", () => {
		draw({ locked: true, name: '', detail: '1 file', counts: cells });
		const reachable = [...host.querySelectorAll('a[href]')].filter(
			(one) => !one.closest('[inert]')
		);
		expect(reachable).toEqual([]);
		expect(host.querySelector('[inert] nav.counts .figure')?.textContent).toBe('3');
		expect(host.textContent).toContain('1 file');
	});

	it('asks for the PIN when the picture is pressed', async () => {
		const { vaultPrompt } = await import('$lib/shell/vault.svelte');
		const ask = vi.spyOn(vaultPrompt, 'ask').mockImplementation(() => {});
		draw({ locked: true, name: '' });
		host.querySelector<HTMLButtonElement>('button[aria-label^="Hidden"]')?.click();
		expect(ask).toHaveBeenCalledOnce();
		ask.mockRestore();
	});

	it('draws the ordinary card when the row is not locked', () => {
		draw({ locked: false });
		expect(host.querySelector('.withheld')).toBeNull();
		expect(host.querySelector('a.name')?.textContent).toBe('Quillhouse');
	});
});

/*
 * Selected on a card is the tile's treatment: the ring, and a tick in the corner, so it is never
 * only a colour and never the hover's lift.
 */
it('wears the selected ring and a tick when it is selected, and neither when it is not', () => {
	draw({ selected: true, onclickcapture: () => {}, id: '01ABC' });
	const card = host.querySelector('.card') as HTMLElement;
	applyStyles(cardSource, card);

	expect(getComputedStyle(card).outline).toContain('var(--selected-ring-width)');
	expect(card.querySelector('.selected-check')).not.toBeNull();
	removeStyles();

	unmount(drawn as Record<string, unknown>);
	drawn = null;
	draw({ selected: false });
	expect(host.querySelector('.selected-check')).toBeNull();
});

/*
 * The picture a site shows a creator with is that site's small profile picture, so it is drawn the
 * way a Site's mark is: whole, at the mark's size, on its own blurred ground. The letter a creator
 * with no picture gets is the same size (see `Avatar`), so a site's wall of creators has one shape.
 */
it("draws a creator's picture from the site as a mark, the way a Site's own picture is drawn", () => {
	const has = vi.spyOn(creatorArt, 'has').mockReturnValue(true);
	try {
		draw({ creatorName: 'Quillhouse' });
		const picture = host.querySelector('img.picture');
		expect(picture?.getAttribute('src')).toBe('/api/creator-art/Quillhouse');
		expect(picture?.classList.contains('mark')).toBe(true);
	} finally {
		has.mockRestore();
	}
});

it("still crops a person's chosen cover to the card, which is a frame and not a mark", () => {
	const has = vi.spyOn(creatorArt, 'has').mockReturnValue(true);
	try {
		draw({ creatorName: 'Quillhouse', coverAssetId: '01DEF' });
		expect(host.querySelector('img.picture')?.classList.contains('mark')).toBe(false);
	} finally {
		has.mockRestore();
	}
});

/*
 * A network wears its Sites within beside its files, in words, and never as a cell in the row
 * under them: the row says what the files carry, the mark says what the Site is.
 */
it('draws a Site with Sites within as a network mark beside the files, not as a cell', () => {
	draw({
		detail: '2 files',
		counts: cardCells('site', '01ABC', '/sites/01ABC', { sites_within: 13, people: 7 })
	});
	expect(host.querySelector('.under')?.textContent).toContain('13 Sites within');
	const cells = [...host.querySelectorAll('nav.counts [data-cell]')].map((one) =>
		one.getAttribute('data-cell')
	);
	expect(cells).not.toContain('sites_within');
	expect(cells).toContain('people');
});

it('draws no network mark on a Site nothing is part of', () => {
	draw({ detail: '2 files', counts: cardCells('site', '01ABC', '/sites/01ABC', { people: 7 }) });
	expect(host.textContent).not.toContain('within');
});

it('says one Site within in the singular', () => {
	draw({ counts: cardCells('site', '01ABC', '/sites/01ABC', { sites_within: 1 }) });
	expect(host.querySelector('.under')?.textContent).toContain('1 Site within');
});

/*
 * A card's name on a phone keeps its size and reaches a finger above and below its line. A ring
 * drawn by `::after` would be clipped by the name's own overflow (kept for the ellipsis), so a
 * press just above or below the line would land on the card, not the link. Padding given back as
 * margin is inside the name's own box, so its clip cannot take it.
 */
describe("a card's name on a phone", () => {
	it('reaches a finger through padding given back as margin, never a ring its clip would cut', () => {
		const phone = cardSource.slice(cardSource.lastIndexOf('@media (max-width: 767px)'));
		expect(phone).toMatch(
			/a\.name \{\s*padding-block: calc\(\(var\(--touch-target\) - 1lh\) \/ 2\);\s*margin-block: calc\(\(1lh - var\(--touch-target\)\) \/ 2\);/
		);
		expect(cardSource).not.toMatch(/\.name::after/);
	});
});

/* The picture fills the face and is positioned, so the browser paints it over anything the face
   itself draws, an outline included: the ring has to be a layer above the picture. */
it("draws the face's focus ring as a layer over the picture, not on the face under it", () => {
	const rules = [...cardSource.matchAll(/([^{}]*\.face:focus-visible[^{}]*)\{([^}]*)\}/g)];
	const onFace = rules.filter(([, selector]) => !selector.includes('::after'));
	const layer = rules.filter(([, selector]) => selector.includes('::after'));
	expect(onFace.every(([, , body]) => !/outline:\s*var/.test(body))).toBe(true);
	expect(layer).toHaveLength(1);
	const body = layer[0][2];
	expect(body).toMatch(/position:\s*absolute/);
	expect(body).toMatch(/z-index:\s*1/);
	expect(body).toMatch(/box-shadow:\s*var\(--focus-ring\)/);
});
