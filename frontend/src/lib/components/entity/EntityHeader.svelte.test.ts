/*
 * One door, not a strip: the page's verbs (Edit, Enrich, Auto-enrich, Sharing, Merge into..., Hide
 * them, Delete) are behind one worded trigger, and none is drawn as a button of its own.
 */

import { readFileSync } from 'node:fs';

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { words as wordsOn } from '$lib/design/testing.svelte';
import { createRawSnippet, flushSync, mount, unmount } from 'svelte';
import { reveal } from '$lib/shell/motion.svelte';

import BandHarness from './BandHarness.svelte';
import EntityHeader from './EntityHeader.svelte';
import type { Verb } from '$lib/components/common/verbs';
import codepoints from '$lib/generated/icon-codepoints.json';
import { toasts } from '$lib/shell/toasts.svelte';
import type { Frame } from '$lib/entity/cover-frame';

/* The disclosure the record opens with, watched rather than replaced: the real slide still runs,
   and a test can read which pace it was asked for. */
vi.mock('$lib/shell/motion.svelte', async (importOriginal) => {
	const real = await importOriginal<typeof import('$lib/shell/motion.svelte')>();
	return { ...real, reveal: vi.fn(real.reveal) };
});

const VERBS: Verb[] = [
	{ id: 'share', label: 'Sharing', icon: 'group', run: () => {} },
	{ id: 'merge', label: 'Merge into\u2026', icon: 'merge', run: () => {} }
];

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

beforeEach(() => {
	host = document.createElement('div');
	document.body.append(host);
	/* Both bands in this header are REMEMBERED, per kind, in the browser's own store, and one
	 * jsdom session shares one store across every test in the file. So a test that collapses the
	 * band leaves the next one starting collapsed, which is the header behaving exactly as it should
	 * and a test measuring the one before it. Cleared rather than worked around with a different
	 * `kind` per test, because the next person to add a case here would hit the same thing. */
	localStorage.clear();
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
});

function draw(props: Record<string, unknown>): void {
	drawn = mount(EntityHeader, {
		target: host,
		props: { name: 'Somebody', ...props }
	}) as Record<string, unknown>;
	flushSync();
}

/* Every button in the row, by what it says.
 *
 * Asked as "does any button say this" rather than by exact text: an icon inside a button
 * contributes its own ligature to `textContent`, so the worded trigger reads as `Options` followed
 * by a character that is not whitespace and does not trim away.
 */
function saysAnything(word: string): boolean {
	return [...host.querySelectorAll('button')].some((one) => (one.textContent ?? '').includes(word));
}

it('puts the page verbs behind one worded door rather than drawing each as a button', () => {
	draw({ options: VERBS, optionIds: ['e-1'] });

	expect(saysAnything('Options')).toBe(true);
	// The verbs themselves are rows in the menu, which is closed. None is a button in the row.
	expect(saysAnything('Sharing')).toBe(false);
	expect(saysAnything('Merge into\u2026')).toBe(false);
});

it('draws no door on a page that can be edited and has nothing else to offer', () => {
	/* The row still exists (Edit is in it) so this reaches the guard that decides whether the
	 * door itself is drawn. Without a record and an editor the row is absent altogether and the
	 * question is never asked, which is a test that cannot fail.
	 */
	draw({
		options: [],
		mayEdit: true,
		record: createRawSnippet(() => ({ render: () => '<p></p>' }))
	});

	expect(saysAnything('Edit')).toBe(true);
	expect(saysAnything('Options')).toBe(false);
});

/*
 * The header's right half: the labelled facts stand there whether or not the rest of the record is
 * open, so a page arriving shut still uses the width, and the name's column holds only what fits.
 */
it('draws the labelled facts beside the name, with the rest of the record shut', () => {
	draw({
		facts: createRawSnippet(() => ({ render: () => '<p class="probe-facts">Facts</p>' })),
		record: createRawSnippet(() => ({ render: () => '<p class="probe-record">Record</p>' }))
	});
	expect(host.querySelector('.facts .probe-facts')).not.toBeNull();
	expect(host.querySelector('.about .probe-facts')).toBeNull();
	expect(host.querySelector('.probe-record')).toBeNull();
});

/*
 * More opens the record, and it opens as the app's disclosure: inside the fold that the `reveal`
 * transition moves, rather than appearing between two frames.
 */
it('opens the rest of the record under More, inside the fold that moves', () => {
	draw({
		facts: createRawSnippet(() => ({ render: () => '<p class="probe-facts">Facts</p>' })),
		record: createRawSnippet(() => ({ render: () => '<p class="probe-record">Record</p>' }))
	});
	const more = [...host.querySelectorAll('button')].find(
		(one) => (one.textContent ?? '').replace(/[\uE000-\uF8FF]/g, '').trim() === 'More'
	);
	vi.mocked(reveal).mockClear();
	more?.click();
	flushSync();

	expect(host.querySelector('.facts .record-fold .probe-record')).not.toBeNull();
	/* At `--dur-base`, one step quicker than a disclosure's `--dur-slow`: a column of facts
	   beside the name, not a large area of the screen. 200 is the token's fallback, since the
	   test document has no stylesheet to read it from. */
	expect(vi.mocked(reveal)).toHaveBeenCalled();
	const [, options] = vi.mocked(reveal).mock.calls[0];
	expect(options).toEqual({ pace: 'base' });
	expect(vi.mocked(reveal).mock.results[0].value.duration).toBe(200);
});

/*
 * The O tally: the drop and the figure, as the player's O counter draws it, never "O count 2".
 */
it('draws the O tally as the drop and its figure, and nothing for nought', () => {
	draw({ counts: 'On 546 files', oCount: 2 });

	const line = host.querySelector('.counts');
	expect(line?.textContent).toContain('On 546 files');
	expect(line?.textContent).not.toContain('O count');
	const tally = line?.querySelector('.o-tally');
	expect(tally?.textContent).toContain('2');
	expect(tally?.textContent).toContain(
		String.fromCodePoint(Number.parseInt(codepoints.water_drop, 16))
	);

	if (drawn) unmount(drawn);
	host.replaceChildren();
	draw({ counts: 'On 546 files', oCount: 0 });
	expect(host.querySelector('.counts .o-tally')).toBeNull();
});

it('names the door for the thing it is about, for somebody who cannot see the page', () => {
	draw({ options: VERBS });

	const trigger = [...host.querySelectorAll('button')].find((one) =>
		(one.textContent ?? '').includes('Options')
	);
	expect(trigger?.getAttribute('aria-label') ?? host.innerHTML).toContain('Somebody');
});

/* Putting the band away, which is what the tabs under it are waiting for.
 *
 * The header sits in the frame's non-scrolling track, so its height comes off the wall of files for
 * as long as the page is open. Somebody browsing what a person is IN does not need the picture and
 * the record taking the top third of the window, and somebody reading about them does, so it is
 * asked, remembered, and the way back is in the same row it went from.
 */
function collapser(): HTMLButtonElement | undefined {
	return [...host.querySelectorAll('button')].find((one) =>
		(one.getAttribute('aria-label') ?? '').endsWith('the details')
	);
}

it('offers a way to put the band away, on a page with no options and nothing to edit', () => {
	// A guest reading a tag: the row, and the control that puts the band away, belong to every
	// entity page, not only one with verbs or a record.
	draw({});

	expect(collapser()).toBeDefined();
});

it('closes the band, takes it out of reach, and leaves the way back', () => {
	draw({ name: 'Somebody', options: VERBS, counts: 'On 12 items' });
	const band = () => host.querySelector('.band');
	expect(band()?.getAttribute('aria-hidden')).toBe('false');
	expect(band()?.classList.contains('open')).toBe(true);

	collapser()!.click();
	flushSync();

	/*
	 * Held in the DOM and hidden, rather than removed, which is what lets the band animate from a
	 * closed height. So what is asserted is that it is out of reach: `aria-hidden` is the half a
	 * test can see; the other half is `visibility: hidden` on the clipped child, which jsdom does
	 * not compute, so the class carrying it is checked instead.
	 */
	expect(band()?.getAttribute('aria-hidden')).toBe('true');
	expect(band()?.classList.contains('open')).toBe(false);
	expect(host.querySelector('h1')).not.toBeNull();

	// ...and the control that did it is still there, in the row it was in, outside the band.
	expect(collapser()).toBeDefined();
	expect(saysAnything('Options')).toBe(true);
	expect(band()?.contains(collapser()!)).toBe(false);
});

it('keeps the controls out of the band, so pressing the control cannot move it', () => {
	/*
	 * The controls must not move when the band is put away, since this one is pressed precisely to
	 * change how much room the band takes. Asserted structurally rather than by pixels: the row is
	 * a sibling of the hero and outside it in both states, so nothing the hero does can move it.
	 * jsdom has no layout, so a position assertion would be two zeroes agreeing.
	 */
	draw({ options: VERBS });

	// The title row is the same `PageHeader` every wall draws, and it carries the name.
	const line = host.querySelector('.page-header');
	expect(line).not.toBeNull();
	expect(line!.querySelector('h1')?.textContent).toContain('Somebody');
	expect(line!.querySelector('.controls')).not.toBeNull();
	expect(host.querySelector('header.hero .controls')).toBeNull();
	expect(host.querySelector('header.hero h1')).toBeNull();

	collapser()!.click();
	flushSync();

	// Still there, still on the title row, and still outside the band that just closed, which is
	// what stops the band's own height from having any say over where they are.
	expect(host.querySelector('.page-header .controls')).not.toBeNull();
	expect(host.querySelector('.band')?.contains(host.querySelector('.page-header')!)).toBe(false);
	expect(host.querySelector('.band .controls')).toBeNull();
});

it('shows the act on the glyph rather than the state', () => {
	/* Arrows OUT while the band is showing, because pressing it is what gives the page the screen;
	 * arrows IN once it has, because pressing it is what brings the band back. The other way round
	 * would be the opposite of what the player's own fullscreen button does with the same pair.
	 *
	 * Read off the CODEPOINT, not the name. The icon font is subset by codepoint with ligature
	 * closure off (thirty names reach most of the alphabet and would keep 407 KB of font instead
	 * of 4) so `Icon` renders the character rather than the word, and asserting on the word finds
	 * two spaces. The map is the same generated one the component reads, so this cannot drift from
	 * whatever the build produced. */
	draw({ options: VERBS });
	const out = String.fromCodePoint(parseInt(codepoints.open_in_full, 16));
	const back = String.fromCodePoint(parseInt(codepoints.close_fullscreen, 16));
	expect(out).not.toBe(back);
	expect(collapser()!.textContent).toContain(out);

	collapser()!.click();
	flushSync();

	expect(collapser()!.textContent).toContain(back);
});

it('does not draw the page own band at all until the header band has been opened once', () => {
	/*
	 * The one part of this header that asks the server anything: on a person, how reliably Sift
	 * recognizes them and what is waiting to be agreed. Neither can be seen while the band is shut,
	 * so neither is asked until it first opens; collapsing the band to browse faster costs no
	 * requests.
	 */
	localStorage.setItem('sift.entity.compact.entity', 'yes');
	draw({
		options: VERBS,
		underCover: createRawSnippet(() => ({ render: () => '<p>Identifies them well</p>' }))
	});

	expect(host.querySelector('.cover-column p')).toBeNull();

	collapser()!.click();
	flushSync();
	expect(host.querySelector('.cover-column p')?.textContent).toBe('Identifies them well');

	/* And it STAYS once it is there. Latched rather than read straight off the band, so collapsing
	 * and expanding does not ask the server the same two questions again. */
	collapser()!.click();
	flushSync();
	expect(host.querySelector('.cover-column p')).not.toBeNull();
});

it('draws the other page band under the PICTURE, in the cover column', () => {
	/*
	 * The name column is where the facts are, so a band there pushes the bottom of the hero, where
	 * the tabs start, down the window. Under the picture the same block spends ground nothing was
	 * using.
	 */
	draw({
		underCover: createRawSnippet(() => ({ render: () => '<p>13 confirmed</p>' }))
	});

	expect(host.querySelector('.cover-column > p')?.textContent).toBe('13 confirmed');
	expect(host.querySelector('.about p'), 'it landed in the name column').toBeNull();
});

it('is drawn exactly as it always was where a page passes no band at all', () => {
	/* Every other entity page (a Site, a tag, a collection, a Photo Set) passes neither
	 * snippet, and the column has to be what it was: the picture, and nothing added around it. */
	draw({});

	const column = host.querySelector('.cover-column');
	expect(column?.children.length, 'something was added to the cover column').toBe(1);
	expect(column?.firstElementChild?.classList.contains('cover')).toBe(true);
});

it('and every other entity page really does pass no band', () => {
	/*
	 * "Every other entity page passes neither snippet" is read off the pages themselves (site, tag,
	 * collection, Photo Set), so the test above speaks for all four. `underTags` is in the list
	 * because no such slot exists: a page passing it would be passing a prop nothing renders,
	 * silently.
	 */
	for (const page of ['tags', 'collections', 'photo-sets', 'sites']) {
		const source = readFileSync(`src/routes/${page}/[id]/+page.svelte`, 'utf8');
		expect(source, `${page} does not draw the shared header`).toContain('EntityHeader');
		expect(source, `${page} passes a header band`).not.toContain('underCover');
		expect(source, `${page} passes the slot that was replaced`).not.toContain('underTags');
	}
});

/*
 * Who else has described this thing, on the line with the heart and the stars. The marks name the
 * box, since with several services configured the word "stash_box" alone would send somebody to
 * Settings to find out which one, and the page hands them in because it already holds them.
 */
it('names each stash-box that knows this thing, beside the heart and the stars', () => {
	draw({
		favorite: false,
		onfavorite: () => {},
		enrichedBy: [
			{ name: 'StashDB', box: 'stashdb' },
			{ name: 'PMVStash', box: 'pmvstash' }
		]
	});

	/*
	 * Through the group `EnrichmentMarks` announces itself as, not `.opinions .mark`: the rating's
	 * button in this row is also called `.mark`, in its own component and scope, and a selector
	 * cannot tell the two apart. The group is the thing being asked about.
	 */
	const group = host.querySelector('.opinions [aria-label="Enriched by"]');
	const marks = [...(group?.querySelectorAll('.mark') ?? [])];
	/* "Enriched by " and then the Enriched by column's own row, word for word. The group carries the
	   same words for a screen reader and that is not the same thing as a tooltip, which is read on
	   its own over one glyph: the mark says the whole fact. */
	expect(marks.map((one) => one.getAttribute('aria-label'))).toEqual([
		'Enriched by Stash-box: StashDB',
		'Enriched by Stash-box: PMVStash'
	]);
});

it('draws the marks even where this kind of thing holds no opinion of its own', () => {
	/* A handle carries no heart and no stars, so the row is not drawn for it at all, and a
	 * stash-box can still know one. The row stands for the marks alone rather than the marks
	 * disappearing with the switches, which is the fault the widened condition exists to avoid. */
	draw({ enrichedBy: [{ name: 'StashDB', box: 'stashdb' }] });

	expect(host.querySelectorAll('.opinions .mark')).toHaveLength(1);
	expect(host.querySelector('.opinions button')).toBeNull();
});

it('draws no row at all for a thing nothing has described and nobody may rate', () => {
	draw({});

	expect(host.querySelector('.opinions')).toBeNull();
});

/*
 * WHO MADE IT, which is a different fact from who has described it.
 *
 * A person five boxes know about may have been typed in by hand, and that difference is what this
 * line exists to show. Its own line under the marks because a mark cannot carry it: a row of marks
 * is a set of "this box has described this", and one of them quietly meaning something else is the
 * kind of thing nobody would ever read correctly.
 */
it('says which stash-box made this thing, under the marks', () => {
	draw({
		enrichedBy: [{ name: 'StashDB', box: 'stashdb' }],
		madeBy: {
			kind: 'stash_box',
			via: 'stash',
			box_id: 'b-1',
			box_name: 'FansDB',
			box_slug: 'fansdb'
		}
	});

	const line = host.querySelector('.made-by');
	expect(line?.textContent).toContain('Created by FansDB');
	// Through the same component the marks above it use, so one box wears one colour on one screen.
	expect(line?.querySelector('[aria-label="Created by Stash-box: FansDB"]')).not.toBeNull();
});

/*
 * The mark on that line says Created by, not Enriched by: the line is separate from the row of
 * marks precisely so the two claims cannot be confused. Asserted on both the tooltip's words and
 * the group they sit in, because a screen reader is given the group's label first and a pointer
 * never is.
 */
it('says CREATED by on the maker line, where the marks above it say enriched by', () => {
	draw({
		enrichedBy: [{ name: 'StashDB', box: 'stashdb' }],
		madeBy: {
			kind: 'stash_box',
			via: 'stash',
			box_id: 'b-2',
			box_name: 'PMVStash',
			box_slug: 'pmvstash'
		}
	});

	const made = host.querySelector('.made-by .marks');
	expect(made?.getAttribute('aria-label')).toBe('Created by');
	expect(made?.querySelector('.mark')?.getAttribute('aria-label')).toBe(
		'Created by Stash-box: PMVStash'
	);

	// And the row above is untouched: one component, two sentences, and the default is the one
	// nearly every caller draws.
	const described = host.querySelector('.opinions .marks');
	expect(described?.getAttribute('aria-label')).toBe('Enriched by');
	expect(described?.querySelector('.mark')?.getAttribute('aria-label')).toBe(
		'Enriched by Stash-box: StashDB'
	);
});

/*
 * The three makers that are not a box. Anything not made by hand has a creation record, so the line
 * names the pass, and the pass's glyph comes from the same table the Enriched by column reads: one
 * word, one mark, wherever either is drawn.
 */
it('says which of its own passes made a thing, with that pass mark beside it', () => {
	draw({
		madeBy: { kind: 'sift', via: 'folder', box_id: null, box_name: null, box_slug: null }
	});

	const line = host.querySelector('.made-by');
	expect(line?.textContent).toContain('Created by Sift, from a folder name');
	expect(line?.querySelector('.mark')).not.toBeNull();
});

it('draws a tag Sift made for a copy in the glyph and words of the act that made the copy', () => {
	// The compressed copy's tag and the edited copy's tag each say their act, and the mark is the
	// one the act's verb wears (Compress, the editor's Modify), not one wand for both.
	for (const [act, said] of [
		['compress', 'Created by Sift, from a file it compressed'],
		['edit', 'Created by Sift, from a file it edited']
	] as const) {
		draw({
			madeBy: { kind: 'sift', via: 'produced', act, box_id: null, box_name: null, box_slug: null }
		});
		const line = host.querySelector('.made-by');
		expect(line?.textContent).toContain(said);
		expect(line?.querySelector('.mark')?.getAttribute('aria-label')).toBe(said.replace(', ', ': '));
		// The glyph drawn is the act's own, not a wand shared by every copy's tag.
		expect(line?.querySelector('.mark .icon')?.textContent).toBe(
			String.fromCodePoint(
				parseInt(codepoints[act === 'compress' ? 'compress' : 'design_services'], 16)
			)
		);
		if (drawn) unmount(drawn);
		drawn = null;
	}
});

it('names a download as the pass it is, which no folder word could say', () => {
	draw({ madeBy: { kind: 'sift', via: 'download', box_id: null, box_name: null, box_slug: null } });
	expect(host.querySelector('.made-by')?.textContent).toContain('Created by Sift, from a download');
});

it('says YOU to the account that made it, with no mark beside the words', () => {
	/* No glyph for a person: there is nothing to say beyond the words, and a mark meaning "a pass
	   did this" over a line that says a person did is the exact confusion this line exists to end. */
	draw({ madeBy: { kind: 'you', via: null, box_id: null, box_name: null, box_slug: null } });
	const line = host.querySelector('.made-by');
	expect(line?.textContent).toContain('Created by you');
	expect(line?.querySelector('.mark')).toBeNull();
});

it('never names another account, only that one made it', () => {
	/* The withholding the sharing screens make: an account that is not the asker's is a fact kept
	   to an admin, and this line is drawn to anybody who may see the thing. The server decides
	   which of the two words it is, so nothing here has to know who is signed in. */
	draw({
		madeBy: { kind: 'another_user', via: null, box_id: null, box_name: null, box_slug: null }
	});
	expect(host.querySelector('.made-by')?.textContent).toContain('Created by another user');
});

it('says nothing at all about who made a thing nothing recorded', () => {
	/* Most of a library: everything somebody typed in, everything a folder name produced, and
	   everything here before Sift wrote this down. A line reading "Created by nobody" would be a
	   claim, where no line is the honest absence of one. */
	draw({ enrichedBy: [{ name: 'StashDB', box: 'stashdb' }] });

	expect(host.querySelector('.made-by')).toBeNull();
});

/*
 * The kind's own mark, left of the name. It is the rail's glyph for that kind's wall (see
 * `iconOf`), so what is asserted is that the heading carries the codepoint for the name given,
 * inside the heading.
 */
it('draws the kind glyph inside the heading, before the name', () => {
	draw({ icon: 'shoppingmode' });

	const heading = host.querySelector('h1');
	expect(heading).not.toBeNull();
	const mark = heading?.querySelector('.icon');
	expect(mark).not.toBeNull();
	// The glyph itself, which `Icon` renders as the character at that codepoint rather than as the
	// hexadecimal it is written down in.
	expect(mark?.textContent).toBe(String.fromCodePoint(parseInt(codepoints.shoppingmode, 16)));
	// Hidden from anything reading the page out: the name beside it already says what this is.
	expect(mark?.getAttribute('aria-hidden')).toBe('true');
});

/*
 * What is waiting on a record is not in the header at all. The column beside the name is capped at
 * 24rem, too narrow for a question with two values and two buttons, so it lives at the top of the
 * History tab, and the tab wears a mark (see `EntityHistory.waiting` and `Tabs`, where both
 * halves are proved). What is asserted here: the column beside the name is the record's, and it is
 * not drawn when the record is not showing.
 */
it('draws no column beside the name until the record itself is showing', () => {
	draw({
		record: createRawSnippet(() => ({ render: () => '<p data-record>Born somewhere</p>' }))
	});

	// The band starts shut, so the More control is the way in and nothing is beside the name yet.
	expect(host.querySelector('.facts')).toBeNull();
	expect(host.querySelector('[data-record]')).toBeNull();
});

/*
 * The ground the whole screen stands on.
 *
 * The blur, the scrim, the layer's extent and its clip are stylesheet, which jsdom does not
 * compute. What a test can hold is what would be a fault under any styling: the ground is the
 * picture the header already drew, from the same address; a page with no picture has no ground
 * rather than an empty box over the screen; and the ground goes when the header does.
 *
 * Drawn through `BandHarness`, a header inside a frame, because the layer is the frame's
 * (`.frame-backdrop`) and the picture is the header's: what these test is the join, and neither
 * half alone proves anything.
 */
it('stands the screen on the same picture the header draws beside the name', () => {
	const host2 = document.createElement('div');
	document.body.append(host2);
	const band = mount(BandHarness, { target: host2, props: { coverAssetId: 'a-1' } });
	flushSync();

	const backdrop = host2.querySelector('.frame-backdrop');
	const picture = backdrop?.querySelector('img');
	expect(picture?.getAttribute('src')).toBe('/api/assets/a-1/thumb');

	// The SAME address the cover itself is drawn from, which is what keeps this to one request and
	// stops the two pictures ever being two pictures.
	const drawnSources = [...host2.querySelectorAll('img')].map((one) => one.getAttribute('src'));
	expect(drawnSources.filter((src) => src === '/api/assets/a-1/thumb').length).toBe(2);

	// It is a ground: nothing to read out, and nothing to press.
	expect(backdrop?.getAttribute('aria-hidden')).toBe('true');
	expect(picture?.getAttribute('alt')).toBe('');

	// And it goes with the header rather than outliving it: a frame is kept across a screen's own
	// changes (an entity page moving to its edit form keeps it), so an uncleared backdrop would be
	// somebody's face behind a page that is not about them.
	unmount(band);
	flushSync();
	expect(host2.querySelector('.frame-backdrop')).toBeNull();
	host2.remove();
});

it('draws no ground at all for a thing with no picture', () => {
	// No cover, no upload, no face and no name a site's own mark could be found under, which is
	// every entity nobody has given a picture to, and most of a fresh library.
	const host2 = document.createElement('div');
	document.body.append(host2);
	const band = mount(BandHarness, { target: host2, props: { counts: 'On 12 items' } });
	flushSync();

	expect(host2.querySelector('.frame-backdrop')).toBeNull();

	unmount(band);
	host2.remove();
});

/* A site's own logo, and the two places it can come from.
 *
 * The header asks the site's cover address when Sift's shipped icon pack has a picture for it, and
 * a picture from a pack of favicons is a MARK: drawn whole, at the letter's measure, never
 * cropped and never stretched, whichever of the two sources it came from. Asked only of the other
 * source (the logo a download fetched), every site Sift ships a picture for would have a small
 * logo blown up to fill the cover, and the Sites wall with it.
 */
it('draws the shipped pack picture as a mark rather than as a photograph of the site', () => {
	draw({
		name: 'Quillhouse',
		coverHref: '/sites/01ABC',
		siteName: 'Quillhouse',
		siteIcon: true
	});
	const picture = host.querySelector('img.picture');
	expect(picture?.classList.contains('mark')).toBe(true);
});

it("puts the logo's token on the pack picture's address, so the header's logo is kept", () => {
	draw({
		name: 'Quillhouse',
		coverHref: '/sites/01ABC',
		siteName: 'Quillhouse',
		siteIcon: '0.1.171-abcdef0123456789',
		art: 'f7'
	});
	expect(host.querySelector('img.picture')?.getAttribute('src')).toBe(
		'/api/sites/01ABC/cover?v=f7.0.1.171-abcdef0123456789'
	);
});

it('draws a chosen cover as the frame it is, which is cropped to the box', () => {
	draw({
		name: 'Quillhouse',
		coverHref: '/sites/01ABC',
		coverAssetId: '01DEF',
		siteName: 'Quillhouse',
		siteIcon: true
	});
	const picture = host.querySelector('img.picture');
	expect(picture?.classList.contains('mark')).toBe(false);
});

/*
 * The two controls that end an edit, together and in one order. This is the top half of the pair;
 * the form's own row at the foot is the bottom half, held in `RecordForm.svelte.test.ts`. Both rows
 * read the same way round, because a record long enough to scroll shows one at a time.
 */
it('offers Cancel and then Save while the record is being edited', () => {
	draw({
		record: createRawSnippet(() => ({ render: () => '<p>the record</p>' })),
		mayEdit: true,
		editing: true,
		saveForm: 'a-record-form'
	});

	const words = [...host.querySelectorAll('button')]
		.map((one) => one.querySelector('.label')?.textContent?.trim())
		.filter((one) => one === 'Cancel' || one === 'Save');
	expect(words).toEqual(['Cancel', 'Save']);

	/* Save is the FORM's submit reached from outside it, which is what stops the page holding a
	   second way to save. Without the attribute the button would be a live control wired to
	   nothing, and every assertion above would still pass. */
	const save = [...host.querySelectorAll('button')].find(
		(one) => one.querySelector('.label')?.textContent?.trim() === 'Save'
	) as HTMLButtonElement;
	expect(save.getAttribute('form')).toBe('a-record-form');
	expect(save.getAttribute('type')).toBe('submit');
});

it('draws neither of them for somebody who may not edit', () => {
	draw({
		record: createRawSnippet(() => ({ render: () => '<p>the record</p>' })),
		mayEdit: false
	});

	expect(saysAnything('Edit')).toBe(false);
	expect(saysAnything('Save')).toBe(false);
});

/*
 * A way to take a chosen cover off. One control for all five kinds, beside the pencil; the write is
 * the page's (`oncover`). What is tested is when the control exists, the question before an
 * uploaded picture goes, and the Undo after a file stops being the cover.
 */
function removeButton(): HTMLButtonElement | null {
	return host.querySelector<HTMLButtonElement>('button[aria-label="Remove the cover"]');
}

it('offers to remove a chosen cover, and only a chosen one, to somebody who may edit', () => {
	const oncover = vi.fn(async () => {});
	draw({ coverHref: '/people/p1', coverAssetId: 'a1', mayEdit: true, oncover });
	expect(removeButton()).not.toBeNull();

	// Nothing chosen: what shows is the automatic picture, and there is nothing to take off.
	unmount(drawn as Record<string, unknown>);
	draw({ coverHref: '/sites/s1', siteIcon: 'tok', mayEdit: true, oncover });
	expect(removeButton()).toBeNull();

	unmount(drawn as Record<string, unknown>);
	draw({ coverHref: '/people/p1', coverAssetId: 'a1', mayEdit: false, oncover });
	expect(removeButton()).toBeNull();

	unmount(drawn as Record<string, unknown>);
	draw({ coverHref: '/people/p1', coverAssetId: 'a1', mayEdit: true });
	expect(removeButton()).toBeNull();
	drawn = null;
	host.replaceChildren();
});

/** Every declaration the component's rules give `selector`, later rules winning, as CSS reads them. */
function declared(selector: string): Record<string, string> {
	const source = readFileSync('src/lib/components/entity/EntityCover.svelte', 'utf8');
	const style = source.slice(source.indexOf('<style')).replace(/\/\*[\s\S]*?\*\//g, '');
	const said: Record<string, string> = {};
	for (const [, selectors, body] of style.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
		const members = selectors.split(',').map((one) => one.trim());
		if (!members.includes(selector)) continue;
		for (const line of body.split(';')) {
			const [property, ...value] = line.split(':');
			if (value.length > 0) said[property.trim()] = value.join(':').trim();
		}
	}
	return said;
}

it('puts the remove control in the BOTTOM-LEFT corner of the cover, the pencil bottom-right', () => {
	// The cover's delete icon goes bottom-left: along the pencil's edge, in the other corner, so
	// neither is pressed for the other.
	const remove = declared('.cover :global(.remove-cover)');
	expect(remove['inset-block-end']).toBe('var(--space-2)');
	expect(remove['inset-block-start'] ?? 'auto').toBe('auto');
	expect(remove['inset-inline-start']).toBe('var(--space-2)');
	expect(remove['inset-inline-end']).toBe('auto');

	const pencil = declared('.cover :global(.pencil)');
	expect(pencil['inset-block-end']).toBe('var(--space-2)');
	expect(pencil['inset-inline-end']).toBe('var(--space-2)');
});

it('takes a file off immediately and offers Undo, which writes the same file and moment back', async () => {
	toasts.clear();
	const oncover = vi.fn(async () => {});
	draw({ coverHref: '/people/p1', coverAssetId: 'a1', coverAtMs: 1500, mayEdit: true, oncover });

	removeButton()?.click();
	await vi.waitFor(() => expect(toasts.items.map((one) => one.message)).toContain('Cover removed'));
	expect(oncover).toHaveBeenCalledWith(null, null);

	const said = toasts.items.find((one) => one.message === 'Cover removed');
	expect(said?.action?.label).toBe('Undo');
	said?.action?.run();
	// The window goes back with the picture, so an Undo is the cover as it was, framed as it was.
	expect(oncover).toHaveBeenLastCalledWith('a1', 1500, { frame: null });
	toasts.clear();
});

it('asks before an uploaded picture goes, because nothing can put it back', async () => {
	toasts.clear();
	const oncover = vi.fn(async () => {});
	draw({ coverHref: '/people/p1', coverUploadId: 'u1', mayEdit: true, oncover });

	removeButton()?.click();
	flushSync();
	expect(oncover).not.toHaveBeenCalled();
	const question = document.body.querySelector('[role="alertdialog"], [role="dialog"]');
	expect(question?.textContent).toContain("can't be put back");
	// An uploaded picture ceases to exist, so the question says Delete (the Delete/Remove rule).
	expect(question?.textContent).toContain('Delete this cover?');

	const confirm = [...document.body.querySelectorAll('button')].find(
		(one) => one.textContent?.trim() === 'Delete'
	);
	confirm?.click();
	await vi.waitFor(() => expect(oncover).toHaveBeenCalledWith(null, null));
	// No Undo on an upload: there is nothing left to write back.
	await vi.waitFor(() => expect(toasts.items.map((one) => one.message)).toContain('Cover removed'));
	expect(toasts.items.find((one) => one.message === 'Cover removed')?.action).toBeUndefined();
	toasts.clear();
});

/* --- reframing the chosen cover --- */

function reframeButton(): HTMLButtonElement | null {
	return host.querySelector<HTMLButtonElement>('button[aria-label="Reframe the cover"]');
}

/** Give the editor's measuring picture a shape, the way a browser would when it loads. jsdom loads
 *  no pictures, so the size is set and the event sent by hand. */
function pictureLoads(width: number, height: number): void {
	const measure = document.body.querySelector<HTMLImageElement>('img.measure');
	expect(measure, 'the editor fetches the whole picture').not.toBeNull();
	Object.defineProperty(measure, 'naturalWidth', { value: width });
	Object.defineProperty(measure, 'naturalHeight', { value: height });
	measure?.dispatchEvent(new Event('load'));
	flushSync();
}

/** A key pressed on one of the crop rectangle's grips, the way the picture editor is driven. */
function press(grip: string, key: string, shiftKey = false): void {
	const handle = document.body.querySelector<HTMLElement>(`[data-grip="${grip}"]`);
	expect(handle, `the crop rectangle has a ${grip} grip`).not.toBeNull();
	handle?.dispatchEvent(new KeyboardEvent('keydown', { key, shiftKey, bubbles: true }));
	flushSync();
}

function saveButton(): HTMLButtonElement | undefined {
	return [...document.body.querySelectorAll<HTMLButtonElement>('button')].find(
		(one) => wordsOn(one) === 'Save'
	);
}

it('offers to reframe a chosen cover, and only a chosen one', () => {
	const oncover = vi.fn(async () => {});
	draw({ coverHref: '/people/p1', coverAssetId: 'a1', mayEdit: true, oncover });
	expect(reframeButton()).not.toBeNull();

	unmount(drawn as Record<string, unknown>);
	draw({ coverHref: '/sites/s1', siteIcon: 'tok', mayEdit: true, oncover });
	expect(reframeButton()).toBeNull();

	unmount(drawn as Record<string, unknown>);
	draw({ coverHref: '/people/p1', coverAssetId: 'a1', oncover });
	expect(reframeButton(), 'a guest reframes nothing').toBeNull();
});

it('frames over the WHOLE picture and saves the window through the cover write', async () => {
	const oncover = vi.fn(async () => {});
	draw({ coverHref: '/people/p1', coverAssetId: 'a1', coverAtMs: 1500, mayEdit: true, oncover });

	reframeButton()?.click();
	flushSync();
	const measure = document.body.querySelector<HTMLImageElement>('img.measure');
	expect(measure?.getAttribute('src')).toContain('whole=1');
	pictureLoads(1600, 900);

	// The middle of the picture editor's crop rectangle moves it, one hundredth per press.
	press('move', 'ArrowRight');
	saveButton()?.click();

	await vi.waitFor(() => expect(oncover).toHaveBeenCalled());
	const [assetId, atMs, more] = oncover.mock.calls[0] as unknown as [
		string,
		number,
		{ frame: { x: number; w: number; h: number } }
	];
	expect([assetId, atMs]).toEqual(['a1', 1500]);
	// A 16:9 picture framed for a 3:4 box, moved one step right of the middle.
	expect(more.frame.h).toBe(1);
	expect(more.frame.x + more.frame.w / 2).toBeCloseTo(0.51, 2);
});

it('writes nothing when the window was not moved', async () => {
	const oncover = vi.fn(async () => {});
	draw({ coverHref: '/people/p1', coverAssetId: 'a1', mayEdit: true, oncover });

	reframeButton()?.click();
	flushSync();
	pictureLoads(1600, 900);
	saveButton()?.click();
	flushSync();

	// Saved untouched, a write would be recorded as the cover being chosen again.
	expect(oncover).not.toHaveBeenCalled();
});

it('reframes an uploaded cover by naming the upload, never a file', async () => {
	const oncover = vi.fn(async () => {});
	draw({ coverHref: '/people/p1', coverUploadId: 'u1', mayEdit: true, oncover });

	reframeButton()?.click();
	flushSync();
	pictureLoads(900, 1600);
	// A corner pulled in is the zoom: the rectangle shrinks toward the corner opposite.
	press('se', 'ArrowLeft', true);
	saveButton()?.click();

	await vi.waitFor(() => expect(oncover).toHaveBeenCalled());
	const [assetId, atMs, more] = oncover.mock.calls[0] as unknown as [
		null,
		null,
		{ uploadId: string; frame: { x: number; y: number; w: number } }
	];
	expect([assetId, atMs, more.uploadId]).toEqual([null, null, 'u1']);
	expect(more.frame.w).toBeLessThan(1);
	// A portrait picture's largest 3:4 window is its full width, centred down it; the top-left held.
	expect([more.frame.x, more.frame.y]).toEqual([0, 0.125]);
});

it('draws the cover through an address that names its window', () => {
	const frame = { x: 0.125, y: 0, w: 0.5, h: 0.6667 };
	draw({ coverHref: '/people/p1', coverAssetId: 'a1', coverFrame: frame, art: 's1' });
	const sources = [...host.querySelectorAll('img')].map((one) => one.getAttribute('src') ?? '');
	expect(sources.some((one) => one.includes(encodeURIComponent('s1.a1.f1250-0-5000-6667')))).toBe(
		true
	);
});

/* --- the reframe is the picture editor's crop --- */

it("reframes with the picture editor's own crop control, held to the cover's 3:4", async () => {
	const oncover = vi.fn(async () => {});
	draw({ coverHref: '/people/p1', coverAssetId: 'a1', mayEdit: true, oncover });

	reframeButton()?.click();
	flushSync();
	pictureLoads(1600, 900);

	// The editor's control: eight grips and the middle, and none of a separate framer's own
	// gestures.
	const grips = [...document.body.querySelectorAll('[data-grip]')].map((one) =>
		one.getAttribute('data-grip')
	);
	expect(grips.sort()).toEqual(['e', 'move', 'n', 'ne', 'nw', 's', 'se', 'sw', 'w']);
	expect(document.body.querySelector('.marquee')).not.toBeNull();
	expect(document.body.querySelector('[aria-roledescription="frame"]')).toBeNull();
	expect(
		document.body.querySelector('input[type="range"]'),
		'no zoom slider of its own'
	).toBeNull();
	// And the editor's foot: Cancel beside the act.
	const words = [...document.body.querySelectorAll('button')].map((one) => one.textContent?.trim());
	expect(words).toContain('Cancel');

	// An EDGE pulled, which on a free rectangle changes only the width: the shape lock follows it.
	press('e', 'ArrowLeft', true);
	saveButton()?.click();

	await vi.waitFor(() => expect(oncover).toHaveBeenCalled());
	const more = (oncover.mock.calls[0] as unknown as [string, null, { frame: Frame }])[2];
	// Width over height IN PIXELS of a 16:9 picture is the cover's 3:4.
	expect((more.frame.w * (1600 / 900)) / more.frame.h).toBeCloseTo(3 / 4, 3);
	expect(more.frame.w).toBeLessThan(largestWide());
});

it('stops the frame at a quarter of its largest, where the cover would only be a blur', async () => {
	const oncover = vi.fn(async () => {});
	draw({ coverHref: '/people/p1', coverAssetId: 'a1', mayEdit: true, oncover });

	reframeButton()?.click();
	flushSync();
	pictureLoads(1600, 900);
	for (let times = 0; times < 40; times += 1) press('se', 'ArrowLeft', true);
	saveButton()?.click();

	await vi.waitFor(() => expect(oncover).toHaveBeenCalled());
	const more = (oncover.mock.calls[0] as unknown as [string, null, { frame: Frame }])[2];
	expect(more.frame.w).toBeCloseTo(largestWide() / 4, 3);
	expect(more.frame.h).toBeCloseTo(1 / 4, 3);
	// Held at the corner that did not move, not jumped toward the pointer.
	expect([more.frame.x + more.frame.w / 2 < 0.5, more.frame.y]).toEqual([true, 0]);
});

/** The widest a 3:4 window on a 16:9 picture can be, as a fraction of the picture. */
function largestWide(): number {
	return 3 / 4 / (1600 / 900);
}

it('draws a Site network mark beside the name, and none for nought', () => {
	draw({ name: 'Northlight Group', sitesWithin: 4 });
	expect(host.querySelector('.network-mark')?.textContent).toContain('4 Sites within');
	unmount(drawn!);
	drawn = null;
	draw({ name: 'Northlight Group', sitesWithin: 0 });
	expect(host.querySelector('.network-mark')).toBeNull();
});

/* The cover's presses stand on a scrim over a picture. The shared wash is a layer over nothing, so
   under the pointer it would take the scrim away and leave the glyph bare on the picture: the layer
   is laid over the scrim here, for hover and for the press. */
it('keeps the scrim under a cover press when the pointer is on it', () => {
	const style = readFileSync('src/lib/components/entity/EntityCover.svelte', 'utf8');
	for (const press of ['pencil', 'remove-cover', 'reframe']) {
		expect(style).toContain(`.cover :global(.${press}:hover:not(:disabled))`);
		expect(style).toContain(`.cover :global(.${press}:active:not(:disabled))`);
	}
	expect(style).toContain(
		'background: color-mix(in srgb, currentColor var(--layer-hover), var(--sift-scrim));'
	);
	expect(style).toContain(
		'background: color-mix(in srgb, currentColor var(--layer-pressed), var(--sift-scrim));'
	);
});
