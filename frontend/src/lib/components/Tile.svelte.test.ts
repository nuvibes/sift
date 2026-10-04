import { afterEach, describe, expect, it, vi } from 'vitest';
import { createRawSnippet, flushSync, mount, unmount } from 'svelte';
import Tile from './Tile.svelte';
import tileSource from './Tile.svelte?raw';
import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import { heldStates } from '$lib/design/testing-states';
import codepoints from '$lib/generated/icon-codepoints.json';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import {
	ALWAYS,
	HIDDEN_MARK,
	ON_HOVER,
	ON_HOVER_CLASS,
	tileMarks,
	VIEWS_MARK
} from '$lib/grid/tile-marks.svelte';

/* The tile, and the two things about it that other features depend on.
 *
 * It is shared: the player opens from it and the rating controls sit inside it, so its overlay
 * slot and its open callback are a contract rather than an implementation detail. They are tested
 * here because the features that fill them are built separately and will be built against these.
 *
 * The rest is about what a concealed tile does not say. That one is a privacy property, not a
 * styling one: a tile that draws a blurred thumbnail is a tile that was sent the thumbnail.
 */

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

afterEach(() => {
	if (mounted) unmount(mounted);
	mounted = null;
	host?.remove();
});

type TileProps = Parameters<typeof Tile>[1];

function render(props: TileProps) {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(Tile, { target: host, props }) as Record<string, unknown>;
	// Without this the handlers are not attached yet and a dispatched event lands on nothing.
	flushSync();
	return host.querySelector('.tile') as HTMLButtonElement;
}

const ITEM = { id: 'asset-1', media_type: 'video', duration_ms: 95_000 };

describe('the size it was given', () => {
	it('is applied as a real width, not silently dropped', () => {
		// The policy the app is served under refuses a style attribute written into markup, and
		// refuses it without saying so. A tile that set its width that way would come out the same
		// size as every other tile and the layout would look broken while every test stayed green,
		// so the width is set through the style property instead.
		const tile = render({ item: ITEM, width: 317, height: 200 });

		expect(tile.style.width).toBe('317px');
		expect(tile.style.height).toBe('200px');
	});
});

describe('opening it', () => {
	it('calls back with the id when clicked', () => {
		const onopen = vi.fn();
		const tile = render({ item: ITEM, width: 200, height: 200, onopen });

		tile.click();

		expect(onopen).toHaveBeenCalledWith('asset-1');
	});

	it('opens from the keyboard too', () => {
		const onopen = vi.fn();
		const tile = render({ item: ITEM, width: 200, height: 200, onopen });

		tile.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));

		expect(onopen).toHaveBeenCalledWith('asset-1');
	});

	it('is a real button, so it is reachable and announced without any of that being re-implemented', () => {
		const tile = render({ item: ITEM, width: 200, height: 200 });

		expect(tile.tagName).toBe('BUTTON');
	});
});

describe('what is drawn', () => {
	it('shows the still once there is one', () => {
		render({ item: ITEM, width: 200, height: 200, thumbSrc: '/api/assets/asset-1/thumb' });

		const image = host.querySelector('img');
		expect(image?.getAttribute('src')).toBe('/api/assets/asset-1/thumb');
	});

	it('shows a shimmer while the file is still being imported', () => {
		// The tile appears the moment a file is accepted, so the library never looks like it
		// swallowed something.
		render({ item: ITEM, width: 200, height: 200, importing: true });

		expect(host.querySelector('.shimmer')).not.toBeNull();
		expect(host.querySelector('img')).toBeNull();
	});

	it('plays the clip only when it holds a slot', () => {
		const props = {
			item: ITEM,
			width: 200,
			height: 200,
			thumbSrc: '/thumb',
			previewSrc: 'blob:clip',
			playing: false
		};
		render(props);
		expect(host.querySelector('video'), 'a tile without a slot spawned a decoder').toBeNull();

		render({ ...props, playing: true });
		expect(host.querySelector('video')).not.toBeNull();
	});

	it('says how long it is, in the face used for machine facts', () => {
		render({ item: ITEM, width: 200, height: 200, thumbSrc: '/thumb' });

		expect(host.querySelector('.duration')?.textContent).toBe('1:35');
	});

	it('says nothing about length when there is none to say', () => {
		render({ item: { id: 'a', media_type: 'image' }, width: 200, height: 200, thumbSrc: '/t' });

		expect(host.querySelector('.duration')).toBeNull();
	});

	it('refuses to time a photograph, even one the library thinks has a length', () => {
		// ffprobe reads a normally-sized jpeg as a one-frame video at 25 fps, so every image
		// carries a forty-millisecond duration that would round to "0:00". The metadata read does not
		// record one; this stops rows that already have one from drawing it.
		render({
			item: { id: 'a', media_type: 'image', duration_ms: 40 },
			width: 200,
			height: 200,
			thumbSrc: '/t'
		});

		expect(host.querySelector('.duration')).toBeNull();
	});

	it('names a GIF instead of timing it', () => {
		/* A length is the least useful thing anybody could be told about a GIF: it is two seconds
		 * long by definition, it loops, and there is nothing to seek with. The number would answer a
		 * question nobody asks and hide the one they do (clip, or loop?), which is the only thing
		 * that tells a GIF from the short video beside it at a glance. */
		render({
			item: { id: 'a', media_type: 'gif', duration_ms: 2040 },
			width: 200,
			height: 200,
			thumbSrc: '/t'
		});

		expect(host.querySelector('.duration')?.textContent).toBe('GIF');
	});

	it('says GIF even for one the library has no length for at all', () => {
		// The chip is about what the file IS, so it does not depend on the metadata read having run.
		render({
			item: { id: 'a', media_type: 'gif' },
			width: 200,
			height: 200,
			thumbSrc: '/t'
		});

		expect(host.querySelector('.duration')?.textContent).toBe('GIF');
	});
});

describe('the resume bar', () => {
	/* A thin line along the bottom edge, filled to where you stopped: the tile's whole answer to
	 * "what am I part-way through", given without anything being clicked.
	 *
	 * What every one of these is really about is that the TILE DECIDES NOTHING. Whether there is a
	 * place worth going back to depends on the file's length, on how far in it was stopped, and on
	 * two preferences the browser has never been told; that is settled on the server, by the same
	 * function the player starts from, and arrives here as one number or as nothing at all. So the
	 * bar is drawn exactly when `resume_ms` is present and never otherwise, and a tile that started
	 * applying a rule of its own would be a second copy of one. */

	it('is not drawn for a file with no saved position', () => {
		render({ item: ITEM, width: 200, height: 200, thumbSrc: '/t' });

		expect(host.querySelector('.progress')).toBeNull();
	});

	it('is drawn, filled to the saved position, when there is one', () => {
		render({
			item: { ...ITEM, resume_ms: 19_000 },
			width: 200,
			height: 200,
			thumbSrc: '/t'
		});

		const fill = host.querySelector('.progress-fill') as HTMLElement;
		expect(fill).not.toBeNull();
		// A fifth of the way in. Read off the element rather than out of the markup: the width is
		// set with a `style:` directive, which the app's own content policy requires: a style
		// ATTRIBUTE would be dropped in silence and every bar would come out empty.
		expect(fill.style.inlineSize).toBe('20%');
	});

	it('cannot draw past the end of its own track', () => {
		// A position saved before the file was re-probed shorter. Clamped rather than trusted,
		// because the alternative is a fill wider than the bar it sits in.
		render({
			item: { ...ITEM, resume_ms: 200_000 },
			width: 200,
			height: 200,
			thumbSrc: '/t'
		});

		expect((host.querySelector('.progress-fill') as HTMLElement).style.inlineSize).toBe('100%');
	});

	it('says nothing for a file whose length was never measured', () => {
		// There is no fraction to draw without one, and a bar filled to an assumed length is worse
		// than no bar: it looks like an answer.
		render({
			item: { id: 'a', media_type: 'video', resume_ms: 19_000 },
			width: 200,
			height: 200,
			thumbSrc: '/t'
		});

		expect(host.querySelector('.progress')).toBeNull();
	});
});

describe('a concealed tile', () => {
	const CONCEALED = { id: 'hidden-1', media_type: '', concealed: true };

	it('draws no picture at all', () => {
		// Not a blurred one. There is nothing to blur: the server sends no bytes for a concealed
		// item, and a tile that blurred a real thumbnail would be one CSS rule away from showing it.
		render({ item: CONCEALED, width: 200, height: 200, thumbSrc: '/api/assets/hidden-1/thumb' });

		expect(host.querySelector('img')).toBeNull();
		expect(host.querySelector('video')).toBeNull();
		expect(host.querySelector('.withheld')).not.toBeNull();
	});

	it('is the withheld face, with the glyph sharp on top of it', () => {
		/*
		 * The concealed tile wears the withheld face every Hidden thing wears: the ground, blurred,
		 * so the one deliberate state does not look like the two that are faults (no still, missing
		 * file). What is pinned is that the glyph is a child of that face, over the blurred layer
		 * rather than inside the filter. There is still no picture in it; see the test above, which
		 * is the privacy property.
		 */
		render({ item: CONCEALED, width: 200, height: 200 });

		const veil = host.querySelector('.withheld');
		expect(veil).not.toBeNull();
		expect(veil?.querySelector('.icon')).not.toBeNull();
		// And it is not the "nothing to draw" slab, which is a different state with a different
		// meaning: that one says the file has no still, this one refuses to say anything.
		expect(host.querySelector('.flat')).toBeNull();
	});

	it('cannot be opened', () => {
		const onopen = vi.fn();
		const tile = render({ item: CONCEALED, width: 200, height: 200, onopen });

		tile.click();
		tile.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));

		expect(onopen).not.toHaveBeenCalled();
	});

	it('says nothing about what it is', () => {
		render({ item: CONCEALED, width: 200, height: 200 });

		expect(host.textContent).not.toContain('hidden-1');
		expect(host.querySelector('.duration')).toBeNull();
	});
});

describe('the mark for a file whose bytes have gone', () => {
	/*
	 * It is a different picture from the hidden mark: "you put this out of sight" and "the file is
	 * not where Sift left it" have nothing to do with each other, and colour alone cannot tell them
	 * apart on a row of 16px badges.
	 *
	 * Asserted against the generated codepoint map, which is what the icon component looks a name
	 * up in; a name it cannot find draws an empty string, so comparing the two marks alone would
	 * pass for a glyph the font does not carry. Both halves are checked.
	 */
	const GONE = { id: 'gone-1', media_type: 'video', unreachable: true, hidden: true };

	it('is its own glyph, and not the one hidden wears', () => {
		render({ item: GONE, width: 200, height: 200, thumbSrc: '/api/assets/gone-1/thumb' });

		const gone = host.querySelector('.mark.gone .icon');
		const hidden = host.querySelector('.mark.vaulted .icon');
		expect(gone, 'the gone mark was not drawn at all').not.toBeNull();
		expect(hidden, 'the hidden mark was not drawn beside it').not.toBeNull();
		expect(gone?.textContent, 'the gone mark drew the crossed-out eye').not.toBe(
			hidden?.textContent
		);
	});

	it('is drawn on a file that never got a thumbnail, which is the tile that needs it most', () => {
		/*
		 * The one tile the mark exists for: a file whose bytes went before a still was made has no
		 * thumbnail, and the mark is a fact about the file, not a control on a picture. No
		 * `thumbSrc` and a `placeholder`, which is what the wall passes for a file it knows it
		 * cannot draw.
		 */
		render({ item: GONE, width: 200, height: 200, placeholder: 'Not here' });

		const gone = host.querySelector('.mark.gone .icon');
		expect(gone, 'the gone mark was not drawn on a tile with no still').not.toBeNull();
		expect(gone?.textContent).toBe(String.fromCodePoint(parseInt(codepoints.unknown_document, 16)));
		// The vault mark goes with it, and for the same reason: it is a fact about the file rather
		// than about the picture.
		expect(host.querySelector('.mark.vaulted .icon')).not.toBeNull();
	});

	it('is still withheld from a tile that is not being shown at all', () => {
		// A concealed tile says something is there and refuses to say what; a row of marks would
		// describe it.
		render({ item: { ...GONE, concealed: true }, width: 200, height: 200 });

		expect(host.querySelector('.marks')).toBeNull();
	});

	it('is withheld from one that is still arriving', () => {
		// The other half. Nothing about it is settled and the tile redraws when the still lands.
		render({ item: GONE, width: 200, height: 200, importing: true });

		expect(host.querySelector('.marks')).toBeNull();
	});

	it('draws a glyph the shipped font actually carries', () => {
		render({ item: GONE, width: 200, height: 200, thumbSrc: '/api/assets/gone-1/thumb' });

		const gone = host.querySelector('.mark.gone .icon');
		expect(gone?.textContent).toBe(String.fromCodePoint(parseInt(codepoints.unknown_document, 16)));
		expect(gone?.textContent, 'a name the icon font does not know draws nothing').not.toBe('');
	});
});

describe('the extension points other features build on', () => {
	it('draws whatever was put in the controls slot, and tells it which asset it is', () => {
		// Filled by the rating controls. Tested here because the alternative is a feature building
		// a tile of its own, and then there are two tiles.
		const controls = createRawSnippet<[string]>((id) => ({
			render: () => `<span class="planted">${id()}</span>`
		}));

		render({ item: ITEM, width: 200, height: 200, thumbSrc: '/thumb', controls });

		expect(host.querySelector('.planted')?.textContent).toBe('asset-1');
	});

	it('reports hover, so the grid can decide what plays', () => {
		const onhover = vi.fn();
		const tile = render({ item: ITEM, width: 200, height: 200, thumbSrc: '/t', onhover });

		tile.dispatchEvent(new PointerEvent('pointerenter', { bubbles: false }));
		expect(onhover).toHaveBeenCalledWith('asset-1', true);

		tile.dispatchEvent(new PointerEvent('pointerleave', { bubbles: false }));
		expect(onhover).toHaveBeenCalledWith('asset-1', false);
	});
});

describe('a tile with no still', () => {
	it('says it is importing, because in the grid that is what it means', () => {
		const tile = render({ item: ITEM, width: 200, height: 140 });

		expect(tile.querySelector('[aria-label="Importing"]')).not.toBeNull();
	});

	it('but says what it really is when the still is never coming', () => {
		/* A deleted file's derivatives went with its asset row. The importing shimmer moves as
		 * though work were happening and announces itself as "Still importing", which on the
		 * Trash screen is a lie to a screen reader and a permanently pulsing box to everybody
		 * else. */
		const tile = render({
			item: ITEM,
			width: 200,
			height: 140,
			placeholder: 'Deleted file, no preview'
		});

		expect(tile.querySelector('[aria-label="Deleted file, no preview"]')).not.toBeNull();
		expect(tile.querySelector('[aria-label="Importing"]')).toBeNull();
	});

	it('and a real still beats both', () => {
		const tile = render({
			item: ITEM,
			width: 200,
			height: 140,
			thumbSrc: '/api/assets/x/thumb',
			placeholder: 'no preview'
		});

		expect(tile.querySelector('img')).not.toBeNull();
		expect(tile.querySelector('[aria-label="no preview"]')).toBeNull();
	});
});

describe('a still that was promised and did not arrive', () => {
	it('says so, rather than leaving the browser to draw a torn page', () => {
		/* The one failure the grid cannot see coming. A tile is handed an address, not a picture,
		 * so a thumbnail that could not be made is a 404 that happens after the markup is written.
		 * Left alone the browser draws its own broken-image glyph, in the system's style, which
		 * reads as the whole app being broken rather than as one file having no still. */
		const tile = render({ item: ITEM, width: 200, height: 140, thumbSrc: '/api/assets/x/thumb' });

		tile.querySelector('img')?.dispatchEvent(new Event('error'));
		flushSync();

		expect(tile.querySelector('img')).toBeNull();
		expect(tile.querySelector('[aria-label="No preview"]')).not.toBeNull();
		// And not the importing shimmer: nothing is happening and nothing is going to.
		expect(tile.querySelector('[aria-label="Importing"]')).toBeNull();
	});

	it('and forgets it when the same tile is given a different asset', () => {
		/* The grid recycles tiles as it scrolls: the element stays, the asset in it changes. A
		 * failure remembered across that is one file's missing still drawn over another file's
		 * perfectly good one, and it survives until the tile happens to be destroyed.
		 *
		 * So the props are reactive here and the component is NOT remounted. Remounting proves
		 * nothing: a fresh tile starts unbroken whether or not anything resets it. */
		const props = $state({ item: ITEM, width: 200, height: 140, thumbSrc: '/api/assets/x/thumb' });
		const tile = render(props);

		tile.querySelector('img')?.dispatchEvent(new Event('error'));
		flushSync();
		expect(tile.querySelector('img')).toBeNull();

		props.thumbSrc = '/api/assets/y/thumb';
		flushSync();

		expect(tile.querySelector('img')?.getAttribute('src')).toBe('/api/assets/y/thumb');
	});
});

describe('a tile picked for a swap', () => {
	it('wears the one pick look with the swap mark, not the selection ring', () => {
		render({ item: ITEM, width: 200, height: 200, thumbSrc: '/thumb', swapPicked: true });
		const pick = host.querySelector('.pick') as HTMLElement | null;
		expect(pick?.dataset.purpose).toBe('swap');
		expect(pick?.getAttribute('aria-hidden')).toBe('true');
		expect(host.querySelector('.ring')).toBeNull();
		// The tile says where the pick sits: its corner, and above a hovered tile's lift.
		expect(tileSource).toMatch(
			/\.tile-frame \{[^}]*--pick-radius: var\(--tile-radius\);[^}]*--pick-layer: 3;/
		);
	});

	it('and one not picked for it draws none', () => {
		render({ item: ITEM, width: 200, height: 200, thumbSrc: '/thumb' });
		expect(host.querySelector('.pick')).toBeNull();
	});
});

describe('a tile that has been picked', () => {
	it('says so, rather than leaving the count in the bar to say it', () => {
		// A selection you cannot see is a selection you act on by accident. The bar says how many;
		// only the tile can say which.
		render({ item: ITEM, width: 200, height: 200, thumbSrc: '/thumb', picked: true });

		const tile = host.querySelector('.tile') as HTMLElement;
		expect(tile.className).toContain('picked');
		expect(tile.getAttribute('aria-pressed')).toBe('true');
	});

	it('and an unpicked one says nothing at all', () => {
		// Not `aria-pressed="false"`, which would announce every tile in the grid as a toggle that
		// happens to be off: on a screen of hundreds, that is noise standing in for information.
		render({ item: ITEM, width: 200, height: 200, thumbSrc: '/thumb' });

		const tile = host.querySelector('.tile') as HTMLElement;
		expect(tile.className).not.toContain('picked');
		expect(tile.getAttribute('aria-pressed')).toBeNull();
	});
});

describe('the ring that says a tile is picked', () => {
	it('is drawn over the tile rather than on it', () => {
		/* Not a box-shadow on the button: the hover rule sets a box-shadow too, at higher
		 * specificity, so the ring would vanish under the pointer, which is where the pointer is at
		 * the end of the long press that just selected something. Its own element cannot be
		 * overridden by the lift. */
		render({ item: ITEM, width: 200, height: 200, thumbSrc: '/thumb', picked: true });

		const ring = host.querySelector('.ring');
		expect(ring).not.toBeNull();
		// A sibling of the button, not a child: anything inside it is inside the thing that swallows
		// clicks meant for the tile's own controls.
		expect(ring?.parentElement?.className).toContain('tile-frame');
	});

	it('and there is no ring at all when nothing is picked', () => {
		render({ item: ITEM, width: 200, height: 200, thumbSrc: '/thumb' });

		expect(host.querySelector('.ring')).toBeNull();
	});
});

describe('the O counter on a tile', () => {
	/* The tally half of the O counter, on the wall as on the file's own screen. The number rides
	   down with the row (the wall already reads this account's own
	   state for the heart and the stars) so nothing here asks for it. */
	it('draws the drop and the number where the count is above nought', () => {
		render({ item: { ...ITEM, o_count: 3 }, width: 200, height: 120 });

		const mark = host.querySelector('.marks .seen .tally');
		expect(mark?.textContent).toBe('3');
	});

	it('says nothing at all at nought, which is most of a library', () => {
		// A counter has no "never pressed" that is different from nought, so a zero here would be
		// furniture on every tile rather than a fact about this one.
		render({ item: { ...ITEM, o_count: 0 }, width: 200, height: 120 });

		expect(host.querySelector('.marks')?.textContent ?? '').not.toContain('0');
	});
});

describe('the smallest tile keeps the one mark nobody chose', () => {
	/*
	 * At the smallest size the chosen marks are dropped, but not the gone mark, which says the
	 * bytes are not there on the tile least able to be told from any other.
	 *
	 * Read out of the stylesheet rather than measured: a container query needs a real layout
	 * engine, and this is a claim about what the rule says.
	 */
	it('hides the contents of the row rather than the row, and spares the gone mark', () => {
		const css = readFileSync(resolve('src/lib/components/Tile.svelte'), 'utf8');
		const rule = css.match(/@container \(max-height: 140px\) \{([\s\S]*?)\n\t\}/);

		expect(rule, 'the smallest-size rule is gone; this test is about what it hides').not.toBeNull();
		expect(rule?.[1]).toContain('.gone');
		expect(rule?.[1]).not.toMatch(/\.marks\s*\{/);
	});
});

/*
 * Every mark drawn always comes before every mark waiting for the hover.
 *
 * A mark shown only on hover is transparent at rest but keeps its space, so a fixed order would
 * hold a hole open at the start of the row. The row is drawn resting-first in the markup, not with
 * CSS `order`, because the sharing and hidden marks are buttons and the keyboard walks the markup.
 */
describe('the marks at rest come first', () => {
	afterEach(() => tileMarks.forget());

	const marksIn = () => [...host.querySelectorAll('.marks .mark')];

	it('puts an always-drawn Hidden mark ahead of a views count waiting for the hover', () => {
		tileMarks.follow({ [VIEWS_MARK]: ON_HOVER, [HIDDEN_MARK]: ALWAYS });
		render({ item: { ...ITEM, views: 4, hidden: true }, width: 200, height: 120 });

		const marks = marksIn();
		expect(marks).toHaveLength(2);
		expect(marks[0].classList.contains('vaulted'), 'the resting mark sat behind a hole').toBe(true);
		expect(marks[1].classList.contains(ON_HOVER_CLASS)).toBe(true);
	});

	it('keeps the fixed order when both are drawn the same way', () => {
		tileMarks.follow({ [VIEWS_MARK]: ALWAYS, [HIDDEN_MARK]: ALWAYS });
		render({ item: { ...ITEM, views: 4, hidden: true }, width: 200, height: 120 });

		const marks = marksIn();
		expect(marks[0].classList.contains('seen')).toBe(true);
		expect(marks[1].classList.contains('vaulted')).toBe(true);
	});
});

/*
 * Hover and selected are two different pictures. A hover is the lift and draws no ring; selected is
 * the ring, the picture pulled in and a tick in the corner. jsdom cannot point, so the stylesheet is
 * compiled with the pointer's and the keyboard's states spelled as attributes a test can hold.
 */
describe('hover and selected on a tile', () => {
	const held = heldStates(tileSource);

	afterEach(() => removeStyles());

	it('lifts under the pointer with a shadow and no ring', () => {
		const tile = render({ item: ITEM, width: 200, height: 150, thumbSrc: '/thumb' });
		applyStyles(held, tile);
		(host.querySelector('.tile-frame') as HTMLElement).setAttribute('data-hover', '');

		expect(getComputedStyle(tile).boxShadow).toBe('var(--elev-tile-lift)');
	});

	it('takes its focus as a ring drawn over the picture, which an outline sat under', () => {
		// The compiled stylesheet, because a browser fake computes no pseudo-element: the ring is an
		// overlay (`::after`) painted above the lifted picture layers, in the focus token.
		const rules = tileSource;
		const at = rules.indexOf('.tile:focus-visible::after');
		expect(at, 'no focus overlay on the tile').toBeGreaterThan(-1);
		const block = rules.slice(at, rules.indexOf('}', at));
		expect(block).toContain('box-shadow: var(--focus-ring)');
		expect(block).toContain('z-index');
		expect(rules).not.toMatch(/\.tile:focus-visible\s*\{[^}]*outline:/);
	});

	it('when picked, wears the selected ring and a tick, and an unpicked one wears neither', () => {
		render({ item: ITEM, width: 200, height: 150, thumbSrc: '/thumb', picked: true });
		const ring = host.querySelector('.ring') as HTMLElement;
		applyStyles(held, ring);

		expect(getComputedStyle(ring).boxShadow).toBe('var(--selected-ring)');
		expect(host.querySelector('.tile-frame > .selected-check')).not.toBeNull();

		unmount(mounted as Record<string, unknown>);
		mounted = null;
		host.remove();
		render({ item: ITEM, width: 200, height: 150, thumbSrc: '/thumb' });
		expect(host.querySelector('.selected-check')).toBeNull();
	});
});

/*
 * The marks in a tile's corner that are presses (the pin, sharing and Hidden marks) are 22px on a
 * phone, over a picture whose own press opens the file. They keep their size and reach a finger
 * through an invisible ring, the rule for a small press inside a card.
 */
describe('a mark that is a press, on a phone', () => {
	it('reaches a finger through a ring of its own', () => {
		const phone = tileSource.slice(tileSource.indexOf('@media (max-width: 767px)'));
		expect(phone).toMatch(/button\.mark \{\s*position: relative;\s*z-index: 1;/);
		expect(phone).toMatch(
			/button\.mark::after \{\s*content: '';\s*position: absolute;\s*inset-block: min\(0px, calc\(\(100% - var\(--touch-target\)\) \/ 2\)\);\s*inset-inline: min\(0px, calc\(\(100% - var\(--touch-target\)\) \/ 2\)\);/
		);
	});
});
