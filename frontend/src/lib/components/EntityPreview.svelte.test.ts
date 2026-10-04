/*
 * The card a chip in the file dialog opens.
 *
 * What matters is where the numbers come from and when they are asked for. The card must agree with
 * the page it links to (one request to the route that fills that page's tab strip), and must not
 * ask until somebody rests on a chip: a file with eight people on it would otherwise make eight
 * requests nobody wanted. The hover behaviour belongs to `LinkPreview` and is proved in its own
 * suite.
 *
 * The card is every entity's, so the same run covers a photo set too: the page, the route and the
 * tab list all come from the kind.
 */
import { flushSync, mount, unmount } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const asked = vi.fn();
vi.mock('$lib/api/client', () => ({
	api: {
		get: (path: string) => asked(path)
	},
	ApiError: class extends Error {}
}));

const Probe = (await import('./EntityPreviewProbe.test.svelte')).default;
const { entityPicture } = await import('./EntityPreview.svelte');

let instance: ReturnType<typeof mount> | null = null;

/** What the links route answers for whatever the card is about. Set per test. */
let boxes: { box_name: string }[] = [];

/** And who MADE it. Null is the ordinary case: somebody typed it in.
 *
 * One value for both addresses the maker can be recorded at, because `makerOf` asks exactly one of
 * them per kind and the card must draw the same line whichever it was. */
let made: {
	kind: string;
	via?: string | null;
	act?: string | null;
	box_id?: string | null;
	box_name?: string | null;
	box_slug?: string | null;
} | null = null;

/** Every address the card asked for, as `/related/...` or `/stash-boxes/...`. */
function askedFor(prefix: string): string[] {
	return asked.mock.calls.map((call) => String(call[0])).filter((path) => path.startsWith(prefix));
}

beforeEach(() => {
	asked.mockReset();
	boxes = [];
	made = null;
	/* Answered by ADDRESS, because the card asks two questions of two routes. A single canned
	   answer for both would make the second route look like it had answered when it had merely
	   been handed the first one's reply. */
	asked.mockImplementation((path: string) => {
		if (path.startsWith('/stash-boxes/links/'))
			return Promise.resolve({ links: boxes, made_by: made });
		// The maker of the two kinds no box can name, at the entity's own address. Answered here so
		// that a card asking the wrong route for a kind reads as no line rather than as the counts
		// reply being drawn as a maker.
		if (path.endsWith('/made-by')) return Promise.resolve(made);
		/* Two tabs answered, one the server has no number for. A null is NOT a nought: a nought is
		   a real answer (drawn as nothing), a null is a number not known (drawn as a dash). */
		return Promise.resolve({ files: 4, tags: 2, loops: null });
	});
});

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	document.body.innerHTML = '';
});

function put(props: Record<string, unknown> = {}): void {
	const host = document.createElement('div');
	document.body.appendChild(host);
	instance = mount(Probe, { target: host, props });
	flushSync();
}

async function hover(): Promise<void> {
	const link = document.querySelector('a.person');
	if (!(link instanceof HTMLElement)) throw new Error('the chip is not there');
	link.dispatchEvent(new MouseEvent('pointerenter', { bubbles: false }));
	await vi.waitFor(() => {
		flushSync();
		if (!document.querySelector('.preview')) throw new Error('no card yet');
	});
}

/** Every count in the card, as "tab -> what it says". */
function counts(): Record<string, string> {
	const out: Record<string, string> = {};
	for (const one of document.querySelectorAll('.counts .count')) {
		const tab = one.closest<HTMLElement>('[data-cell]')?.dataset.cell ?? '';
		out[tab] = (one.textContent ?? '').replace(/[\uE000-\uF8FF]/g, '').trim();
	}
	return out;
}

describe("the card on a person's face", () => {
	it('asks nothing until somebody rests on the face', () => {
		put();

		expect(asked).not.toHaveBeenCalled();
	});

	it('asks the one route that answers every tab, once', async () => {
		put();
		await hover();

		expect(askedFor('/related/')).toEqual(['/related/person/p-1']);
	});

	it('shows the name as a link to their page', async () => {
		put();
		await hover();

		const name = document.querySelector('.preview .name');
		if (!(name instanceof HTMLAnchorElement)) throw new Error('the name is not a link');
		expect(name.textContent?.trim()).toBe('Marisol Vane');
		expect(name.getAttribute('href')).toBe('/people/p-1');
	});

	it('draws a number per tab, each a way into that tab of their page', async () => {
		put();
		await hover();
		await vi.waitFor(() => {
			flushSync();
			if (!document.querySelector('.counts')) throw new Error('no numbers yet');
		});

		expect(counts()).toMatchObject({ files: '4', tags: '2' });

		const tags = document.querySelector('.counts [data-cell="tags"] .count');
		expect(tags?.getAttribute('aria-label')).toBe('Tags: 2');
		expect(tags?.getAttribute('href')).toBe('/people/p-1?show=tags');
	});

	it('draws nothing for a nought, rather than a bare 0', async () => {
		asked.mockImplementation((path: string) =>
			Promise.resolve(
				path.startsWith('/related/') ? { files: 1, tags: 0 } : { links: [], made_by: null }
			)
		);
		put();
		await hover();
		await vi.waitFor(() => {
			flushSync();
			if (!document.querySelector('.counts')) throw new Error('no numbers yet');
		});

		expect(counts().files).toBe('1');
		expect(counts()).not.toHaveProperty('tags');
	});

	it('names each tab on hover, because a glyph and a number say nothing on their own', async () => {
		/* Seven pairs of a shape and a figure, and which shape means Photo Sets rather than
		   Collections would be a thing to learn by opening one. The words are the tab's own, so a
		   person's own people wall reads "Seen with" here exactly as it does on their page. */
		put();
		await hover();
		await vi.waitFor(() => {
			flushSync();
			if (!document.querySelector('.counts')) throw new Error('no numbers yet');
		});

		const seenWith = [...document.querySelectorAll('.counts .count')].find((one) =>
			(one.getAttribute('aria-label') ?? '').startsWith('Seen with')
		);
		if (!(seenWith instanceof HTMLElement)) throw new Error('there is no seen-with count');
		const wrap = seenWith.closest('.wrap');
		if (!(wrap instanceof HTMLElement)) throw new Error('the count carries no tooltip');

		wrap.dispatchEvent(new MouseEvent('pointermove', { bubbles: true }));
		await vi.waitFor(() => {
			flushSync();
			const bubble = document.querySelector('[role="tooltip"]');
			if (!bubble) throw new Error('no label yet');
			expect(bubble.textContent?.trim()).toBe('Seen with');
		});
	});

	it('draws a tab the server had no number for without inventing a nought', async () => {
		put();
		await hover();
		await vi.waitFor(() => {
			flushSync();
			if (!document.querySelector('.counts')) throw new Error('no numbers yet');
		});

		// A nought is a real answer ("this person is on no loops") and drawing one where the
		// server said nothing would be the card claiming to know something it does not.
		expect(counts().loops).toBe('—');
	});
});

/*
 * Who has described this person, beside their name. The mark lives here and on the entity's own
 * page, and it names the box, since with several services configured "stash_box" alone sends
 * somebody through Settings to find out which one.
 */
describe('which stash-boxes know this person', () => {
	it('draws one mark per box and names each of them', async () => {
		boxes = [{ box_name: 'StashDB' }, { box_name: 'PMVStash' }];
		put();
		await hover();
		await vi.waitFor(() => {
			flushSync();
			if (document.querySelectorAll('.preview .mark').length !== 2) throw new Error('no marks');
		});

		expect(
			[...document.querySelectorAll('.preview .mark')].map((one) => one.getAttribute('aria-label'))
		).toEqual(['Enriched by Stash-box: StashDB', 'Enriched by Stash-box: PMVStash']);
	});

	it('asks the same route the page asks, so the two cannot disagree', async () => {
		put();
		await hover();
		await vi.waitFor(() => {
			flushSync();
			if (askedFor('/stash-boxes/').length === 0) throw new Error('not asked yet');
		});

		/* Twice, and both times the SAME address, which is what this is about. The card must read
		 * what the page reads, so that one person cannot be described two ways depending on whether
		 * somebody opened the page or rested on a chip.
		 *
		 * The second read is the maker's, and it is the price of the card asking `makerOf` for every
		 * kind instead of keeping its own list of which kinds record one, a list that would leave a
		 * collection and a Photo Set with no maker line here at all. The count is pinned rather than
		 * loosened so a third read cannot arrive unnoticed. */
		expect(askedFor('/stash-boxes/')).toEqual([
			'/stash-boxes/links/person/p-1',
			'/stash-boxes/links/person/p-1'
		]);
	});

	/*
	 * Who made them, as the page says: the line is the header's, drawn by the same component with
	 * the same word, so the card never disagrees with the page it opens.
	 */
	it('says which box invented this person, in the words the page uses', async () => {
		boxes = [{ box_name: 'StashDB' }];
		made = {
			kind: 'stash_box',
			via: 'stash',
			box_id: 'b-2',
			box_name: 'PMVStash',
			box_slug: 'pmvstash'
		};
		put();
		await hover();
		await vi.waitFor(() => {
			flushSync();
			if (!document.querySelector('.preview .made-by')) throw new Error('no maker yet');
		});

		const line = document.querySelector('.preview .made-by');
		expect(line?.textContent).toContain('Created by PMVStash');
		// Directly under the title, in the column beside the picture: the line after the name and
		// its marks, not a row of the card under the picture.
		expect(line?.parentElement?.classList.contains('title')).toBe(true);
		expect(line?.previousElementSibling?.classList.contains('said')).toBe(true);
		// CREATED by, not enriched by: the mark beside these words would otherwise make the opposite
		// claim to the words themselves, and the mark above it in the name row still says enriched.
		expect(line?.querySelector('.marks')?.getAttribute('aria-label')).toBe('Created by');
		expect(document.querySelector('.preview .said .marks')?.getAttribute('aria-label')).toBe(
			'Enriched by'
		);
	});

	it('says nothing about who made a person nothing recorded it for', async () => {
		// Most of a library. A line reading "Created by nobody" would be a claim; no line is the
		// honest absence of one, which is what the header does with the same answer.
		boxes = [{ box_name: 'StashDB' }];
		put();
		await hover();
		await vi.waitFor(() => {
			flushSync();
			if (document.querySelectorAll('.preview .mark').length !== 1) throw new Error('no marks');
		});

		expect(document.querySelector('.preview .made-by')).toBeNull();
	});

	it('draws nothing at all for a person no box has ever heard of', async () => {
		put();
		await hover();
		await vi.waitFor(() => {
			flushSync();
			if (askedFor('/stash-boxes/').length === 0) throw new Error('not asked yet');
		});
		flushSync();

		// Not an empty row and not a dash. Most people in most libraries have never been linked to
		// anything, and a mark saying so would be furniture on every card in the app.
		expect(document.querySelector('.preview .marks')).toBeNull();
	});
});

/*
 * The card is open while the band under it is re-read.
 *
 * A player re-reads its band on every library change, and a running task rings one about once a
 * second. Each re-read hands the chip a NEW row for the same person, so the card has to tell "the
 * same thing, asked again" from "a different thing" by what it is about, not by the object.
 */
describe('a card left open while its band is re-read', () => {
	function held(): { row: { id: string; name: string } } {
		const props = $state({ row: { id: 'p-1', name: 'Marisol Vane' } });
		const host = document.createElement('div');
		document.body.appendChild(host);
		instance = mount(Probe, { target: host, props });
		flushSync();
		return props;
	}

	async function numbersDrawn(): Promise<void> {
		await vi.waitFor(() => {
			flushSync();
			if (!document.querySelector('.counts')) throw new Error('no numbers yet');
		});
	}

	it('keeps its numbers when the row comes back for the same person', async () => {
		const props = held();
		await hover();
		await numbersDrawn();

		props.row = { id: 'p-1', name: 'Marisol Vane' };
		flushSync();

		expect(document.querySelector('.preview .bones')).toBeNull();
		expect(counts()).toMatchObject({ files: '4', tags: '2' });
		expect(askedFor('/related/')).toEqual(['/related/person/p-1']);
	});

	it('asks again, rather than sitting on bones, when the open card is handed somebody else', async () => {
		const props = held();
		await hover();
		await numbersDrawn();

		props.row = { id: 'p-2', name: 'Wren Halloway' };
		flushSync();
		await numbersDrawn();

		expect(askedFor('/related/')).toEqual(['/related/person/p-1', '/related/person/p-2']);
		expect(document.querySelector('.preview .name')?.getAttribute('href')).toBe('/people/p-2');
	});
});

/*
 * The same card about something that is not a person.
 *
 * A photo set rather than a collection, because its address is the one that is not simply its kind
 * with an `s` on the end (`photo_set` is filed at `/photo-sets`) so a card that spelt the
 * address from the kind by hand would be caught here.
 */
describe('the same card about a photo set', () => {
	it('asks the route for THAT kind, and links the name to that page', async () => {
		put({ kind: 'photo_set', id: 's-1', name: 'Beach morning' });
		await hover();

		/*
		 * Two requests, and neither of them a stash-box: no stash-box has heard of a photo set,
		 * which is asserted below. The maker is recorded for all five kinds and read at the kind's
		 * own address.
		 */
		expect(asked.mock.calls.map((call) => String(call[0])).sort()).toEqual([
			'/photo-sets/s-1/made-by',
			'/related/photo_set/s-1'
		]);
		expect(askedFor('/stash-boxes/')).toEqual([]);

		const name = document.querySelector('.preview .name');
		if (!(name instanceof HTMLAnchorElement)) throw new Error('the name is not a link');
		expect(name.getAttribute('href')).toBe('/photo-sets/s-1');
	});

	/*
	 * WHO MADE THE SET.
	 *
	 * A maker read off the stash-box answer would be read for exactly the three kinds a box can
	 * name, leaving a collection and a Photo Set with the line on their own page and nothing under
	 * the pointer. A Photo Set is the case worth pinning because most of them are Sift's own work: the
	 * folder pass makes them, so "Created by Sift, from a folder name" is the sentence somebody
	 * actually meets, and it is the header's sentence word for word rather than a second spelling.
	 */
	it('says who made the set, in the words the page uses', async () => {
		made = { kind: 'sift', via: 'folder' };
		put({ kind: 'photo_set', id: 's-1', name: 'Beach morning' });
		await hover();
		await vi.waitFor(() => {
			flushSync();
			if (!document.querySelector('.preview .made-by')) throw new Error('no maker yet');
		});

		expect(document.querySelector('.preview .made-by')?.textContent).toContain(
			'Created by Sift, from a folder name'
		);
	});

	it('draws a tag Sift made for a copy in the mark of the act that made the copy', async () => {
		made = { kind: 'sift', via: 'produced', act: 'compress' };
		put({ kind: 'tag', id: 't-1', name: 'Smaller' });
		await hover();
		await vi.waitFor(() => {
			flushSync();
			if (!document.querySelector('.preview .made-by')) throw new Error('no maker yet');
		});

		const line = document.querySelector('.preview .made-by');
		expect(line?.textContent).toContain('Created by Sift, from a file it compressed');
		expect(line?.querySelector('.mark')?.getAttribute('aria-label')).toBe(
			'Created by Sift: from a file it compressed'
		);
	});

	it('draws that kind of page own tabs, and no tab for its own kind', async () => {
		// A photo set has Files, People, Tags and Sites, and its Files wall is called Pictures
		// there, which is the tab strip's own word rather than a second vocabulary for this card.
		put({ kind: 'photo_set', id: 's-1', name: 'Beach morning' });
		await hover();
		await vi.waitFor(() => {
			flushSync();
			if (!document.querySelector('.counts')) throw new Error('no numbers yet');
		});

		const tabs = Object.keys(counts());
		expect(tabs).toEqual(['files', 'people', 'tags', 'sites']);
		expect(counts().files).toBe('4');
	});
});

/*
 * Which picture a thing is drawn as, which the chip and the card must not answer twice.
 *
 * The rule is exported rather than kept private because the band imports it: a chip wearing a cover
 * beside a card wearing a monogram is one thing drawn as two.
 */
describe('the picture a thing is drawn as', () => {
	it("is the ENTITY's own cover address, never the file behind it", () => {
		// An uploaded cover has no file behind it, and a cover naming a moment of a video is not the
		// frame the file itself is drawn as, so the file's address would be the wrong picture twice.
		expect(entityPicture('person', 'p-1', 'Marisol Vane').src).toBe('/api/people/p-1/cover');
		expect(entityPicture('photo_set', 's-1', 'Beach morning').src).toBe(
			'/api/photo-sets/s-1/cover'
		);
	});

	it('answers for every kind, because every chip in the file dialog asks', () => {
		/* One opinion about which picture a thing wears, for all five kinds: the thing's own cover
		   address, and `Avatar`'s monogram behind it where the answer is a 404. A tag has a route
		   for a cover too, so it gets the same treatment rather than being the one chip in the row
		   with nothing in front of the word. */
		expect(entityPicture('collection', 'c-1', 'Keepers').src).toBe('/api/collections/c-1/cover');
		expect(entityPicture('tag', 't-1', 'rooftop').src).toBe('/api/tags/t-1/cover');
		// The name travels with it, because that is what the letter behind a missing cover is drawn
		// from: a chip's own label is a snippet and cannot stand in for it.
		expect(entityPicture('tag', 't-1', 'rooftop').name).toBe('rooftop');
	});

	it('has no second address behind a cover: a site with nothing chosen shows its shipped logo or its letter', () => {
		// A site's cover address answers with the shipped logo when nobody has chosen a picture, and
		// 404 where the pack has none: the letter stands behind that. Nothing is fetched from the
		// site for a mark, so there is no second address to fall to, for a site or anything else.
		expect(entityPicture('site', 'pf-1', 'Bramblecast').instead).toBeNull();
		expect(entityPicture('site', 'pf-2', 'Quillhaven').instead).toBeNull();
		expect(entityPicture('collection', 'c-1', 'Bramblecast').instead).toBeNull();
	});
});
