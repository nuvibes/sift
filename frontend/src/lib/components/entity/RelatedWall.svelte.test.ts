/*
 * One tab's wall on an entity page: its addresses and words, each of which can be wrong while
 * drawing perfectly, and a left tab's answer never landing under the tab now chosen.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createRawSnippet, flushSync, mount, unmount, tick, type ComponentProps } from 'svelte';

import { api } from '$lib/api/client';
import { loadRelated, ordersFor, tabSort, type RelatedRow } from '$lib/entity/related.svelte';
import { forgetMeasurements } from '$lib/grid/cards.svelte';
import { measuring } from '$lib/grid/measuring';
import { ableTo, ordersOffered, screenBar } from '$lib/components/shell/screen-bar.svelte';
import { openAsset } from '$lib/player/asset-view';
import { readFileSync } from 'node:fs';

import { page } from '$app/state';
import { goto } from '$app/navigation';

import RelatedWall from './RelatedWall.svelte';

vi.mock('$lib/entity/related.svelte', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/entity/related.svelte')>()),
	loadRelated: vi.fn()
}));
vi.mock('$lib/player/asset-view', () => ({ openAsset: vi.fn() }));
/* Every request this file makes is answered here, immediately. The Loops tab draws the media grid, which
   asks the server for its page; a real request would answer during whichever test is running when
   it comes back, or after the last, and the grid would reach different code on each run. */
const ONE_MARK = vi.hoisted(() => ({
	items: [{ id: 'm1', media_type: 'video', width: 1920, height: 1080, duration_ms: 4000 }],
	total: 1,
	offset: 0,
	limit: 48,
	complete: true
}));
vi.mock('$lib/api/client', async (importOriginal) => {
	const real = await importOriginal<typeof import('$lib/api/client')>();
	const unwritten = async (path: string) => {
		throw new Error(`this file writes no answer for ${path}`);
	};
	return {
		...real,
		api: {
			...real.api,
			get: vi.fn(async (path: string) => (path === '/loops' ? ONE_MARK : unwritten(path))),
			post: vi.fn(unwritten),
			put: vi.fn(unwritten),
			del: vi.fn(unwritten)
		}
	};
});

const load = vi.mocked(loadRelated);
const opened = vi.mocked(openAsset);

let host: HTMLElement;
let instance: Record<string, unknown> | null = null;

function row(overrides: Partial<RelatedRow> = {}): RelatedRow {
	return { id: 'r1', name: 'beach', ...overrides };
}

function answered(items: RelatedRow[], total = items.length) {
	return { items, total };
}

type Showing =
	'tags' | 'people' | 'sites' | 'sites_within' | 'collections' | 'photo_sets' | 'loops';

/* The props are `$state` so a test can move the wall to another tab the way pressing one does.
 * Mounted with a plain object the component would hold the values it was given for ever, and the
 * rising counter, which only ever matters when the tab CHANGES, could not be reached at all. */
async function draw(showing: Showing, extra: Record<string, unknown> = {}) {
	host = document.createElement('div');
	document.body.append(host);
	const props: ComponentProps<typeof RelatedWall> = $state({
		on: 'person',
		id: 'p1',
		showing,
		title: showing === 'people' ? 'Seen with' : 'Tags',
		icon: 'shoppingmode',
		...extra
	});
	instance = mount(RelatedWall, { target: host, props });
	flushSync();
	await settle();
	return props;
}

/** Let the fetch inside the effect resolve and the markup catch up with it. */
async function settle() {
	await tick();
	await tick();
	flushSync();
}

beforeEach(() => {
	vi.resetAllMocks();
	load.mockResolvedValue(answered([]));
});

afterEach(() => {
	if (instance) void unmount(instance, { outro: false });
	instance = null;
	host?.remove();
	document.body.innerHTML = '';
});

function links(): HTMLAnchorElement[] {
	return [...host.querySelectorAll('a[href]')] as HTMLAnchorElement[];
}

describe('where a card leads', () => {
	it('sends every other kind to its own page', async () => {
		load.mockResolvedValue(answered([row({ id: 't7' })]));

		await draw('tags');

		expect(links().map((link) => link.getAttribute('href'))).toContain('/tags/t7');
	});

	/*
	 * One click, one rule: a thing reached through something else opens carrying that something
	 * else as a filter, so the wall you land on is the two together and the bar says so. A plain
	 * link would discard what made the card worth pressing from where it was pressed.
	 */
	it('carries the page you came from onto a person, as a filter', async () => {
		load.mockResolvedValue(answered([row({ id: 'p2', name: 'Jane Else' })]));

		await draw('people', { named: 'Jane Doe' });

		expect(links().map((link) => link.getAttribute('href'))).toContain(
			'/people/p2?people=Jane+Doe'
		);
	});

	it("carries a tag's own name onto the person pressed on its People tab", async () => {
		load.mockResolvedValue(answered([row({ id: 'p2', name: 'Jane Else' })]));

		await draw('people', { on: 'tag', id: 't1', named: 'beach' });

		expect(links().map((link) => link.getAttribute('href'))).toContain('/people/p2?tags=beach');
	});

	it('carries a person onto the site pressed on their Sites tab', async () => {
		load.mockResolvedValue(answered([row({ id: 's3', name: 'Another Studio' })]));

		await draw('sites', { named: 'Jane Doe' });

		expect(links().map((link) => link.getAttribute('href'))).toContain('/sites/s3?people=Jane+Doe');
	});

	it('leaves a site inside a network a plain link', async () => {
		// The one tab here that is not about this thing's FILES: a site is part of a network because
		// of a column on its row, so the network as a filter on the member's wall asks a different
		// question and on most libraries an empty one.
		load.mockResolvedValue(answered([row({ id: 's4', name: 'Another Studio' })]));

		await draw('sites_within', { on: 'site', id: 's1', named: 'Another Studio' });

		expect(links().map((link) => link.getAttribute('href'))).toContain('/sites/s4');
	});

	it('leaves a collection a plain link, because its wall takes no query', async () => {
		// A collection's Files wall is the arrangement somebody made by hand and says on the bar
		// that there is nothing to filter in it. A filter sent there is a chip nothing can act on.
		load.mockResolvedValue(answered([row({ id: 'c5', name: 'beach' })]));

		await draw('collections', { named: 'Jane Doe' });

		expect(links().map((link) => link.getAttribute('href'))).toContain('/collections/c5');
	});

	/*
	 * A Site's People wall counts people with only a username on it, whose picture would pick a
	 * filter that empties the Files tab. A card whose number here is nought opens its own page
	 * instead, without this page carried onto it. A card with files here keeps both the pick and
	 * the carried filter.
	 */
	it('opens somebody with nothing here at their own page, and picks only those with files', async () => {
		load.mockResolvedValue(
			answered([
				row({ id: 'p2', name: 'Jane Else', asset_count: 0 }),
				row({ id: 'p3', name: 'Neve Alder', asset_count: 4 })
			])
		);

		await draw('people', { on: 'site', id: 's1', named: 'Sunsetter' });

		const hrefs = links().map((link) => link.getAttribute('href'));
		expect(hrefs).toContain('/people/p2');
		expect(hrefs).not.toContain('/people/p2?sites=Sunsetter');
		expect(hrefs).toContain('/people/p3?sites=Sunsetter');
		expect(host.querySelector('[aria-label="Filter the files to Jane Else"]')).toBeNull();
		expect(host.querySelector('[aria-label="Filter the files to Neve Alder"]')).not.toBeNull();
	});

	it('carries nothing while the page has not said its name yet', async () => {
		// An address with an empty filter on it draws a chip for nobody.
		load.mockResolvedValue(answered([row({ id: 'p2', name: 'Jane Else' })]));

		await draw('people', { named: '' });

		expect(links().map((link) => link.getAttribute('href'))).toContain('/people/p2');
	});
});

/*
 * A wall of marks is not a wall of cards: loops are drawn by the media grid, the one Browse uses,
 * so nothing fetches through the related list and there is no card to press. Held as a test so a
 * card coming back on this tab fails.
 */
describe('a wall of marks is the media grid', () => {
	it('draws no entity cards at all', async () => {
		load.mockResolvedValue(answered([row({ asset_id: 'a1', duration_ms: 4000 })]));

		await draw('loops');

		// No card, so no link to it either: neither the words of a card nor where one would lead.
		expect(links()).toHaveLength(0);
		// A card's rendering of a loop; if this word ever comes back, so has the card.
		expect(host.textContent).not.toContain('Untitled loop');
		/*
		 * Asserted on the card, the thing whose absence is claimed, and not on `.wall`:
		 * `EntityGrid`, `AssetGrid` and `TheaterWall` all name their container `wall`, so "no
		 * `.wall`" would assert that the media grid did not draw. Paired with the positive, a media
		 * tile on screen, because an absence alone also passes against a wall that drew nothing.
		 */
		expect(host.querySelector('.card'), 'an entity card was drawn').toBeNull();
		// The control, and it is a pair rather than one selector. A grid DID render, and it drew no
		// card, which the entity grid could not have done, since it was handed a row and a row is
		// what it makes a card out of. Asserting the absences alone passes just as well against a
		// component that rendered nothing.
		expect(host.querySelector('.wall'), 'no grid rendered at all').not.toBeNull();
		// And it is the media grid that drew it: it asked the marks' own list for its page.
		await vi.waitFor(() =>
			expect(vi.mocked(api.get)).toHaveBeenCalledWith('/loops', expect.anything())
		);
	});

	it("tells the bar the page by NAME, so the panel counts this page's marked files", async () => {
		/* The route is asked by id; the bar counts and keeps filters in the file language, which reads
		   names. Handed the id, the panel over a person's Loops tab would count every marked file
		   in the library. */
		load.mockResolvedValue(answered([]));

		await draw('loops', { named: 'Jane Doe' });

		expect(ableTo(screenBar.tools.filterable), 'the Loops tab offered no panel').toBe(true);
		expect(screenBar.tools.query).toEqual({ loops: 'any', people: 'Jane Doe' });
	});
});

describe('what a card says', () => {
	it('counts a set in pictures and a tag in files, and says one of them singly', async () => {
		load.mockResolvedValue(answered([row({ item_count: 1 })]));
		await draw('photo_sets');
		expect(host.textContent).toContain('1 picture');
		expect(host.textContent).not.toContain('1 pictures');

		if (instance) void unmount(instance, { outro: false });
		host.remove();
		load.mockResolvedValue(answered([row({ asset_count: 3 })]));
		await draw('tags');
		expect(host.textContent).toContain('3 files');
	});

	it('groups a large count the way every other count on the card is grouped', async () => {
		load.mockResolvedValue(answered([row({ asset_count: 3007 })]));
		await draw('people', { on: 'site' });
		expect(host.textContent).toContain(`${(3007).toLocaleString()} files from this Site`);
		expect(host.textContent).not.toContain('3007 files');
	});
});

describe("a site's own logo on a tab", () => {
	/*
	 * A person's Sites tab draws the logo for sites the shipped pack has one for, as the Sites wall
	 * does: one card, one answer. The field comes down on every site row (`SiteView.icon`), and
	 * this wall has to pass it on in its own projection of a row, or the tab draws a LETTER.
	 *
	 * The picture's CLASS is what is asserted, because that is what says which kind of picture it is:
	 * a pack logo is shown whole (`mark`) and a chosen cover is cropped to the card. See
	 * `EntityCard.svelte.test.ts`, which holds the same two cases at the card's own door.
	 */
	it('draws the pack logo for a site the pack knows', async () => {
		load.mockResolvedValue(answered([row({ icon: '0.1.171-abc' })]));
		await draw('sites');

		expect(host.querySelector('img.picture')?.classList.contains('mark')).toBe(true);
	});

	it('leaves a site the pack has never heard of with its letter', async () => {
		load.mockResolvedValue(answered([row({ icon: null })]));
		await draw('sites');

		expect(host.querySelector('img.picture')).toBeNull();
	});
});

describe('an empty wall', () => {
	it('names what was looked for rather than saying nothing reaches anything', async () => {
		await draw('tags');

		expect(host.textContent).toContain('Nothing here carries tags yet.');
	});

	it("says the seen-with wall's own sentence, because 'carries people' is not English", async () => {
		await draw('people');

		expect(host.textContent).toContain('Nobody else turns up on these files.');
	});
});

describe('the number beside the tab', () => {
	it('reports the total from the same answer the cards came from', async () => {
		const counted = vi.fn();
		load.mockResolvedValue(answered([row()], 12));

		await draw('tags', { oncount: counted });

		expect(counted).toHaveBeenCalledWith(12, false);
	});
});

describe('the bar above it', () => {
	/*
	 * The size slider works on every tab, not only Files.
	 *
	 * The wall honours the chosen notch (the cards genuinely resize), and the bar has to be told
	 * so, or the control above every non-Files tab is drawn dimmed with the general "nothing to
	 * resize on this screen" beside it. Perfectly consistent, and a lie.
	 *
	 * Asserted through `ableTo`, which is what the bar itself asks, rather than against the raw
	 * value: the field is `true` or a REASON, and a test comparing it to `true` by hand would be a
	 * second reader of that rule.
	 */
	it('is told these cards can be resized', async () => {
		await draw('tags');

		expect(ableTo(screenBar.tools.resizable)).toBe(true);
	});

	it('is told why the query language and the preview do not apply', async () => {
		await draw('tags');

		// Dimmed WITH A REASON, never absent: a control that vanishes reads as a feature that does
		// not exist. Both say what they are about rather than falling back to the general wording.
		expect(ableTo(screenBar.tools.filterable)).toBe(false);
		expect(screenBar.tools.filterable).toContain('tags');
		expect(ableTo(screenBar.tools.playable)).toBe(false);
		expect(typeof screenBar.tools.playable).toBe('string');
	});
});

describe('the wall pages', () => {
	/*
	 * A guard against silent truncation: the heading draws the scoped total, so the wall must page
	 * rather than stop at a first page. Asserted as "it asks with a limit and an offset, and draws
	 * the control that moves them" rather than against a page size: the size is measured off a real
	 * card and its scrolling box, which in jsdom is zero, so a pinned number would pin only the
	 * fallback.
	 */
	it('asks for a page rather than for whatever the default is', async () => {
		load.mockResolvedValue(answered([row()], 200));

		await draw('tags');

		expect(load).toHaveBeenCalledWith(
			'person',
			'p1',
			'tags',
			expect.objectContaining({ limit: expect.any(Number), offset: 0 })
		);
	});

	it('draws the way to the rest of them, and says how far along it is', async () => {
		load.mockResolvedValue(answered([row()], 200));

		await draw('tags');

		// The shared pager's own words. A wall that draws sixty of two hundred and says nothing is
		// the fault; this is the sentence that makes it impossible.
		expect(host.textContent).toContain('of 200');
		expect(host.querySelector('[aria-label="Next page"]')).not.toBeNull();
	});

	it('goes back to the first page when the tab changes', async () => {
		load.mockResolvedValue(answered([row()], 200));
		const props = await draw('tags');

		const next = host.querySelector('[aria-label="Next page"]') as HTMLButtonElement;
		next.click();
		flushSync();
		await settle();
		const turned = load.mock.lastCall?.[3] as { offset: number };
		expect(turned.offset).toBeGreaterThan(0);

		props.showing = 'people';
		props.title = 'Seen with';
		flushSync();
		await settle();

		// A different tab is a different list, so page four of the last one means nothing in it.
		expect(load).toHaveBeenLastCalledWith(
			'person',
			'p1',
			'people',
			expect.objectContaining({ offset: 0 })
		);
	});
});

describe('a slower answer for a tab somebody has left', () => {
	it('cannot land under the tab they are on now', async () => {
		// The only way this is ever seen is a slow server, so it is staged rather than waited for.
		let release: ((page: { items: RelatedRow[]; total: number }) => void) | null = null;
		load.mockImplementationOnce(
			() => new Promise((resolve) => (release = resolve)) as ReturnType<typeof loadRelated>
		);

		const props = await draw('tags');
		expect(release).not.toBeNull();

		// Moved to another tab; its own answer lands immediately.
		load.mockResolvedValue(answered([row({ id: 'p9', name: 'somebody' })]));
		props.showing = 'people';
		props.title = 'Seen with';
		flushSync();
		await settle();

		// And now the wall somebody has left finally answers.
		release!(answered([row({ id: 't1', name: 'a stale tag' })]));
		await settle();

		expect(host.textContent).not.toContain('a stale tag');
		expect(host.textContent).toContain('somebody');
	});
});

describe('a tab somebody moves to', () => {
	it("never lays out the previous tab's rows as its own cards, even for one frame", async () => {
		// The person on the Seen with tab; the Sites tab's own answer has not landed yet.
		load.mockResolvedValue(answered([row({ id: 'p7', name: 'somebody seen' })]));
		const props = await draw('people');
		expect(host.textContent).toContain('somebody seen');
		load.mockImplementation(() => new Promise(() => {}) as ReturnType<typeof loadRelated>);

		// Every link the move draws is recorded, so a card drawn and then emptied is still seen.
		const drawnAsSites: string[] = [];
		const watcher = new MutationObserver((records) => {
			for (const record of records) {
				// A keyed card is kept and re-pointed rather than added again, so a changed link
				// counts as much as a new one.
				const touched = record.type === 'attributes' ? [record.target] : [...record.addedNodes];
				for (const node of touched) {
					if (!(node instanceof Element)) continue;
					for (const one of [node, ...node.querySelectorAll('[href], [src]')]) {
						const where = one.getAttribute('href') ?? one.getAttribute('src') ?? '';
						if (where.includes('/sites/p7')) drawnAsSites.push(where);
					}
				}
			}
		});
		watcher.observe(host, {
			childList: true,
			subtree: true,
			attributes: true,
			attributeFilter: ['href', 'src']
		});

		props.showing = 'sites';
		props.title = 'Sites';
		flushSync();
		await tick();
		watcher.disconnect();

		// A person's id under the Sites tab is a link to a Site that does not exist and a cover
		// the server answers 404 for. The card is still on screen while the Sites answer is coming,
		// and it is still a person's card, out of reach.
		expect(drawnAsSites).toEqual([]);
		expect(host.textContent).toContain('somebody seen');
		expect(links().some((one) => one.getAttribute('href')?.startsWith('/people/p7'))).toBe(true);
		const wall = host.querySelector<HTMLElement>('.wall.held');
		expect(wall?.inert || wall?.hasAttribute('inert')).toBe(true);
	});

	it('keeps the last answer on screen until the next lands, never an empty or waiting wall', async () => {
		load.mockResolvedValue(answered([row({ id: 'p7', name: 'somebody seen' })]));
		const props = await draw('people');
		let release: ((page: { items: RelatedRow[]; total: number }) => void) | null = null;
		load.mockImplementation(
			() => new Promise((resolve) => (release = resolve)) as ReturnType<typeof loadRelated>
		);

		// What the wall held after every change, so a frame drawn empty and refilled is still seen.
		const states: string[] = [];
		const look = () => {
			const cards = host.querySelectorAll('.wall:not(.waiting) .card').length;
			const waiting = host.querySelector('.wall.waiting') !== null;
			const empty = host.querySelector('.empty') !== null;
			states.push(waiting ? 'waiting' : empty ? 'empty' : cards === 0 ? 'nothing' : 'cards');
		};
		const watcher = new MutationObserver(look);
		watcher.observe(host, { childList: true, subtree: true, attributes: true });

		props.showing = 'sites';
		props.title = 'Sites';
		flushSync();
		await settle();
		expect(host.querySelector('.wall.held')).not.toBeNull();

		release!(answered([row({ id: 's3', name: 'a site' })]));
		await settle();
		await settle();
		watcher.disconnect();
		look();

		expect(states.filter((one) => one !== 'cards')).toEqual([]);
		expect(host.textContent).toContain('a site');
		expect(host.textContent).not.toContain('somebody seen');
		expect(host.querySelector('.wall.held')).toBeNull();
		const wall = host.querySelector<HTMLElement>('.wall');
		expect(wall?.inert || wall?.hasAttribute('inert')).toBe(false);
	});
});

describe('a tab left while it said it was empty', () => {
	it('keeps its empty sentence until the next answer lands, never the waiting cards', async () => {
		const props = await draw('sites', { title: 'Sites' });
		expect(host.textContent).toContain('Nothing here carries sites yet.');
		let release: ((page: { items: RelatedRow[]; total: number }) => void) | null = null;
		load.mockImplementation(
			() => new Promise((resolve) => (release = resolve)) as ReturnType<typeof loadRelated>
		);

		const waited: boolean[] = [];
		const watcher = new MutationObserver(() =>
			waited.push(host.querySelector('.wall.waiting') !== null)
		);
		watcher.observe(host, { childList: true, subtree: true, attributes: true });

		props.showing = 'collections';
		props.title = 'Collections';
		flushSync();
		await settle();
		// The sentence on screen is still the one the Sites answer drew, not a claim about
		// Collections nobody has answered yet.
		expect(host.textContent).toContain('Nothing here carries sites yet.');
		expect(host.textContent).not.toContain('carries collections');

		release!(answered([row({ id: 'c1', name: 'a collection' })]));
		await settle();
		await settle();
		watcher.disconnect();

		expect(waited.filter(Boolean)).toEqual([]);
		expect(host.textContent).toContain('a collection');
		expect(host.textContent).not.toContain('Nothing here carries');
	});
});

describe('opening a loop', () => {
	it('does not intercept a press on any other kind of card', async () => {
		load.mockResolvedValue(answered([row({ id: 't7' })]));
		await draw('tags');

		const press = new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 });
		links()[0].dispatchEvent(press);

		expect(press.defaultPrevented).toBe(false);
		expect(opened).not.toHaveBeenCalled();
	});
});

/*
 * The words on the selection bar, which are about the ROWS and were taken from the TAB.
 *
 * Handing the heading over as the noun would make the bar read "1 people selected" on a wall of
 * people, and on a person's own page (where the tab is called "Seen with") "1 seen with selected":
 * the heading is a plural when it is a noun at all.
 *
 * Ctrl-click is how a row is picked with nothing picked yet; a plain click on a card opens it.
 */
describe('what the selection bar calls a row', () => {
	function pick(name: string, held = false) {
		const card = [...host.querySelectorAll('.card')].find((one) => one.textContent?.includes(name));
		expect(card, `no card for ${name}`).toBeDefined();
		card!.dispatchEvent(
			new MouseEvent('click', { bubbles: true, cancelable: true, button: 0, ctrlKey: !held })
		);
		flushSync();
	}

	it('uses the singular of what the rows ARE, not the plural on the tab', async () => {
		load.mockResolvedValue(answered([row({ id: 'p9', name: 'somebody' })]));
		await draw('people');

		pick('somebody');

		expect(host.textContent).toContain('1 person selected');
		expect(host.textContent).not.toContain('1 people selected');
		// The heading on a person's own wall is a phrase, and must not end up in this sentence.
		expect(host.textContent).not.toContain('seen with selected');
	});

	it('still says people once there is more than one', async () => {
		load.mockResolvedValue(
			answered([row({ id: 'p9', name: 'somebody' }), row({ id: 'p8', name: 'someone else' })])
		);
		await draw('people');

		pick('somebody');
		pick('someone else', true);

		expect(host.textContent).toContain('2 people selected');
	});
});

describe('what a tab wall hands the merge sheet', () => {
	it('fills the picture from the row, through the builders every other picker uses', () => {
		/* The wall is the half that had to change: `WallRow` grew an optional picture and this is
		 * what fills it. Read off the source rather than by driving a selection and a press, which
		 * would be a test of the selection bar, and the two things a reader has to be able to
		 * check are exactly these: that it is filled at all, and that it is filled by the SHARED
		 * builders rather than by a third spelling of "which picture does a person have".
		 *
		 * The carrying half is asserted for real in `wall-verbs.svelte.test.ts`.
		 */
		const source = readFileSync('src/lib/components/entity/RelatedWall.svelte', 'utf8');
		expect(source, 'the wall hands the sheet no picture').toContain('picture: pictureOf(row)');
		expect(source).toContain('personRow(row).picture');
		expect(source).toContain('siteRow(row).picture');

		const flows = readFileSync('src/lib/components/entity/EntityWallFlows.svelte', 'utf8');
		expect(flows, 'the sheet is not given the picture the row carries').toContain(
			'picture: row.picture'
		);
	});
});

/*
 * Picking a card to filter the page's files by. A press on the picture picks, the name stays the
 * card's own link, and the picks are the address. `page` is the shared stand-in from the test
 * setup; each test points it at an address and puts it back.
 */
describe('picking cards to narrow the files', () => {
	const address = page as unknown as { url: URL };
	const moved = vi.mocked(goto);

	afterEach(() => {
		address.url = new URL('http://localhost/');
	});

	function face(name: string): HTMLButtonElement {
		const card = [...host.querySelectorAll('.card')].find((one) => one.textContent?.includes(name));
		const button = card?.querySelector('button.face');
		expect(button, `no pick toggle on ${name}`).toBeTruthy();
		return button as HTMLButtonElement;
	}

	it('picks a card by its picture, writing it into the address AS the filter, in place', async () => {
		address.url = new URL('http://localhost/people/p1?show=people');
		load.mockResolvedValue(answered([row({ id: 'p2', name: 'Jane Else' })]));
		await draw('people', { named: 'Jane Doe' });

		face('Jane Else').click();

		expect(moved).toHaveBeenCalledWith('/people/p1?show=people&people=Jane+Else', {
			replaceState: true,
			keepFocus: true,
			noScroll: true
		});
		// The name is still the way to the card's own page, carrying this page as a filter.
		expect(links().map((link) => link.getAttribute('href'))).toContain(
			'/people/p2?people=Jane+Doe'
		);
	});

	it('draws a card the address has picked with the wash, and takes the pick off on a second press', async () => {
		address.url = new URL('http://localhost/people/p1?show=tags&people=Jane+Else&tags=beach');
		load.mockResolvedValue(
			answered([row({ id: 't1', name: 'beach' }), row({ id: 't2', name: 'dunes' })])
		);
		await draw('tags');

		// The pick's one look is the shared mark over the picture; a card with no pick draws none.
		const picked = face('beach').closest('.card');
		expect(picked?.querySelector('.pick[data-purpose="filter"]')).not.toBeNull();
		expect(face('dunes').closest('.card')?.querySelector('.pick')).toBeNull();

		face('beach').click();

		// The pick from the OTHER tab is kept: picks are gathered across the tabs.
		expect(moved).toHaveBeenCalledWith(
			'/people/p1?show=tags&people=Jane+Else',
			expect.objectContaining({ replaceState: true })
		);
	});

	it('leaves a ctrl-click to the verb selection rather than picking', async () => {
		address.url = new URL('http://localhost/people/p1?show=people');
		load.mockResolvedValue(answered([row({ id: 'p2', name: 'Jane Else' })]));
		await draw('people');

		face('Jane Else').dispatchEvent(
			new MouseEvent('click', { bubbles: true, cancelable: true, button: 0, ctrlKey: true })
		);
		flushSync();

		expect(moved).not.toHaveBeenCalled();
		expect(host.textContent).toContain('1 person selected');
	});

	/* A tab's card reaches both panels, as the wall's does: the Hidden eye and the sharing mark. */
	it('draws a hidden card-s eye, which opens the Hidden panel', async () => {
		load.mockResolvedValue(answered([row({ id: 'p9', name: 'Ada Lumen', vault: true })]));
		await draw('people');
		const eye = host.querySelector<HTMLButtonElement>('button[aria-label$="Open hidden"]');
		expect(eye, 'the card drew no Hidden mark').not.toBeNull();
		eye?.click();
		await settle();
		const panel = [...document.querySelectorAll('[role="dialog"]')].find((one) =>
			one.textContent?.includes('Ada Lumen')
		);
		expect(panel?.textContent).toContain('Hidden');
	});

	it('draws the sharing mark a tag carries on its own wall', async () => {
		load.mockResolvedValue(answered([row({ id: 't7', name: 'beach', shared: true })]));
		await draw('tags');
		expect(host.querySelector('.card .marks')).not.toBeNull();
	});

	it('draws a tag tab with the one card shape every wall draws', async () => {
		load.mockResolvedValue(answered([row({ id: 't1', name: 'beach' })]));
		await draw('tags');
		const face = host.querySelector('.card .face');
		expect(face).not.toBeNull();
		expect(face?.className).not.toMatch(/landscape/);
	});

	it('offers no pick on the sites inside a network, whose every pick would empty the files', async () => {
		load.mockResolvedValue(answered([row({ id: 's4', name: 'Another Studio' })]));
		await draw('sites_within', { on: 'site', id: 's1', named: 'Big Network' });

		expect(host.querySelector('button.face')).toBeNull();
		expect(host.querySelector('a.face')).not.toBeNull();
	});
});

/*
 * One first page per tab: the first card's measurement trims the rows held (see `CardPaging.fill`),
 * and the next visit to the same kind of tab asks at the measured size, for that kind only, since a
 * tag card's size is not a face's. `measuring` stands in for the layout jsdom never does: 150px
 * cards in a 1200px box, one row to a screen, are 8 x 1 x 4 screens = 32.
 */
describe('one first page per tab', () => {
	const MEASURED = 32;
	const every = Array.from({ length: 200 }, (_x, at) => row({ id: `t${at}`, name: `tag ${at}` }));
	let browser: ReturnType<typeof measuring>;

	beforeEach(() => {
		forgetMeasurements();
		Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
			configurable: true,
			get: () => 1200
		});
		browser = measuring({ width: 150, height: 300 });
		load.mockImplementation(async (_on, _id, _showing, { limit = 60, offset = 0 } = {}) =>
			answered(every.slice(offset, offset + limit), every.length)
		);
	});

	afterEach(() => {
		browser.done();
		delete (HTMLElement.prototype as { clientWidth?: number }).clientWidth;
	});

	function limits(): number[] {
		return load.mock.calls.map((call) => Number(call[3]?.limit));
	}

	it('trims the page it holds when the first card is measured, and asks nothing more', async () => {
		await draw('tags');
		browser.deliver();
		await settle();
		await settle();
		expect(limits()).toEqual([60]);
	});

	it('asks at the measured size on the next visit to the same kind of tab, and only once', async () => {
		await draw('tags');
		browser.deliver();
		await settle();
		if (instance) void unmount(instance, { outro: false });
		instance = null;
		host.remove();
		load.mockClear();
		await draw('tags');
		expect(limits()).toEqual([MEASURED]);
	});

	it('keeps each kind of card to its own measurement', async () => {
		await draw('tags');
		browser.deliver();
		await settle();
		if (instance) void unmount(instance, { outro: false });
		instance = null;
		host.remove();
		load.mockClear();
		await draw('people');
		expect(limits()[0]).toBe(60);
	});
});

/* The listing row's cover moment and token reach the card's picture. See the walls' own tests. */
it("puts the row's moment and token on the cover's address", async () => {
	load.mockResolvedValue(
		answered([row({ id: 't7', cover_asset_id: 'cover1', cover_at_ms: 4200, art: 'stamp' })])
	);
	await draw('tags');
	const src = host.querySelector('img.picture')?.getAttribute('src') ?? '';
	expect(src).toMatch(/\?v=stamp\.cover1\.4200$/);
});

describe("the page's own cards after the wall's", () => {
	it('draws them on the last page, and a wall holding only them is not empty', async () => {
		/* A Site's People tab ends with the usernames that belong to nobody yet. With no person on
		   the Site those are the whole tab, and the empty sentence must not take their place. */
		load.mockResolvedValue(answered([]));
		const after = createRawSnippet(() => ({ render: () => '<p class="loose">nobody yet</p>' }));

		await draw('people', { on: 'site', after, trailing: 1 });

		expect(host.querySelector('.loose')?.textContent).toBe('nobody yet');
		expect(host.textContent).not.toContain('Nobody else turns up');
	});
});

describe('the box and the order on a tab', () => {
	const address = page as unknown as { url: URL };

	afterEach(() => {
		address.url = new URL('http://localhost/');
		tabSort('tags').set('largest');
	});

	it('asks with the words in the address, and draws them in its box', async () => {
		address.url = new URL('http://localhost/people/p1?show=tags&called=bea');
		const oncount = vi.fn();
		load.mockResolvedValue(answered([row()], 1));

		await draw('tags', { oncount });

		expect(load).toHaveBeenLastCalledWith(
			'person',
			'p1',
			'tags',
			expect.objectContaining({ words: 'bea', offset: 0 })
		);
		const box = host.querySelector('input[placeholder="Search tags"]') as HTMLInputElement;
		expect(box.value).toBe('bea');
		expect(oncount).toHaveBeenLastCalledWith(1, true);
	});

	it("offers the bar the wall's orders, and asks again from the top in the one chosen", async () => {
		load.mockResolvedValue(answered([row()], 200));
		await draw('tags');

		expect(ordersOffered(screenBar.tools.sorts)).toEqual(ordersFor('tags'));
		expect(screenBar.tools.sort).toBe('largest');
		screenBar.tools.onSort?.('name_az');
		flushSync();
		await settle();

		expect(load).toHaveBeenLastCalledWith(
			'person',
			'p1',
			'tags',
			expect.objectContaining({ sort: 'name_az', offset: 0 })
		);
		expect(tabSort('tags').value).toBe('name_az');
	});

	it("leaves the order on the Loops tab to the media grid, whose orders are a loop's", async () => {
		const props = await draw('loops');
		// Asked again after the grid has published, as a renamed heading does.
		props.title = 'Marks';
		flushSync();
		await settle();

		const labels = ordersOffered(screenBar.tools.sorts).map((one) => one.label);
		expect(labels).toContain('Recently created');
	});
});
