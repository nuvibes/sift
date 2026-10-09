/*
 * One door, not a strip: the page's verbs are behind one worded trigger, none a button of its own.
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

/* The record's disclosure watched, not replaced, so its pace can be read. */
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
	/*
	 * The bands are remembered in the browser's store, which one jsdom session shares; cleared each
	 * test.
	 */
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

/* Any button saying this: an icon adds its ligature to `textContent`, which does not trim away. */
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
	/* Edit keeps the row, so this reaches the guard on the door itself. */
	draw({
		options: [],
		mayEdit: true,
		record: createRawSnippet(() => ({ render: () => '<p></p>' }))
	});

	expect(saysAnything('Edit')).toBe(true);
	expect(saysAnything('Options')).toBe(false);
});

/* The labelled facts stand beside the name with the rest of the record shut. */
it('draws the labelled facts beside the name, with the rest of the record shut', () => {
	draw({
		facts: createRawSnippet(() => ({ render: () => '<p class="probe-facts">Facts</p>' })),
		record: createRawSnippet(() => ({ render: () => '<p class="probe-record">Record</p>' }))
	});
	expect(host.querySelector('.facts .probe-facts')).not.toBeNull();
	expect(host.querySelector('.about .probe-facts')).toBeNull();
	expect(host.querySelector('.probe-record')).toBeNull();
});

/* More opens the record inside the fold `reveal` moves. */
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
	/* At `--dur-base` (fallback 200), a step quicker than a disclosure. */
	expect(vi.mocked(reveal)).toHaveBeenCalled();
	const [, options] = vi.mocked(reveal).mock.calls[0];
	expect(options).toEqual({ pace: 'base' });
	expect(vi.mocked(reveal).mock.results[0].value.duration).toBe(200);
});

/* The O tally as the drop and its figure. */
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

/* Putting the band away, asked and remembered, the way back in the same row. */
function collapser(): HTMLButtonElement | undefined {
	return [...host.querySelectorAll('button')].find((one) =>
		(one.getAttribute('aria-label') ?? '').endsWith('the details')
	);
}

it('offers a way to put the band away, on a page with no options and nothing to edit', () => {
	// A guest reading a tag still gets the row and the control.
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

	/* Held hidden rather than removed, so it can animate; `aria-hidden` and the class are what jsdom
	 * can see. */
	expect(band()?.getAttribute('aria-hidden')).toBe('true');
	expect(band()?.classList.contains('open')).toBe(false);
	expect(host.querySelector('h1')).not.toBeNull();

	// ...and the control that did it is still there, in the row it was in, outside the band.
	expect(collapser()).toBeDefined();
	expect(saysAnything('Options')).toBe(true);
	expect(band()?.contains(collapser()!)).toBe(false);
});

it('keeps the controls out of the band, so pressing the control cannot move it', () => {
	/* The controls do not move when the band goes: the row is outside the hero in both states. */
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

	// Still on the title row, outside the band.
	expect(host.querySelector('.page-header .controls')).not.toBeNull();
	expect(host.querySelector('.band')?.contains(host.querySelector('.page-header')!)).toBe(false);
	expect(host.querySelector('.band .controls')).toBeNull();
});

it('shows the act on the glyph rather than the state', () => {
	/* Arrows out while the band shows, in once it is gone; read off the codepoint, as the font is
	 * subset with ligatures off. */
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
	/* A person's strength and waiting faces are asked only once the band first opens. */
	localStorage.setItem('sift.entity.compact.entity', 'yes');
	draw({
		options: VERBS,
		underCover: createRawSnippet(() => ({ render: () => '<p>Identifies them well</p>' }))
	});

	expect(host.querySelector('.cover-column p')).toBeNull();

	collapser()!.click();
	flushSync();
	expect(host.querySelector('.cover-column p')?.textContent).toBe('Identifies them well');

	/* And latched, so collapsing and expanding asks nothing again. */
	collapser()!.click();
	flushSync();
	expect(host.querySelector('.cover-column p')).not.toBeNull();
});

it('draws the other page band under the PICTURE, in the cover column', () => {
	/* Under the picture, a block spends ground nothing was using. */
	draw({
		underCover: createRawSnippet(() => ({ render: () => '<p>13 confirmed</p>' }))
	});

	expect(host.querySelector('.cover-column > p')?.textContent).toBe('13 confirmed');
	expect(host.querySelector('.about p'), 'it landed in the name column').toBeNull();
});

it('is drawn exactly as it always was where a page passes no band at all', () => {
	/* Every other entity page passes neither snippet. */
	draw({});

	const column = host.querySelector('.cover-column');
	expect(column?.children.length, 'something was added to the cover column').toBe(1);
	expect(column?.firstElementChild?.classList.contains('cover')).toBe(true);
});

it('and every other entity page really does pass no band', () => {
	/* Read off the pages themselves; `underTags` is listed because no such slot exists. */
	for (const page of ['tags', 'collections', 'photo-sets', 'sites']) {
		const source = readFileSync(`src/routes/${page}/[id]/+page.svelte`, 'utf8');
		expect(source, `${page} does not draw the shared header`).toContain('EntityHeader');
		expect(source, `${page} passes a header band`).not.toContain('underCover');
		expect(source, `${page} passes the slot that was replaced`).not.toContain('underTags');
	}
});

/* Who else has described this, naming each box, beside the heart and stars. */
it('names each stash-box that knows this thing, beside the heart and the stars', () => {
	draw({
		favorite: false,
		onfavorite: () => {},
		enrichedBy: [
			{ name: 'StashDB', box: 'stashdb' },
			{ name: 'PMVStash', box: 'pmvstash' }
		]
	});

	/* Through the group `EnrichmentMarks` announces, since the rating's button is also `.mark`. */
	const group = host.querySelector('.opinions [aria-label="Enriched by"]');
	const marks = [...(group?.querySelectorAll('.mark') ?? [])];
	/* "Enriched by " and the column's own row, word for word. */
	expect(marks.map((one) => one.getAttribute('aria-label'))).toEqual([
		'Enriched by Stash-box: StashDB',
		'Enriched by Stash-box: PMVStash'
	]);
});

it('draws the marks even where this kind of thing holds no opinion of its own', () => {
	/* A handle has no heart or stars, but the row stands for the marks alone. */
	draw({ enrichedBy: [{ name: 'StashDB', box: 'stashdb' }] });

	expect(host.querySelectorAll('.opinions .mark')).toHaveLength(1);
	expect(host.querySelector('.opinions button')).toBeNull();
});

it('draws no row at all for a thing nothing has described and nobody may rate', () => {
	draw({});

	expect(host.querySelector('.opinions')).toBeNull();
});

/* Who made it, a different fact from who described it, on a line of its own. */
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

/* That line says Created by, in the tooltip and the group's label. */
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

	// The row above is untouched.
	const described = host.querySelector('.opinions .marks');
	expect(described?.getAttribute('aria-label')).toBe('Enriched by');
	expect(described?.querySelector('.mark')?.getAttribute('aria-label')).toBe(
		'Enriched by Stash-box: StashDB'
	);
});

/* The makers that are not a box: the pass, with its glyph from the Enriched by table. */
it('says which of its own passes made a thing, with that pass mark beside it', () => {
	draw({
		madeBy: { kind: 'sift', via: 'folder', box_id: null, box_name: null, box_slug: null }
	});

	const line = host.querySelector('.made-by');
	expect(line?.textContent).toContain('Created by Sift, from a folder name');
	expect(line?.querySelector('.mark')).not.toBeNull();
});

it('draws a tag Sift made for a copy in the glyph and words of the act that made the copy', () => {
	// Each copy's tag wears its act's own verb glyph.
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
	/* No glyph for a person. */
	draw({ madeBy: { kind: 'you', via: null, box_id: null, box_name: null, box_slug: null } });
	const line = host.querySelector('.made-by');
	expect(line?.textContent).toContain('Created by you');
	expect(line?.querySelector('.mark')).toBeNull();
});

it('never names another account, only that one made it', () => {
	/* An account that is not the asker's is withheld; the server picks the word. */
	draw({
		madeBy: { kind: 'another_user', via: null, box_id: null, box_name: null, box_slug: null }
	});
	expect(host.querySelector('.made-by')?.textContent).toContain('Created by another user');
});

it('says nothing at all about who made a thing nothing recorded', () => {
	/* No maker recorded draws no line. */
	draw({ enrichedBy: [{ name: 'StashDB', box: 'stashdb' }] });

	expect(host.querySelector('.made-by')).toBeNull();
});

/* The kind's rail glyph inside the heading, before the name. */
it('draws the kind glyph inside the heading, before the name', () => {
	draw({ icon: 'shoppingmode' });

	const heading = host.querySelector('h1');
	expect(heading).not.toBeNull();
	const mark = heading?.querySelector('.icon');
	expect(mark).not.toBeNull();
	// `Icon` renders the codepoint's character.
	expect(mark?.textContent).toBe(String.fromCodePoint(parseInt(codepoints.shoppingmode, 16)));
	// Hidden from anything reading the page out: the name beside it already says what this is.
	expect(mark?.getAttribute('aria-hidden')).toBe('true');
});

/* No column beside the name until the record shows; waiting questions live on History. */
it('draws no column beside the name until the record itself is showing', () => {
	draw({
		record: createRawSnippet(() => ({ render: () => '<p data-record>Born somewhere</p>' }))
	});

	// The band starts shut, so the More control is the way in and nothing is beside the name yet.
	expect(host.querySelector('.facts')).toBeNull();
	expect(host.querySelector('[data-record]')).toBeNull();
});

/* The screen's ground is the header's own picture, through BandHarness: absent with no picture,
 * gone with the header. */
it('stands the screen on the same picture the header draws beside the name', () => {
	const host2 = document.createElement('div');
	document.body.append(host2);
	const band = mount(BandHarness, { target: host2, props: { coverAssetId: 'a-1' } });
	flushSync();

	const backdrop = host2.querySelector('.frame-backdrop');
	const picture = backdrop?.querySelector('img');
	expect(picture?.getAttribute('src')).toBe('/api/assets/a-1/thumb');

	// The same address, so one request and one picture.
	const drawnSources = [...host2.querySelectorAll('img')].map((one) => one.getAttribute('src'));
	expect(drawnSources.filter((src) => src === '/api/assets/a-1/thumb').length).toBe(2);

	// It is a ground: nothing to read out, and nothing to press.
	expect(backdrop?.getAttribute('aria-hidden')).toBe('true');
	expect(picture?.getAttribute('alt')).toBe('');

	// It goes with the header, or a face would stay behind another page.
	unmount(band);
	flushSync();
	expect(host2.querySelector('.frame-backdrop')).toBeNull();
	host2.remove();
});

it('draws no ground at all for a thing with no picture', () => {
	// No picture of any kind.
	const host2 = document.createElement('div');
	document.body.append(host2);
	const band = mount(BandHarness, { target: host2, props: { counts: 'On 12 items' } });
	flushSync();

	expect(host2.querySelector('.frame-backdrop')).toBeNull();

	unmount(band);
	host2.remove();
});

/* A shipped pack picture is a mark, whole and never cropped, from either source. */
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

/* Cancel then Save while editing, matching the form's foot (RecordForm.svelte.test.ts). */
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

	/* Save is the form's submit from outside it, or it would be wired to nothing. */
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

/* Taking a chosen cover off: when the control exists, the question, and the Undo. */
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
	// The cover's delete icon bottom-left, away from the pencil.
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

/** Give the measuring picture a shape, as jsdom loads none. */
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

	// The editor's eight grips and middle, nothing of a separate framer.
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

/* The cover's presses keep the scrim under them on hover and press. */
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
