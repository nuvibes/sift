/*
 * Stepping onto a file this account may not see.
 *
 * With the vault open, hidden files sit on the ordinary walls, so arrowing through a wall lands on
 * one. The dialog must keep its shape and the controls that still mean something, rather than
 * collapsing to one line with no way onwards. Why it is hidden is the server's decision; this draws
 * what it was given.
 */
import { flushSync, mount, unmount } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, onTestFinished, vi } from 'vitest';

import AssetView from './AssetView.svelte';
import { api } from '$lib/api/client';
import { libraryChanges } from '$lib/library/changes.svelte';
import { phoneWidth } from '$lib/components/common/phone-width.svelte';
import { finger } from '$lib/components/player/finger.svelte';

const served = vi.hoisted(() => ({
	detail: {} as Record<string, unknown>,
	/* Who is in the file. Its own request, so its own served answer: the band is drawn from this
	   and not from the detail. An invented name: nobody real goes in a fixture. */
	people: [] as Record<string, unknown>[],
	/* Where it came from, and what it is part of. Three more requests, three more served answers,
	   for the same reason: each one draws a row of the band on its own. */
	filings: [] as Record<string, unknown>[],
	collections: { items: [] as Record<string, unknown>[] },
	photoSets: { items: [] as Record<string, unknown>[] },
	/*
	 * And the words on it: the tags are the fifth row of the band, loaded here with the other four
	 * kinds.
	 */
	tags: [] as Record<string, unknown>[],
	/* What landed on the clipboard, and whether it was allowed to. The helper is stood in for
	   rather than the clipboard itself: `navigator.clipboard` does not exist over plain http and
	   `$lib/shell/clipboard` is the one place that knows what to do about that: what is under test here
	   is only that the name is what this screen hands it. */
	copied: [] as string[],
	copyWorks: true
}));

/** Every sentence this screen put in front of somebody. */
const shouted = vi.hoisted(() => [] as { words: string }[]);

/*
 * The same doors the record test mocks: the view reads the page's state and the session, and
 * without them nothing draws at all.
 */
vi.mock('$lib/shell/session.svelte', () => ({ session: { isAdmin: true, canSave: false } }));

vi.mock('$app/state', () => ({
	page: { route: { id: '/browse' }, url: new URL('http://localhost/browse'), state: {} }
}));

vi.mock('$lib/shell/toasts.svelte', () => ({
	toasts: {
		show: (words: string) => {
			shouted.push({ words });
		}
	}
}));

vi.mock('$lib/shell/clipboard', () => ({
	copyText: async (text: string) => {
		served.copied.push(text);
		return served.copyWorks;
	}
}));

vi.mock('$lib/api/client', () => ({
	api: {
		get: vi.fn(async (path: string) => {
			if (path.endsWith('/people')) return served.people;
			if (path.endsWith('/filings')) return served.filings;
			if (path.endsWith('/tags')) return served.tags;
			if (path === '/collections') return served.collections;
			if (path === '/photo-sets') return served.photoSets;
			if (path === '/songs') return { items: [] };
			/* The field registry, which the record grid reads on its first draw. Shaped rather than
			   empty: it stores `answer.subjects` without checking, so a bare `[]` leaves the
			   registry holding undefined and every read of it throws out of a render. */
			if (path === '/records/fields') return { subjects: {} };
			if (path.startsWith('/assets/')) return served.detail;
			return [];
		}),
		/* Every one of them answers a PROMISE. A bare `vi.fn()` returns undefined, and the view
		   reports a sitting on its way out with `.catch(...)` on the result, so an unmount would
		   throw from a teardown, outside any test, which vitest reports as an unhandled rejection. */
		post: vi.fn(async () => ({})),
		put: vi.fn(async () => ({})),
		del: vi.fn(async () => ({}))
	},
	ApiError: class extends Error {}
}));

/** The shape the detail route answers with, cut down to what this view reads. */
function detail(over: Record<string, unknown> = {}) {
	return {
		id: 'a-1',
		/* A PHOTOGRAPH, so the one case here that is not hidden draws the still rather than the
		   player: the player asks the server how to play the file the moment it mounts, and this
		   file is about the frame around the picture rather than about playback. */
		media_type: 'image',
		filename: 'a-picture.jpg',
		original_filename: null,
		concealed: false,
		favorite: false,
		rating: null,
		hidden: false,
		hidden_here: false,
		shared: false,
		restricted: false,
		shared_here: false,
		restricted_here: false,
		added_at: 1_700_000_000,
		duration_ms: null,
		width: 1920,
		height: 1080,
		enriched: [],
		// What wrote to it without a person doing it. Drawn as the marks beside the name.
		enriched_by: [],
		links: [],
		playback_repair: null,
		sprite: null,
		art: null,
		...over
	};
}

let instance: ReturnType<typeof mount> | null = null;
let host: HTMLElement;

beforeEach(() => {
	served.people = [];
	served.filings = [];
	served.collections = { items: [] };
	served.photoSets = { items: [] };
	served.tags = [];
	host = document.createElement('div');
	document.body.appendChild(host);
});

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	host.remove();
});

async function show(props: Record<string, unknown>): Promise<void> {
	instance = mount(AssetView, { target: host, props: { id: 'a-1', ...props } });
	await vi.waitFor(() => {
		flushSync();
		if (!host.querySelector('.stage')) throw new Error('nothing drawn yet');
	});
}

describe('a hidden file reached by stepping', () => {
	it('keeps the stage and says what it is instead of replacing the view', async () => {
		served.detail = detail({ concealed: true });
		await show({ onnext: () => {}, onprevious: () => {} });

		// The frame is still there, which is what keeps the dialog the size it was, and is also the
		// element a browser holds fullscreen, so it must survive a step onto one of these.
		expect(host.querySelector('.stage')).not.toBeNull();
		expect(host.textContent).toContain('It takes the PIN to see');
	});

	it('keeps Previous and Next, so stepping past it works', async () => {
		served.detail = detail({ concealed: true });
		const onnext = vi.fn();
		await show({ onnext, onprevious: () => {} });

		const next = host.querySelector('[aria-label="Next"]');
		if (!(next instanceof HTMLElement)) throw new Error('there is no way onwards');
		(next.closest('button') as HTMLButtonElement).click();
		flushSync();

		expect(onnext).toHaveBeenCalled();
		expect(host.querySelector('[aria-label="Previous"]')).not.toBeNull();
	});

	it('keeps the whole bar with nowhere to step, every press dimmed with the reason', async () => {
		served.detail = detail({ concealed: true });
		await show({});

		const play = host.querySelector('.player-bar button.play') as HTMLButtonElement | null;
		expect(play?.disabled).toBe(true);
		expect(play?.getAttribute('aria-label')).toBe('This one is hidden');
		const hidden = host.querySelectorAll('.player-bar button[aria-label="This one is hidden"]');
		expect(hidden.length).toBeGreaterThanOrEqual(5);
		expect([...hidden].every((one) => (one as HTMLButtonElement).disabled)).toBe(true);
		const steps = ['Nothing before this', 'Nothing after this'];
		const reasons = new Set(
			[...host.querySelectorAll('.player-bar button:disabled')]
				.map((one) => one.getAttribute('aria-label'))
				.filter((words) => !steps.includes(words ?? ''))
		);
		expect([...reasons]).toEqual(['This one is hidden']);
	});

	it('says nothing else about it: no controls, no rows, no record', async () => {
		// There is nothing to say about a file this account may not see, and every control would be
		// one the server refuses. That is the point of the vault rather than a limit of this screen.
		served.detail = detail({ concealed: true });
		await show({ onnext: () => {}, onprevious: () => {} });

		expect(host.querySelector('.acts')).toBeNull();
		expect(host.querySelector('.entities')).toBeNull();
		expect(host.querySelector('.below')).toBeNull();
	});

	it('draws the controls and everything under them for a file that is not hidden', async () => {
		// The other half of the branch, so a test that passes because nothing renders cannot be
		// mistaken for this one working. `.record`, the full-width rows under the player.
		served.detail = detail();
		await show({});

		await vi.waitFor(() => {
			flushSync();
			if (!host.querySelector('.acts')) throw new Error('no controls yet');
		});
		expect(host.querySelector('.record')).not.toBeNull();
	});
});

/*
 * The rows under the player, and the panel beside the lookalikes.
 *
 * Here rather than in a file of their own because this is the one test that mounts this view, and a
 * second harness for the same thousand-line component is a second set of mocks to keep in step.
 */
describe('the rows under the player', () => {
	it('draws a person as the shared chip, not as a pill of this screen own', async () => {
		// A person's chip is one height with its neighbours: this screen draws no pill of its own,
		// and the height is the one thing a gate can read.
		served.people = [
			{ id: 'p-1', name: 'Marla Quist', cover_asset_id: null, automatic: false, source: null }
		];
		served.detail = detail();
		await show({});

		const rows = await vi.waitFor(() => {
			flushSync();
			const found = host.querySelector('.entities');
			if (!found) throw new Error('no rows yet');
			return found;
		});

		const chip = rows.querySelector('.chip');
		expect(chip).not.toBeNull();
		// The face is the chip own, at the chip own height, rather than a box sized here.
		expect(chip?.querySelector('.shot')).not.toBeNull();
		// And nothing is left of the hand-drawn pill.
		expect(rows.querySelector('.person')).toBeNull();
	});

	it('gives each kind a row of its own, in the order the question is asked', async () => {
		/* One line holding all five kinds would make a file with four people and three sites seven
		   chips in a row with nothing but the pictures to tell them apart. The glyph at the head of each
		   row is what says which kind it is, and the order is the rail's. */
		served.people = [
			{ id: 'p-1', name: 'Marla Quist', cover_asset_id: null, automatic: false, source: null }
		];
		served.filings = [
			{
				username_id: 'ac-1',
				username: null,
				person_id: null,
				site: 'Bramblecast',
				site_id: 'pf-1',
				source: null
			}
		];
		served.collections = { items: [{ id: 'c-1', name: 'Keepers' }] };
		served.photoSets = { items: [{ id: 'ps-1', name: 'Beach morning' }] };
		served.tags = [{ id: 't-1', name: 'rooftop' }];
		served.detail = detail();
		await show({});

		await vi.waitFor(() => {
			flushSync();
			if (host.querySelectorAll('.entity-row').length < 5) throw new Error('not all drawn');
		});
		const named = [...host.querySelectorAll('.entity-row [role="img"]')].map((one) =>
			one.getAttribute('aria-label')
		);
		expect(named).toEqual(['People', 'Sites', 'Collections', 'Photo Sets', 'Tags']);
	});

	it('draws no row for a kind the file has none of', async () => {
		/*
		 * A heading over nothing is a row somebody reads and learns nothing from, Tags included:
		 * tagging is under "Add to" on the file's own menu, as on every other surface, so there is
		 * no adder to stand here.
		 */
		served.people = [
			{ id: 'p-1', name: 'Marla Quist', cover_asset_id: null, automatic: false, source: null }
		];
		served.detail = detail();
		await show({});

		await vi.waitFor(() => {
			flushSync();
			if (!host.querySelector('.entity-row')) throw new Error('no rows yet');
		});
		const named = [...host.querySelectorAll('.entity-row [role="img"]')].map((one) =>
			one.getAttribute('aria-label')
		);
		expect(named).not.toContain('Sites');
		expect(named).not.toContain('Collections');
		expect(named).not.toContain('Photo sets');
		expect(named, 'the empty Tags row is still drawn').not.toContain('Tags');
	});

	it('carries the file name at the head of the action row', async () => {
		/*
		 * The sheet has no title bar, so the file's name is drawn here; it is what tells two
		 * similar clips apart.
		 */
		served.detail = detail({ filename: 'a-picture.jpg' });
		await show({});

		const acts = await vi.waitFor(() => {
			flushSync();
			const found = host.querySelector('.acts');
			if (!found) throw new Error('no controls yet');
			return found;
		});
		expect(acts.querySelector('.filename')?.textContent?.trim()).toBe('a-picture.jpg');
	});

	it("puts the heart and the stars in front of Add to, at the row's trailing end", async () => {
		/*
		 * The heart and stars sit before the Add to button within the row's trailing group.
		 * Asserted as document order within `.ends`, because a rule that merely laid them out the
		 * right way round would not survive the row wrapping on a narrow window.
		 */
		await show({});

		const ends = await vi.waitFor(() => {
			flushSync();
			const found = host.querySelector('.ends');
			if (!found) throw new Error('no controls yet');
			return found;
		});
		/* The icon's own ligature is a character in the button's text and it is not whitespace, so
		   `trim` leaves it behind and "Add to" reads as " Add to". Everything outside printable ASCII
		   is dropped before trimming, which is the glyph and nothing else in these four labels. */
		const order = [...ends.querySelectorAll('button')].map((one) =>
			(one.getAttribute('aria-label') ?? one.textContent ?? '').replace(/[^\x20-\x7e]/g, '').trim()
		);
		/* The whole group, spelled out: it is cheaper to read than three index comparisons and it says
		   the arrangement outright: the two marks, then the door that puts this file somewhere, then
		   the door that holds everything else, which stays where the eye already looks for it. */
		expect(order).toEqual([
			'Add to favorites',
			'This file: not rated',
			/* The O counter is the third mark: one press each, like the two before it. */
			'O counter: 0',
			'Add to',
			'Options for this file'
		]);
	});

	it('gives the fold control a ground rather than an underline on hover', async () => {
		/*
		 * The collapse arrow is a control on a row of controls, so it wears `ghost` (whose hover
		 * steps a background) rather than `quiet` (which underlines, for a word at the end of a
		 * list). Asserted as the tone class, the one thing about it a scoped stylesheet cannot be
		 * asked for here.
		 */
		await show({});

		const fold = await vi.waitFor(() => {
			flushSync();
			const found = host.querySelector<HTMLElement>('.acts > button[aria-expanded]');
			if (!found) throw new Error('no fold control yet');
			return found;
		});

		expect(fold.classList.contains('ghost')).toBe(true);
		expect(fold.classList.contains('quiet')).toBe(false);
	});

	it('copies the name when the name is pressed, and says so for a beat', async () => {
		/*
		 * The name itself is the copy control, with the tooltip saying so; a second glyph beside
		 * the widest target on the row would be one more thing to read past.
		 */
		served.copied.length = 0;
		served.copyWorks = true;
		served.detail = detail({ filename: 'a-picture.jpg' });
		await show({});
		const name = await vi.waitFor(() => {
			flushSync();
			const found = host.querySelector<HTMLElement>('.filename');
			if (!found) throw new Error('no name yet');
			return found;
		});
		expect(name.tagName, 'the name is not something to press').toBe('BUTTON');

		name.click();

		await vi.waitFor(() => {
			flushSync();
			expect(served.copied).toEqual(['a-picture.jpg']);
		});
	});

	it('leaves the label up after the press, so the word "Copied" is there to read', async () => {
		/*
		 * The tooltip is the whole answer here: bubbles are dismissed on press everywhere else, so
		 * this one control, whose answer is the label, must keep it and read "Copied" after the
		 * press.
		 *
		 * Driven with a pointer move rather than an enter, because a move is what that component
		 * takes as somebody actually pointing at it.
		 */
		served.copied.length = 0;
		served.copyWorks = true;
		served.detail = detail({ filename: 'a-picture.jpg' });
		await show({});
		const name = await vi.waitFor(() => {
			flushSync();
			const found = host.querySelector<HTMLElement>('.filename');
			if (!found) throw new Error('no name yet');
			return found;
		});

		name.closest('.wrap')?.dispatchEvent(new PointerEvent('pointermove', { bubbles: true }));
		const bubble = await vi.waitFor(() => {
			flushSync();
			const found = document.querySelector('[role="tooltip"]');
			if (!found) throw new Error('no label yet');
			return found;
		});
		// Copy, and only Copy: the name is already on screen beside it.
		expect(bubble.textContent?.trim()).toBe('Copy');

		name.click();

		await vi.waitFor(() => {
			flushSync();
			const still = document.querySelector('[role="tooltip"]');
			if (!still) throw new Error('the label was taken away by the press');
			expect(still.textContent).toContain('Copied');
		});
	});

	it('says nothing was copied rather than claiming it was', async () => {
		/* The tooltip reading "Copied" over an empty clipboard is the one lie this control can tell,
		   and a plain-http browser that refuses the write is the case it would tell it in. */
		served.copied.length = 0;
		served.copyWorks = false;
		served.detail = detail({ filename: 'a-picture.jpg' });
		await show({});
		const name = await vi.waitFor(() => {
			flushSync();
			const found = host.querySelector<HTMLElement>('.filename');
			if (!found) throw new Error('no name yet');
			return found;
		});

		name.click();

		await vi.waitFor(() => {
			flushSync();
			expect(shouted.map((one) => one.words)).toContain("That name couldn't be copied");
		});
	});

	it('wears the marks saying what wrote to it, beside the name', async () => {
		/*
		 * The same marks a person's page and a hover card wear, drawn by the same component: one
		 * per pass, each naming its box or saying which of Sift's own passes did it. Beside the
		 * name, because History says the same thing three presses away behind a tab.
		 */
		served.detail = detail({
			enriched_by: [
				{ via: 'stash', name: 'Bramblecast' },
				{ via: 'faces', name: null }
			]
		});
		await show({});

		const marks = await vi.waitFor(() => {
			flushSync();
			const found = host.querySelector('.named [role="group"]');
			if (!found) throw new Error('no marks yet');
			return found;
		});
		const said = [...marks.querySelectorAll('[role="img"]')].map((one) =>
			one.getAttribute('aria-label')
		);
		/*
		 * "Enriched by " and then the Enriched by column's own row, word for word. The prefix stays
		 * because nothing announces a group to a pointer, so without it a hover reads "Sift: from a
		 * face". The row is still the panel's, which keeps one fact to one spelling.
		 */
		expect(said).toEqual(['Enriched by Stash-box: Bramblecast', 'Enriched by Sift: from a face']);
	});

	it('draws a site by its own mark, and what the file is part of by its cover', async () => {
		/* A site drawn as bare words beside a collection wearing a cover would say that some of the
		   row's nouns are things and the rest are labels. Every entity chip carries the chip's own
		   picture, and the site's is the mark Sift already holds, which is the rule the pick rows
		   and the Sites wall follow. */
		served.filings = [
			{
				username_id: 'ac-1',
				username: '@quill',
				person_id: null,
				site: 'Bramblecast',
				site_id: 'pf-1',
				source: null
			}
		];
		served.photoSets = { items: [{ id: 'ps-1', name: 'Beach morning' }] };
		served.detail = detail();
		await show({});

		const filed = await vi.waitFor(() => {
			flushSync();
			const found = host.querySelector('.entities .chip');
			if (!found) throw new Error('no site chip yet');
			return found;
		});
		// The chip's OWN picture box, at the chip's own inner height: the same one a person wears.
		// Which address goes in it is `entityPicture`'s rule and is proved where that rule lives.
		expect(filed.querySelector('.shot')).not.toBeNull();
		// The SITE'S NAME alone. The row already says these are Sites, so the username in front of
		// it would repeat the heading and bury the one word being scanned for.
		expect(filed.textContent).toContain('Bramblecast');
		expect(filed.textContent).not.toContain('@quill');

		// And the photo set, drawn by the same rule rather than by ids read off the listing here.
		const set = [...host.querySelectorAll('.entities .chip')].find((one) =>
			one.textContent?.includes('Beach morning')
		);
		expect(set?.querySelector('.shot')).not.toBeNull();
	});

	it('wraps every entity chip in the same hover target, which is what levels the rows', async () => {
		/*
		 * Every entity chip sits in one block wrapper. An inline wrapper is laid out on a line with
		 * room under it for descenders, which makes a row taller than its neighbours and rides its
		 * chips 2px high. A chip outside the wrapper would be free to sit somewhere else.
		 */
		served.people = [
			{ id: 'p-1', name: 'Marla Quist', cover_asset_id: null, automatic: false, source: null }
		];
		served.filings = [
			{
				username_id: 'ac-1',
				username: null,
				person_id: null,
				site: 'Bramblecast',
				site_id: 'pf-1',
				source: null
			}
		];
		served.collections = { items: [{ id: 'c-1', name: 'Keepers' }] };
		served.tags = [{ id: 't-1', name: 'rooftop' }];
		served.detail = detail();
		await show({});

		await vi.waitFor(() => {
			flushSync();
			if (host.querySelectorAll('.entities .hovered').length < 4) throw new Error('not all drawn');
		});
		/* A person, a site, a collection and a tag: four kinds, one wrapper each, and not one of
		   them standing outside it. Counted rather than named, so a fifth kind added to the rows
		   without the wrapper is caught here rather than by somebody noticing a row sitting two
		   pixels high. EVERY chip in the rows is an entity: the "Added" date is in the record,
		   where the file's other facts are read. */
		const chips = [...host.querySelectorAll('.entities .chip')];
		const wrapped = chips.filter((one) => one.closest('.hovered') !== null);
		expect(wrapped).toHaveLength(4);
		expect(chips.filter((one) => one.closest('.hovered') === null)).toHaveLength(0);
		expect(host.textContent).not.toContain('Added ');
	});

	it('draws the record as panes with no cap and no way out to offer', async () => {
		/*
		 * The record is full width under both strips and as tall as the pane inside it, with no cap
		 * and no "See more". What is held is that the record is three panes and carries neither
		 * the word nor a height; a cap on a full-width panel would hide the record for no
		 * reason.
		 */
		served.detail = detail();
		await show({});

		const panes = await vi.waitFor(() => {
			flushSync();
			const found = host.querySelector('.panes');
			if (!found) throw new Error('no record yet');
			return found;
		});

		expect(panes.classList.contains('fixed')).toBe(false);
		expect(host.textContent).not.toContain('See more');
		// And the panes are a tablist.
		expect(host.querySelector('[role="tablist"]')).not.toBeNull();
	});
});

/*
 * The band, after something has been put on the file from this very screen. Adding a tag under "Add
 * to" goes out through the shared verb, which rings the library bell when the write lands; the band
 * re-reads on that bell, as every other list does, so the chip appears immediately (removal moves
 * the local list directly from the chip).
 */
describe('the band after a pick', () => {
	/** Ring the bell the way a verb does when its write has landed, and let the re-read finish. */
	async function libraryMoved(): Promise<void> {
		libraryChanges.changed();
		flushSync();
		await vi.waitFor(() => {
			flushSync();
			if (!host.querySelector('.entities')) throw new Error('no rows yet');
		});
	}

	it('shows a tag put on the file without waiting for a reload', async () => {
		served.tags = [];
		served.detail = detail();
		await show({});

		await vi.waitFor(() => {
			flushSync();
			if (!host.querySelector('.acts')) throw new Error('nothing drawn yet');
		});
		expect(host.textContent).not.toContain('rooftop');

		// What the write did, as the server would now answer it.
		served.tags = [{ id: 't-1', name: 'rooftop' }];
		await libraryMoved();

		await vi.waitFor(() => {
			flushSync();
			if (!host.textContent?.includes('rooftop')) throw new Error('the band has not caught up');
		});
	});

	it('shows a person filed under the file the same way', async () => {
		served.people = [];
		served.detail = detail();
		await show({});

		await vi.waitFor(() => {
			flushSync();
			if (!host.querySelector('.acts')) throw new Error('nothing drawn yet');
		});
		expect(host.textContent).not.toContain('Marla Quist');

		served.people = [
			{ id: 'p-1', name: 'Marla Quist', cover_asset_id: null, automatic: false, source: null }
		];
		await libraryMoved();

		await vi.waitFor(() => {
			flushSync();
			if (!host.textContent?.includes('Marla Quist')) throw new Error('the band has not caught up');
		});
	});

	it('does not blank what is drawn while it re-reads, and does not re-report the sitting', async () => {
		/* The reason the bell rings `loadBand` and never the whole of `load`. That one empties the
		   rows before it fetches: right when the file CHANGES, so the previous file's people do not
		   sit under the new picture, and wrong for a file already on screen, where it is a band that
		   blinks on every change made anywhere in the library. It also restarts a still's dwell
		   timer, which would report this photograph as freshly opened every time anything was
		   tagged. */
		served.tags = [{ id: 't-1', name: 'rooftop' }];
		served.detail = detail();
		await show({});

		await vi.waitFor(() => {
			flushSync();
			if (!host.textContent?.includes('rooftop')) throw new Error('no tag yet');
		});
		const asked = vi.mocked(api.get).mock.calls.filter((call) => call[0] === `/assets/a-1`).length;

		await libraryMoved();

		// Still there through the re-read, rather than gone and back.
		expect(host.textContent).toContain('rooftop');
		// And the file itself was not fetched again, which is what the dwell timer hangs off.
		expect(vi.mocked(api.get).mock.calls.filter((call) => call[0] === `/assets/a-1`)).toHaveLength(
			asked
		);
	});
});

/*
 * On a phone the viewer owns the screen: the file's verbs are a bar, what the library knows about
 * the file is a sheet from Info rather than a fold under the picture, and a finger drawn sideways
 * steps through the run in place of the bar's Previous and Next.
 */
describe('on a phone', () => {
	/* A window at a phone's width with a finger as its pointer, set on the two shared readings. */
	beforeEach(() => {
		phoneWidth.yes = true;
		finger.yes = true;
	});

	afterEach(() => {
		phoneWidth.yes = false;
		finger.yes = false;
	});

	async function bar(): Promise<HTMLElement> {
		return vi.waitFor(() => {
			flushSync();
			const acts = host.querySelector<HTMLElement>('.acts.fills');
			if (!acts) throw new Error('no bar yet');
			return acts;
		});
	}

	it('draws the verbs as a bar with Info, and no fold under the picture', async () => {
		served.detail = detail();
		await show({});
		const acts = await bar();

		expect(acts.querySelector('[aria-label="Show info"]')).not.toBeNull();
		expect(acts.querySelector('[aria-label="Share"]')).not.toBeNull();
		expect(acts.querySelector('[aria-label="More for this file"]')).not.toBeNull();
		expect(host.textContent).not.toContain('Expand');
		expect(host.querySelector('.fold')).toBeNull();
	});

	it('opens the sections in a sheet from Info, and Escape shuts only the sheet', async () => {
		served.detail = detail();
		/* The frame the viewer sits in, listening on the window from before the sheet opened, as a
		   dialog does: it shuts on an Escape nobody answered, so it must hear this one answered. */
		const frameHeard: boolean[] = [];
		const frame = (event: KeyboardEvent) => {
			if (event.key === 'Escape') frameHeard.push(event.defaultPrevented);
		};
		window.addEventListener('keydown', frame);
		onTestFinished(() => window.removeEventListener('keydown', frame));
		await show({});
		const acts = await bar();

		(acts.querySelector('[aria-label="Show info"]') as HTMLButtonElement).click();
		flushSync();
		const sheet = await vi.waitFor(() => {
			flushSync();
			const open = document.querySelector<HTMLElement>('aside.drawer.open');
			if (!open?.querySelector('.record')) throw new Error('no sheet yet');
			return open;
		});
		expect(sheet.textContent).toContain('File info');

		const escape = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
		window.dispatchEvent(escape);
		flushSync();

		expect(escape.defaultPrevented).toBe(true);
		expect(frameHeard, 'the frame under the sheet heard an Escape nobody answered').toEqual([true]);
		expect(document.querySelector('aside.drawer.open')).toBeNull();
	});

	it('steps on a sideways stroke and draws no Previous or Next on the bar', async () => {
		served.detail = detail();
		const onnext = vi.fn();
		await show({ onnext, onprevious: () => {} });
		await bar();

		expect(host.querySelector('[aria-label="Next"]')).toBeNull();
		const picture = host.querySelector('.swipe .stage') as HTMLElement;
		for (const [type, x] of [
			['pointerdown', 300],
			['pointerup', 60]
		] as const) {
			picture.dispatchEvent(
				new PointerEvent(type, {
					bubbles: true,
					pointerType: 'touch',
					isPrimary: true,
					pointerId: 1,
					clientX: x,
					clientY: 100
				})
			);
		}

		expect(onnext).toHaveBeenCalledOnce();
	});
});
