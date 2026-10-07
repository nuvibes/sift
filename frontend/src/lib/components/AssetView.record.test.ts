/*
 * The file's record, as three panes behind a strip.
 *
 * What is under test is the part a screenshot cannot show: which fields are in which pane, what the
 * numbers on the tabs are counting, that the history is asked for when its pane is opened and not
 * with the file, and that it is asked AGAIN when the file moves under an open pane.
 *
 * Its own file rather than a second describe in `AssetView.hidden.test.ts`, and the reason is the
 * registry: it memoises its one request for the session, so a file that serves real fields would
 * decide what every other test in it draws. A module registry is per test FILE in this runner,
 * which is the seam.
 */
import { flushSync, mount, unmount } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import AssetViewProbe, { showing, wentAway } from './AssetViewProbe.test.svelte';
import { api, ApiError } from '$lib/api/client';
import { rereadInterfaceState } from '$lib/shell/interface-state.svelte';
import { jobChanges, libraryChanges, recorded } from '$lib/library/changes.svelte';
import { toasts } from '$lib/shell/toasts.svelte';
import { PANEL_WAIT_MS } from './file-band.svelte';

vi.mock('$lib/shell/session.svelte', () => ({ session: { isAdmin: true, canSave: false } }));
/*
 * The panel asks which screen it was opened from (a sitting keeps the word); this harness has no
 * router, so the page is a Browse page with nothing typed. Without it every test here fails before
 * the record is drawn.
 */
vi.mock('$app/state', () => ({
	page: { route: { id: '/browse' }, url: new URL('http://localhost/browse'), state: {} }
}));

const served = vi.hoisted(() => ({
	detail: {} as Record<string, unknown>,
	/** What the server says a file is made of. Cut down to six fields across the two halves. */
	fields: {
		subjects: {
			asset: [
				{
					key: 'title',
					subject: 'asset',
					label: 'Title',
					kind: 'text',
					shown: 'record',
					group: 'record',
					editable: true,
					imported: true,
					help: null,
					suggests: null,
					entry: null,
					links_to: null
				},
				{
					key: 'music',
					subject: 'asset',
					label: 'Music',
					kind: 'text',
					shown: 'record',
					group: 'record',
					editable: true,
					imported: true,
					help: null,
					suggests: null,
					entry: null,
					links_to: null
				},
				{
					/* The one editable field whose save touches the DISK, so it is in the fixture
					   rather than left out as "one more text box": the record's save has to send it
					   somewhere else entirely, and a cut-down registry without it cannot show that. */
					key: 'filename',
					subject: 'asset',
					label: 'Filename',
					kind: 'filename',
					shown: 'record',
					group: 'record',
					editable: true,
					imported: false,
					help: null,
					suggests: null,
					entry: null,
					links_to: null
				},
				{
					key: 'container',
					subject: 'asset',
					label: 'Container',
					kind: 'word',
					shown: 'record',
					group: 'media',
					editable: false,
					imported: false,
					help: null,
					suggests: null,
					entry: null,
					links_to: null
				},
				{
					key: 'fps',
					subject: 'asset',
					label: 'Frame rate',
					kind: 'rate',
					shown: 'record',
					group: 'media',
					editable: false,
					imported: false,
					help: null,
					suggests: null,
					entry: null,
					links_to: null
				}
			]
		}
	},
	/**
	 * What this account has arranged. Nothing about the record's panes is in here; see the last
	 * describe in this file.
	 */
	interface: {} as Record<string, string>,
	/** What has happened to the file, and how many times it was asked for. */
	history: [] as Record<string, unknown>[],
	historyAsks: 0,
	historyFails: false,
	/** The `limit` each history read asked with, in order. */
	historyLimits: [] as (number | undefined)[],
	/** Who is on the file, and the collections holding it: the rows the chips are drawn from. */
	people: [] as Record<string, unknown>[],
	collections: [] as Record<string, unknown>[],
	/** The song the file carries, as the Songs wall narrowed to the file answers it. */
	songs: [] as Record<string, unknown>[],
	/** The sites the file is filed under, each carrying its site's cover fields. */
	filings: [] as Record<string, unknown>[],
	/** Where a stash-box disagrees with the file, and which record each read was about. */
	disagreements: [] as Record<string, unknown>[],
	disagreementAsks: [] as string[]
}));

/* What Run task offers, as the server lists it: two stages, one pass each. Hoisted because the
   module mock below reads it. */
const RUN_NOW_GROUPS = vi.hoisted(() => [
	{
		family: 'generate',
		label: 'Generate now',
		every: { key: 'generate:all', label: 'Generate all', help: '' },
		passes: [{ key: 'thumbnails', label: 'Thumbnails', help: '' }]
	},
	{
		family: 'identify',
		label: 'Identify now',
		every: { key: 'identify:all', label: 'Identify all', help: '' },
		passes: [{ key: 'faces', label: 'Faces', help: '' }]
	}
]);

vi.mock('$lib/api/client', () => ({
	api: {
		get: vi.fn(async (path: string, options?: { query?: { limit?: number } }) => {
			if (path === '/records/fields') return served.fields;
			if (path === '/settings/interface') return { state: served.interface };
			if (path.startsWith('/stash-boxes/disagreements/')) {
				served.disagreementAsks.push(path);
				return { disagreements: served.disagreements };
			}
			if (path.endsWith('/history')) {
				served.historyAsks += 1;
				served.historyLimits.push(options?.query?.limit);
				if (served.historyFails) throw new Error('no');
				// The route's answer: the newest `limit` lines (fifty unasked) and the total.
				const limit = options?.query?.limit ?? 50;
				return { items: served.history.slice(-limit), total: served.history.length };
			}
			if (path.endsWith('/people')) return served.people;
			if (path.endsWith('/filings')) return served.filings;
			if (path.endsWith('/tags')) return [];
			if (path === '/collections') return { items: served.collections };
			if (path === '/photo-sets') return { items: [] };
			if (path === '/songs') return { items: served.songs };
			if (path === '/importing/run-now') return { groups: RUN_NOW_GROUPS };
			if (path.startsWith('/assets/')) return { ...served.detail, id: path.split('/')[2] };
			return [];
		}),
		/* The delete route's real shape, because the screen reads it: `skipped` is what decides
		   whether the rows go or the page is read again, and a bare `{}` makes that read undefined
		   and take the wrong branch silently. */
		post: vi.fn(async () => ({ changed: 1, skipped: 0, reason: null })),
		put: vi.fn(async () => ({})),
		del: vi.fn(async () => ({}))
	},
	/* The real shape, not a bare `Error`: `detail` is what carries a refusal the server wrote for
	   the person reading it, and a stand-in without it cannot tell the two kinds of failure apart,
	   which is exactly the branch the rename's refusal takes. */
	ApiError: class extends Error {
		status: number;
		detail?: string;
		constructor(status = 400, message = 'no', detail?: string) {
			super(message);
			this.status = status;
			this.detail = detail;
		}
	}
}));

/** The detail route's answer, cut down to what this view reads. An invented name, as every fixture. */
function detail(over: Record<string, unknown> = {}) {
	return {
		id: 'a-1',
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
		title: 'Golden hour',
		music: null,
		container: 'jpg',
		fps: null,
		...over
	};
}

let instance: ReturnType<typeof mount> | null = null;
let host: HTMLElement;

beforeEach(() => {
	/* The account's arrangement is read once per SESSION and held in the module, which is right in
	   the app and is state carried between cases here: a pane pressed in one test is the pane the
	   next one opens on. Forgotten rather than re-asked: nothing is fetched until somebody reads. */
	rereadInterfaceState();
	showing.id = 'a-1';
	wentAway.ids.length = 0;
	vi.mocked(api.put).mockClear();
	vi.mocked(api.post).mockClear();
	vi.mocked(api.post).mockResolvedValue({ changed: 1, skipped: 0, reason: null });
	served.detail = detail();
	served.interface = {};
	served.history = [];
	served.historyAsks = 0;
	served.historyFails = false;
	served.historyLimits = [];
	served.people = [];
	served.collections = [];
	served.songs = [];
	served.filings = [];
	served.disagreements = [];
	served.disagreementAsks = [];
});

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	host.remove();
});

async function show(): Promise<void> {
	host = document.createElement('div');
	document.body.appendChild(host);
	instance = mount(AssetViewProbe, { target: host });
	await vi.waitFor(() => {
		flushSync();
		if (!host.querySelector('[role="tablist"]')) throw new Error('no record yet');
	});
}

/** The strip's words, with their counts. */
function strip(): string[] {
	return [...host.querySelectorAll('[role="tab"]')].map((one) => one.textContent?.trim() ?? '');
}

/** The pane on screen. The others are all laid out beside it (the strip is a carousel and the
 *  panes sit in a row that travels) and are taken out of REACH rather than out of the layout. */
function pane(): HTMLElement {
	const open = [...host.querySelectorAll('[role="tabpanel"]')].find(
		(one) => !one.hasAttribute('inert')
	);
	if (!(open instanceof HTMLElement)) throw new Error('no pane is showing');
	return open;
}

function press(word: string): void {
	const tab = [...host.querySelectorAll('[role="tab"]')].find((one) =>
		one.textContent?.startsWith(word)
	);
	if (!(tab instanceof HTMLElement)) throw new Error(`there is no ${word} tab`);
	tab.click();
	flushSync();
}

/** Every row label in the pane that is showing. */
function labels(): string[] {
	return [...pane().querySelectorAll('dt')].map((one) => one.textContent?.trim() ?? '');
}

/** The record's own writes: the Remote's offer also puts to `/remote/...` while a player is open. */
function recordPuts() {
	return vi.mocked(api.put).mock.calls.filter(([path]) => !String(path).startsWith('/remote/'));
}

describe('the three panes', () => {
	it('splits the record by what the registry says each field is', async () => {
		/* The whole reason `group` is on the declaration. Neither half is a list in this
		   screen, so a field added to the registry next year lands in the pane it declares. */
		await show();

		expect(labels()).toEqual(['Title', 'Music', 'Filename']);

		press('Media');

		expect(labels()).toEqual(['Container']);
	});

	it('counts what is filled in, not what is declared', async () => {
		/*
		 * Two record fields and one of them written, two machine facts and one of them measured,
		 * and no history number before the thread is read.
		 */
		await show();

		expect(strip()).toEqual(['About2', 'Media1', 'History']);
	});

	it('reads the panel immediately when the picture cannot be drawn, not after the wait', async () => {
		await show();
		const panelReads = () =>
			vi.mocked(api.get).mock.calls.filter(([path]) => String(path).endsWith('/people')).length;
		expect(panelReads(), 'the panel read before the picture').toBe(0);

		const picture = host.querySelector('.swipe img, .swipe canvas, .swipe video');
		expect(picture, 'no picture drawn').not.toBeNull();
		// The still asks the server why it failed; the answer is not what this is about.
		vi.stubGlobal(
			'fetch',
			vi.fn(async () => new Response(null, { status: 404 }))
		);
		try {
			picture!.dispatchEvent(new Event('error'));
			flushSync();
			expect(panelReads(), 'a picture that failed held the panel for the whole wait').toBe(1);
			await Promise.resolve();
		} finally {
			vi.unstubAllGlobals();
		}
	});

	it('wears no history number until the thread answers, then its total', async () => {
		/*
		 * The thread is read once the picture is up (here, the panel's ceiling on waiting for it),
		 * never before; until then the tab says the word alone, so no number is drawn to be taken
		 * back.
		 */
		served.history = [1, 2, 3].map((at) => ({
			kind: 'added',
			what: 'Added from a folder',
			actor: 'sift',
			actor_name: null,
			detail: [],
			at,
			reversed: false,
			undo: null
		}));

		await show();
		expect(strip()).toEqual(['About2', 'Media1', 'History']);
		expect(served.historyAsks).toBe(0);

		await vi.waitFor(
			() => {
				flushSync();
				expect(strip()).toEqual(['About2', 'Media1', 'History3']);
			},
			{ timeout: PANEL_WAIT_MS + 1000 }
		);
		expect(served.historyAsks).toBe(1);
	});

	it('draws every record field, filled or not, and only the facts the file answers', async () => {
		/* Opposite answers to the same question, on purpose. An empty record field is something to
		   fill in; an empty machine fact is something the file does not carry: a photograph has no
		   frame rate, and a row of dashes for it is not an invitation. */
		await show();

		expect(labels()).toContain('Music');

		press('Media');

		expect(labels()).not.toContain('Frame rate');
	});
});

describe('a file nothing can compare', () => {
	/*
	 * A file whose frames the decoder refuses is whole enough to keep and to play, but Sift can
	 * never compare it to anything else. Said once, in the pane with the file's other measured
	 * facts, because "no near-copies found" and "never in the comparison" are otherwise the same
	 * silence.
	 */
	it('says so where the rest of what was measured off the file is said', async () => {
		served.detail = detail({
			fingerprint_verdict: 'this file could not be decoded (Invalid NAL unit size)'
		});

		await show();
		press('Media');

		expect(pane().textContent).toContain('no fingerprint');
		expect(pane().textContent).toContain('Invalid NAL unit size');
	});

	it('and says nothing at all about an ordinary file', async () => {
		// The known negative, and most of a library. A file whose fingerprints have merely not been
		// taken yet is waiting rather than refused, and the field carries the permanent answer only.
		await show();
		press('Media');

		expect(pane().textContent).not.toContain('no fingerprint');
	});
});

describe('the invitation on an empty field', () => {
	it('opens the form on the field that was pressed', async () => {
		await show();
		const add = [...pane().querySelectorAll('button')].find(
			(one) => one.getAttribute('aria-label') === 'Add Music'
		);
		if (!add) throw new Error('nothing invites a value');

		add.click();
		flushSync();

		// That one field alone, with the cross and the tick inside its box, not the whole form...
		expect(pane().querySelector('form')?.getAttribute('aria-label')).toBe('File info');
		expect(pane().querySelectorAll('.edit-marks input')).toHaveLength(1);
		// ...and the cursor is in it, rather than merely somewhere.
		await vi.waitFor(() =>
			expect((document.activeElement as HTMLElement | null)?.id.endsWith('-music-box')).toBe(true)
		);
	});

	it('sends that one field alone from its tick, so nothing else is written back', async () => {
		/* The route writes what it is sent. A body built from every drawn field would carry the
		   fields nobody opened, holding whatever the draft had for them, and write it over them. */
		await show();
		(pane().querySelector('button[aria-label="Add Music"]') as HTMLButtonElement).click();
		flushSync();
		const box = pane().querySelector('.edit-marks input') as HTMLInputElement;
		box.value = 'A tune';
		box.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		(pane().querySelector('button[aria-label="Save Music"]') as HTMLButtonElement).click();

		await vi.waitFor(() => expect(vi.mocked(api.put)).toHaveBeenCalled());
		expect(vi.mocked(api.put)).toHaveBeenCalledWith('/assets/a-1', { body: { music: 'A tune' } });
	});

	it('closes the other two panes while the boxes are up', async () => {
		/* The form lives in About. A press on Media would tear it down and take whatever was typed
		   with it, silently. So both of the others are closed off until Save or Cancel. */
		await show();
		pressEdit();

		const shut = [...host.querySelectorAll('[role="tab"]')].filter(
			(one) => (one as HTMLButtonElement).disabled
		);
		expect(shut.map((one) => one.textContent?.trim())).toEqual(['Media1', 'History']);
	});

	it("stands Edit at the row's start, and Save and Cancel in its place at the row's end", async () => {
		/* Edit opens the form from the START of the File info row. Pressed, it goes, and Cancel and
		   Save stand at the END (the heading's last column) until one of them is pressed; then Edit
		   is back at the start. */
		await show();
		const line = () =>
			[...host.querySelectorAll('.fold.section .heading-line')].find((one) =>
				one.textContent?.includes('File info')
			);
		const at = (end: '.leading' | '.actions') =>
			[...(line()?.querySelectorAll<HTMLButtonElement>(`:scope > ${end} button`) ?? [])].map(words);

		expect(at('.leading')).toEqual(['Edit']);
		expect(at('.actions')).toEqual([]);

		pressEdit();
		expect(at('.leading'), 'Edit stayed up beside Cancel and Save').toEqual([]);
		expect(at('.actions')).toEqual(['Cancel', 'Save']);

		const cancel = [...(line()?.querySelectorAll<HTMLButtonElement>('.actions button') ?? [])].find(
			(one) => words(one) === 'Cancel'
		);
		cancel?.click();
		flushSync();
		expect(at('.leading')).toEqual(['Edit']);
		expect(at('.actions')).toEqual([]);
	});
});

describe('what happened to the file', () => {
	it('is not asked for until its pane is opened, and then only once', async () => {
		/* A popout is opened far more often than its history is read, which is the whole reason it
		   is not fetched with the file. */
		served.history = [
			{
				kind: 'added',
				what: 'Added from Holiday',
				actor: 'sift',
				actor_name: null,
				detail: [],
				at: 1,
				reversed: false,
				undo: null
			}
		];
		await show();

		expect(served.historyAsks).toBe(0);

		press('History');
		await vi.waitFor(() => {
			flushSync();
			if (!pane().textContent?.includes('Added from')) throw new Error('not read yet');
		});
		press('About');
		press('History');

		expect(served.historyAsks).toBe(1);
	});

	it('says so when it cannot be read, and asks again on the next press', async () => {
		/* A history that could not be read is not an answer, so the way somebody retries is to open
		   the pane again. Without that the pane is permanently the failure it met once. */
		served.historyFails = true;
		await show();

		press('History');
		await vi.waitFor(() => {
			flushSync();
			if (!pane().textContent?.includes("couldn't be read")) throw new Error('no word yet');
		});

		served.historyFails = false;
		press('About');
		press('History');

		expect(served.historyAsks).toBe(2);
	});
});

describe('where a stash-box disagrees with the file', () => {
	it('is drawn at the top of the History pane, with both answers, as on a person', async () => {
		/* A box disagreeing about a file's title must not go unsaid on the file's page when a
		   person's, a Site's and a tag's History tab each draw the panel. The same panel, asked
		   about THIS file. */
		served.disagreements = [
			{
				subject: 'asset',
				local_id: 'a-1',
				name: 'a-picture.jpg',
				box_id: 'b-1',
				box_name: 'StashDB',
				key: 'title',
				mine: 'Golden hour',
				theirs: 'Blue hour'
			}
		];
		await show();

		press('History');
		await vi.waitFor(() => {
			flushSync();
			if (!pane().textContent?.includes('Blue hour')) throw new Error('not drawn yet');
		});

		expect(served.disagreementAsks).toEqual(['/stash-boxes/disagreements/asset/a-1']);
		const words = pane().textContent ?? '';
		expect(words).toContain('One field StashDB disagrees with');
		expect(words).toContain('Keep yours');
		expect(words).toContain('Take theirs');
	});

	it('marks the History tab before it is opened, from the count the file arrives with', async () => {
		/* The panel is inside the tab, so without a mark the file's page would say nothing about a
		   question waiting until History was opened. The count rides on the file, as the history's
		   number does. */
		served.detail = detail({ disagreements: 2, disagreement_boxes: ['StashDB'] });
		await show();

		const history = [...host.querySelectorAll('[role="tab"]')].find((one) =>
			one.textContent?.includes('History')
		);
		expect(history?.querySelector('.attention')).not.toBeNull();
		// The box by its name, as the panel inside the tab says it.
		expect(history?.querySelector('[aria-label="2 fields StashDB disagrees with"]')).not.toBeNull();
	});
});

describe('the file moving under an open pane', () => {
	it("reads the new file's history rather than leaving the old events up", async () => {
		/*
		 * Stepping to the next file, or randomizing, with History open must reload the pane:
		 * `showPane` runs only on a press, so without this the pane would sit on its skeleton. The
		 * other two panes read the record this view already holds.
		 */
		served.history = [
			{
				kind: 'added',
				what: 'Added from the first file',
				actor: 'sift',
				actor_name: null,
				detail: [],
				at: 1,
				reversed: false,
				undo: null
			}
		];
		await show();
		press('History');
		await vi.waitFor(() => {
			flushSync();
			if (!pane().textContent?.includes('the first file')) throw new Error('not read yet');
		});

		served.history = [
			{
				kind: 'added',
				what: 'Added from the second file',
				actor: 'sift',
				actor_name: null,
				detail: [],
				at: 2,
				reversed: false,
				undo: null
			}
		];
		showing.id = 'a-2';

		await vi.waitFor(() => {
			flushSync();
			if (!pane().textContent?.includes('the second file')) throw new Error('still the old one');
		});
		expect(served.historyAsks).toBe(2);
		// And the file before it is not still being reported under the new picture.
		expect(pane().textContent).not.toContain('the first file');
	});

	it('draws what happened OLDEST first, the newest at the bottom', async () => {
		/*
		 * History is listed oldest to newest, the order the read answers in, so the pane keeps no
		 * second, reversed copy.
		 */
		served.history = [
			{
				kind: 'added',
				what: 'Added first',
				actor: 'sift',
				actor_name: null,
				detail: [],
				at: 1,
				reversed: false,
				undo: null
			},
			{
				kind: 'renamed',
				what: 'Renamed last',
				actor: 'sift',
				actor_name: null,
				detail: [],
				at: 2,
				reversed: false,
				undo: null
			}
		];
		await show();

		press('History');
		await vi.waitFor(() => {
			flushSync();
			if (!pane().textContent?.includes('Renamed last')) throw new Error('not read yet');
		});

		const said = pane().textContent ?? '';
		expect(said.indexOf('Added first')).toBeLessThan(said.indexOf('Renamed last'));
	});
});

function line(what: string, at: number): Record<string, unknown> {
	return {
		kind: 'edited',
		what,
		actor: 'sift',
		actor_name: null,
		detail: [],
		at,
		reversed: false,
		undo: null
	};
}

describe('a write made while the pane is open', () => {
	it('reads the thread again, without blanking it, when this tab writes something', async () => {
		/*
		 * A file added to a collection must show in its history right away. Every write this tab
		 * makes rings `recorded`, because the server does not announce a collection add to an
		 * account whose view it did not move.
		 */
		served.history = [line('Added from the library', 1)];
		await show();
		press('History');
		await vi.waitFor(() => {
			flushSync();
			if (!pane().textContent?.includes('Added from the library')) throw new Error('not read yet');
		});

		served.history = [line('Added from the library', 1), line('Put in Shelf One', 2)];
		recorded.changed();
		// Still the previous thread while the new one is on its way: no busy line in between.
		flushSync();
		expect(pane().textContent).toContain('Added from the library');

		await vi.waitFor(() => {
			flushSync();
			if (!pane().textContent?.includes('Put in Shelf One')) throw new Error('not re-read');
		});
		expect(served.historyAsks).toBe(2);
	});

	it('costs nothing for a file whose history was never opened', async () => {
		await show();
		recorded.changed();
		await new Promise((done) => setTimeout(done, 400));
		expect(served.historyAsks).toBe(0);
	});

	it('reads itself once for a burst of bells, whichever bell rang', async () => {
		served.history = [line('Added from the library', 1)];
		await show();
		press('History');
		await vi.waitFor(() => {
			flushSync();
			if (!pane().textContent?.includes('Added from the library')) throw new Error('not read yet');
		});
		const before = served.historyAsks;

		for (const bell of [libraryChanges, jobChanges, recorded, libraryChanges, jobChanges]) {
			bell.changed();
			flushSync();
			await new Promise((done) => setTimeout(done, 60));
		}
		await new Promise((done) => setTimeout(done, 600));

		expect(served.historyAsks - before).toBe(1);
	});

	it('reads itself at most once a second while the library keeps ringing', async () => {
		served.history = [line('Added from the library', 1)];
		await show();
		press('History');
		await vi.waitFor(() => {
			flushSync();
			if (!pane().textContent?.includes('Added from the library')) throw new Error('not read yet');
		});
		const before = served.historyAsks;

		// A bell every 300 ms for 1.8 s: more than the settle apart, so each one alone would be a read.
		for (let rung = 0; rung < 6; rung += 1) {
			libraryChanges.changed();
			flushSync();
			await new Promise((done) => setTimeout(done, 300));
		}
		await new Promise((done) => setTimeout(done, 1200));

		// Six bells over three seconds: at most one read a second, never a read each.
		expect(served.historyAsks - before).toBeGreaterThanOrEqual(1);
		expect(served.historyAsks - before).toBeLessThanOrEqual(3);
	});
});

describe('Show earlier, at the cut end of a long history', () => {
	it('is offered while the server holds more, says how many, and asks for them', async () => {
		/* The read keeps the NEWEST events and answers them oldest first, so with the newest at the
		   bottom what a long history is missing is its top, and the word to reach it goes there. */
		served.history = Array.from({ length: 60 }, (_, at) => line(`Event ${at}`, at));
		await show();
		press('History');
		await vi.waitFor(() => {
			flushSync();
			if (!pane().textContent?.includes('Event 59')) throw new Error('not read yet');
		});
		expect(served.historyLimits).toEqual([50]);
		// The tab wears the whole history, never the fifty drawn.
		expect(strip()).toContain('History60');

		const earlier = [...pane().querySelectorAll('button')].find((one) =>
			one.textContent?.includes('Show 10 earlier')
		);
		if (!(earlier instanceof HTMLElement)) throw new Error('no Show earlier');
		// And it sits above the thread, at the end that is cut.
		expect(
			earlier.compareDocumentPosition(pane().querySelector('li') as Node) &
				Node.DOCUMENT_POSITION_FOLLOWING
		).toBeTruthy();

		earlier.click();
		await vi.waitFor(() => {
			flushSync();
			if (served.historyAsks < 2) throw new Error('not asked');
		});
		expect(served.historyLimits).toEqual([50, 100]);
		// Sixty came back of sixty: that is the whole of it, and the word goes.
		await vi.waitFor(() => {
			flushSync();
			const still = [...pane().querySelectorAll('button')].some((one) =>
				one.textContent?.includes('earlier')
			);
			if (still) throw new Error('still offered');
		});
		expect(pane().textContent).toContain('Event 0');
	});

	it('is not offered for a history of exactly one page', async () => {
		served.history = Array.from({ length: 50 }, (_, at) => line(`Event ${at}`, at));
		await show();
		press('History');
		await vi.waitFor(() => {
			flushSync();
			if (!pane().textContent?.includes('Event 49')) throw new Error('not read yet');
		});
		expect(pane().textContent).not.toContain('earlier');
		expect(strip()).toContain('History50');
	});

	it('is not offered for a history shorter than a page', async () => {
		served.history = [line('Only one', 1)];
		await show();
		press('History');
		await vi.waitFor(() => {
			flushSync();
			if (!pane().textContent?.includes('Only one')) throw new Error('not read yet');
		});
		expect(pane().textContent).not.toContain('Show earlier');
	});
});

describe("the pane, which is this opening's and not the account's", () => {
	it('opens on About however the account left it', async () => {
		/*
		 * A popout is opened to look at the file, so its record always comes up on About. The
		 * per-account `popout.record_tab` key is not in the server's accepted list, and a client
		 * still holding it must not be obeyed either.
		 */
		served.interface = { 'popout.record_tab': 'media' };
		await show();

		expect(labels()).toEqual(['Title', 'Music', 'Filename']);
	});

	it('holds the pane while the file moves under it, which is where the habit really is', async () => {
		/* The pane is held within one opening. Somebody stepping through a run of clips checking codecs
		   presses Media once, not once per file. */
		await show();

		press('Media');
		showing.id = 'a-2';
		await vi.waitFor(() => {
			flushSync();
			if (!labels().includes('Container')) throw new Error('not on Media any more');
		});
	});

	it('writes nothing to the account when a pane is pressed', async () => {
		// The server does not accept this key. A client writing it would be refused, and this is
		// what stops it being written at all.
		await show();

		press('Media');

		expect(recordPuts()).toHaveLength(0);
	});
});

describe("the fold on the file's own row", () => {
	/** The control between the name and the buttons.
	 *
	 * Found by its `aria-expanded` and not by its words: a `Button`'s glyph is a LIGATURE, so the
	 * element's text reads "expand_lessCollapse" and an anchored match on the word finds nothing. */
	function toggle(): HTMLButtonElement {
		const found = host.querySelector<HTMLButtonElement>('.acts > button[aria-expanded]');
		if (!found) throw new Error('there is no Expand control');
		return found;
	}

	/** Mounted, without waiting for a record that may be folded away. */
	async function shown(): Promise<void> {
		host = document.createElement('div');
		document.body.appendChild(host);
		instance = mount(AssetViewProbe, { target: host });
		await vi.waitFor(() => {
			flushSync();
			if (!host.querySelector('.acts')) throw new Error('nothing drawn yet');
		});
	}

	it('opens showing everything, and says so where it can be heard', async () => {
		/* The default is open: a panel that starts folded and unfolds a moment later reads as broken.
		   `aria-expanded` is what makes this a disclosure rather than a button whose word changes:
		   somebody who cannot see the block open is told that it is open. */
		await show();

		expect(toggle().textContent).toContain('Collapse');
		expect(toggle().getAttribute('aria-expanded')).toBe('true');
		// In the middle of the name's row, between the name and the buttons: the one place it
		// stands, which is what keeps it on the centre line.
		expect(toggle().previousElementSibling?.classList.contains('named')).toBe(true);
		expect(toggle().nextElementSibling?.classList.contains('ends')).toBe(true);
		expect(toggle().closest('.section-heading')).toBeNull();
		const controls = toggle().getAttribute('aria-controls');
		expect(host.querySelector(`#${controls}`)?.querySelector('[role="tablist"]')).not.toBeNull();
	});

	it('folds everything under the row away, and remembers it for the account', async () => {
		/*
		 * One control on the name's line, remembered each time it is opened or closed. Written
		 * through to the account rather than this browser, because how much of a screen somebody
		 * wants does not change between machines.
		 */
		await show();

		toggle().click();
		flushSync();

		expect(toggle().textContent).toContain('Expand');
		expect(toggle().getAttribute('aria-expanded')).toBe('false');
		expect(vi.mocked(api.put)).toHaveBeenCalledWith('/settings/interface', {
			body: { state: { 'popout.expanded': 'shut' } }
		});
	});

	it('opens folded for an account that left it that way', async () => {
		/* The other direction, and the reason the value is a WORD rather than a flag: an absent row
		   means open, so "shut" has to be storable. */
		served.interface = { 'popout.expanded': 'shut' };
		await shown();

		await vi.waitFor(() => {
			flushSync();
			if (toggle().getAttribute('aria-expanded') !== 'false') throw new Error('still open');
		});
	});
});

/** An element's words, without the icon glyphs drawn beside them (the font's private-use points). */
function words(one: Element): string {
	return (one.textContent ?? '').replace(/[\uE000-\uF8FF]/g, '').trim();
}

describe('the sections under the picture', () => {
	/* Each section folds under its own heading, beside the words, and this browser keeps the last
	   press. Enrichment is what the file is filed under; File info is the record. */
	const KEYS = ['sift.file.fold.enrichment', 'sift.file.fold.record'];
	beforeEach(() => KEYS.forEach((key) => localStorage.removeItem(key)));
	afterEach(() => KEYS.forEach((key) => localStorage.removeItem(key)));

	function arrow(section: string): HTMLButtonElement {
		// The words and the arrow are one press now, so the press is found by its words.
		const found = [
			...host.querySelectorAll<HTMLButtonElement>('.section-heading button[aria-expanded]')
		].find((one) => (one.getAttribute('aria-label') ?? words(one)) === section);
		if (!found) throw new Error(`${section} has no arrow`);
		return found;
	}

	function headings(): string[] {
		return [...host.querySelectorAll('.under .section-heading h3')].map(words);
	}

	it('heads what the file is filed under as Enrichment, first, and folds it', async () => {
		served.people = [{ id: 'p-1', name: 'Esme Wrenfield' }];
		await show();
		await vi.waitFor(() => {
			flushSync();
			if (!host.querySelector('.entities')) throw new Error('no rows yet');
		});

		expect(headings()[0]).toBe('Enrichment');
		expect(headings()).not.toContain('About this file');
		expect(arrow('Enrichment').getAttribute('aria-expanded')).toBe('true');

		arrow('Enrichment').click();
		flushSync();

		expect(arrow('Enrichment').getAttribute('aria-expanded')).toBe('false');
		await vi.waitFor(() => expect(host.querySelector('.entities')).toBeNull());
		expect(localStorage.getItem('sift.file.fold.enrichment')).toBe('no');
	});

	it('folds the record, takes Edit with it, and closes a form it folds away', async () => {
		/* The heading's controls act on what it holds, so they go with it. A form shut away
		   mid-edit is closed rather than left open behind a Save nobody can read. */
		await show();
		const edit = () =>
			[...host.querySelectorAll('button')].find((one) => one.textContent?.trim().endsWith('Edit'));
		const save = () =>
			[...host.querySelectorAll('button')].find((one) => one.textContent?.trim().endsWith('Save'));
		edit()?.click();
		flushSync();
		expect(save()).toBeDefined();

		arrow('File info').click();
		flushSync();

		await vi.waitFor(() => expect(host.querySelector('[role="tablist"]')).toBeNull());
		expect(save()).toBeUndefined();
		expect(edit()).toBeUndefined();
		expect(localStorage.getItem('sift.file.fold.record')).toBe('no');

		arrow('File info').click();
		flushSync();
		expect(save()).toBeUndefined();
		expect(edit()).toBeDefined();
	});

	it('opens a section shut where this browser left it shut', async () => {
		localStorage.setItem('sift.file.fold.record', 'no');
		await shown();
		await vi.waitFor(() => {
			flushSync();
			arrow('File info');
		});

		expect(arrow('File info').getAttribute('aria-expanded')).toBe('false');
		expect(host.querySelector('[role="tablist"]')).toBeNull();
	});

	async function shown(): Promise<void> {
		host = document.createElement('div');
		document.body.appendChild(host);
		instance = mount(AssetViewProbe, { target: host });
		await vi.waitFor(() => {
			flushSync();
			if (!host.querySelector('.acts')) throw new Error('nothing drawn yet');
		});
	}
});

describe('run task on the file', () => {
	/*
	 * "Run task" on the file's context menu: the stage's press as Settings words it, then the pass,
	 * or the stage's every pass first in its flyout. "Look for faces again" is one row of it.
	 */
	function row(named: string): HTMLElement | undefined {
		return [...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].find(
			(one) => (one.textContent ?? '').replace(/[\uE000-\uF8FF]/g, '').trim() === named
		);
	}

	async function open(named: string): Promise<void> {
		const found = await vi.waitFor(() => {
			flushSync();
			const one = row(named);
			if (!one) throw new Error(`no row "${named}"`);
			return one;
		});
		/* The way a pointer does it: bits-ui's sub trigger answers the pointer sequence. */
		found.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0 }));
		found.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, button: 0 }));
		found.click();
		flushSync();
	}

	async function pressFaces(pass = 'Faces'): Promise<void> {
		const door = host.querySelector<HTMLButtonElement>('[aria-label="Options for this file"]');
		if (!door) throw new Error('the file has no menu');
		door.click();
		await open('Run task');
		await open('Identify now');
		await open(pass);
	}

	it("runs the stage's every pass in one request and says the server's one sentence", async () => {
		const said = vi.spyOn(toasts, 'show');
		const before = vi.mocked(api.post).getMockImplementation();
		vi.mocked(api.post).mockImplementation(async (path: string) => {
			if (path === '/assets/run') return { said: 'Identifying 1 file: faces.' };
			return { changed: 1, skipped: 0, reason: null };
		});
		await show();

		await pressFaces('Identify all');

		await vi.waitFor(() => expect(said).toHaveBeenCalledWith('Identifying 1 file: faces.'));
		const runs = vi.mocked(api.post).mock.calls.filter(([path]) => path === '/assets/run');
		expect(runs).toEqual([['/assets/run', { body: { run: 'identify:all', asset_ids: ['a-1'] } }]]);
		said.mockRestore();
		if (before) vi.mocked(api.post).mockImplementation(before);
	});

	it('asks for the pass on this file and says what the server started', async () => {
		const said = vi.spyOn(toasts, 'show');
		const before = vi.mocked(api.post).getMockImplementation();
		vi.mocked(api.post).mockImplementation(async (path: string) => {
			if (path === '/assets/run') return { said: 'Looking for faces in 1 file.' };
			return { changed: 1, skipped: 0, reason: null };
		});
		await show();

		await pressFaces();

		await vi.waitFor(() => expect(said).toHaveBeenCalledWith('Looking for faces in 1 file.'));
		expect(vi.mocked(api.post)).toHaveBeenCalledWith('/assets/run', {
			body: { run: 'faces', asset_ids: ['a-1'] }
		});
		expect(row('Look for faces again'), 'the one-off row is gone').toBeUndefined();
		said.mockRestore();
		if (before) vi.mocked(api.post).mockImplementation(before);
	});

	it("shows the server's own sentence when it refuses", async () => {
		/* The refusals are written for the person pressing (a switch off, the card missing, the
		   file already waiting) and a flat "could not" would leave them nothing to act on. */
		const said = vi.spyOn(toasts, 'show');
		/* By address, not "the next post": the view reports a sitting of its own on the way in, and
		   a once-only refusal is spent on that. */
		const before = vi.mocked(api.post).getMockImplementation();
		vi.mocked(api.post).mockImplementation(async (path: string) => {
			if (path === '/assets/run') {
				throw new ApiError(409, 'no', 'Nothing was queued: this file is already waiting for it.');
			}
			return { changed: 1, skipped: 0, reason: null };
		});
		await show();

		await pressFaces();

		await vi.waitFor(() =>
			expect(said).toHaveBeenCalledWith(
				'Nothing was queued: this file is already waiting for it.',
				{ tone: 'error' }
			)
		);
		said.mockRestore();
		if (before) vi.mocked(api.post).mockImplementation(before);
	});
});

describe('deleting the file that is open', () => {
	/** Every row of the file's own menu, in the order it is drawn. */
	async function menuRows(): Promise<string[]> {
		const door = host.querySelector<HTMLButtonElement>(
			'[aria-label="Options for this file"], button[aria-label="Options for this file"]'
		);
		if (!door) throw new Error('the file has no menu');
		door.click();
		await vi.waitFor(() => {
			flushSync();
			if (!document.querySelector('[role="menu"]')) throw new Error('the menu has not opened');
		});
		return [...document.querySelectorAll('[role="menu"] [role="menuitem"]')].map(
			(one) => one.textContent?.trim() ?? ''
		);
	}

	it('offers Remove, and offers it LAST', async () => {
		/*
		 * Remove on the screen showing one file, so a file can be taken out without closing the
		 * panel and finding its tile again. Last on the menu so it is not hit on the way to Rename,
		 * Trim and Compress. Remove, because the question it opens answers Remove first.
		 */
		await show();

		const rows = await menuRows();

		expect(rows.some((row) => row.includes('Remove'))).toBe(true);
		expect(rows[rows.length - 1]).toContain('Remove');
	});

	it('puts Remove in a group of its own, the way every destructive row in the app is fenced', async () => {
		/* Not written here: `menuGroups` makes a destructive verb its own group, last, and
		   `ContextMenuGroup` draws the line in front of every group but the first. */
		await show();

		await menuRows();

		const groups = [...document.querySelectorAll('[role="menu"] .menu-group')];
		const last = groups.at(-1);
		expect(last?.querySelectorAll('[role="menuitem"]')).toHaveLength(1);
		expect(last?.textContent).toContain('Remove');
		expect(last?.querySelector('.menu-separator')).not.toBeNull();
	});

	it('offers the three enrichment rows the same file offers on its tile', async () => {
		/* One declaration, two doors: a file's Options menu missing what its tile menu offers
		   is two vocabularies for one file. */
		await show();

		const rows = await menuRows();

		for (const word of ['Auto-enrich', 'Enrich', "Don't enrich"]) {
			expect(
				rows.some((row) => row.includes(word)),
				`${word} is missing`
			).toBe(true);
		}
	});

	it('tells its frame the file has gone, rather than re-reading it into a 404', async () => {
		/*
		 * `forget` is what a file being taken away reaches: a delete is not a change to the file,
		 * and `around.refresh` would re-read, meet a 404 and draw "Not found" in a dialog that was
		 * working. `forget` is the host's seam for "stop showing this", so every verb that takes a
		 * file away reaches it.
		 *
		 * What to do about it is the frame's (a popout over a wall steps on, and closes when there
		 * is nowhere to step). This harness is not a frame, so what is asserted is the report.
		 */
		await show();

		const rows = await menuRows();
		const del = [...document.querySelectorAll<HTMLElement>('[role="menu"] [role="menuitem"]')][
			rows.length - 1
		];
		del.click();

		const confirm = await vi.waitFor(() => {
			flushSync();
			const found = [...document.querySelectorAll<HTMLButtonElement>('button')].find((one) =>
				['Remove from Sift', 'Delete from disk'].includes(one.textContent?.trim() ?? '')
			);
			if (!found) throw new Error('nothing to agree to yet');
			return found;
		});
		confirm.click();

		await vi.waitFor(() => {
			flushSync();
			expect(wentAway.ids).toEqual(['a-1']);
		});
	});
});

describe('the Filename box, which is the one field that touches the disk', () => {
	/*
	 * The record's own write is a PUT that sets columns. A filename is not a column: changing it
	 * renames the file in the folder, which is the organize verb's job, can be refused for reasons
	 * no other field has, and is written down in one place. So the box is a real edit and the save
	 * sends it somewhere else, and the two halves of that ("it is left out of the record body"
	 * and "it goes to the rename route") are asserted together, because either on its own passes
	 * while the box quietly does nothing.
	 */

	/** The boxes, opened from Edit on the heading. Every field is in the one form. */
	async function openTheBoxes(): Promise<void> {
		await show();
		pressEdit();
	}

	function box(key: string): HTMLInputElement {
		const found = [...pane().querySelectorAll('input')].find((one) =>
			one.id.endsWith(`-${key}-box`)
		);
		if (!(found instanceof HTMLInputElement)) throw new Error(`there is no ${key} box`);
		return found;
	}

	function type(key: string, words: string): void {
		const into = box(key);
		into.value = words;
		into.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
	}

	/** Every route the view has POSTed to. The view ping goes through the same verb. */
	function posted(): string[] {
		return vi.mocked(api.post).mock.calls.map((call) => String(call[0]));
	}

	/** The rename route refuses, in the words the organize verb writes; everything else answers. */
	function refuseTheRename(said: string): void {
		vi.mocked(api.post).mockImplementation((async (path: string) => {
			if (path.endsWith('/rename')) throw new ApiError(409, 'no', said);
			return { changed: 1, skipped: 0, reason: null };
		}) as unknown as typeof api.post);
	}

	async function saveTheBoxes(): Promise<void> {
		/* Looked for in the whole view rather than in the form: the record's Save is drawn OUTSIDE
		   it and reaches it by `form="..."`, so that a record long enough to scroll does not push
		   its own Save off the screen. */
		const save = host.querySelector('button[type="submit"]');
		if (!(save instanceof HTMLElement)) throw new Error('there is no Save');
		save.click();
		await vi.waitFor(() => {
			flushSync();
			if (recordPuts().length === 0) throw new Error('nothing was written yet');
		});
	}

	it('sends a changed name to the rename route and leaves it out of the record', async () => {
		await openTheBoxes();

		type('filename', 'sunset-walk.jpg');
		await saveTheBoxes();

		const body = recordPuts()[0][1]?.body as Record<string, unknown>;
		expect(body).not.toHaveProperty('filename');
		expect(api.post).toHaveBeenCalledWith('/assets/a-1/rename', {
			body: { name: 'sunset-walk.jpg' }
		});
	});

	it('does not rename a file to the name it already has', async () => {
		// A write against the filesystem for nothing, which the verb refuses anyway with a sentence
		// that is true and useless.
		await openTheBoxes();

		type('music', 'Something quiet');
		await saveTheBoxes();

		/* Asked of the rename route by name rather than of `post` as a whole: the view also reports
		   that the file was looked at, through the same verb, and a test that forbade every POST
		   would be about that instead. */
		expect(posted()).not.toContain('/assets/a-1/rename');
	});

	it('writes the other fields first, so a refused rename does not take them with it', async () => {
		/* The order, asserted as behaviour rather than as a comment. A rename that went first would
		   mean one bad character in the name threw away a Title typed in the same sitting. */
		refuseTheRename('There is already something called that.');
		await openTheBoxes();

		type('music', 'Something quiet');
		type('filename', 'taken.jpg');
		await saveTheBoxes();

		const body = recordPuts()[0][1]?.body as Record<string, unknown>;
		expect(body.music).toBe('Something quiet');
	});

	it('shows the refusal in the words the server wrote, with the boxes still up', async () => {
		/* "That could not be saved." would be the form's flat sentence, and it leaves somebody
		   re-pressing Save with no idea what to change. */
		refuseTheRename('There is already something called that.');
		await openTheBoxes();

		type('filename', 'taken.jpg');
		await saveTheBoxes();

		await vi.waitFor(() => {
			flushSync();
			if (!pane().textContent?.includes('There is already something called that.'))
				throw new Error('the refusal is not on screen');
		});
		expect(pane().querySelector('form')).not.toBeNull();
		expect(box('filename').value).toBe('taken.jpg');
	});
});

/* The chips under the file are drawn from rows this view already holds, and those rows name the
   cover: handed to `entityPicture`, the address carries the row's token and the chosen file, which
   is the only address the server lets the browser keep (`kernel/covers.py names_its_cover`). A chip
   drawn on the bare address would be re-asked on every file opened. */
describe('the chips under the file', () => {
	it("draw a person and a collection on the address that names each one's cover", async () => {
		served.people = [
			{ id: 'p-1', name: 'Neve Arbogast', cover_asset_id: 'a-9', cover_at_ms: null, art: 'st' }
		];
		served.collections = [
			{ id: 'c-1', name: 'Shelf', cover_upload_id: 'u-4', cover_asset_id: null, art: 'st' }
		];
		await show();
		await vi.waitFor(() => {
			flushSync();
			const sources = [...host.querySelectorAll('img')].map((one) => one.getAttribute('src'));
			expect(sources).toContain('/api/people/p-1/cover?v=st.a-9');
			expect(sources).toContain('/api/collections/c-1/cover?v=st.u-4');
		});
	});

	it("draw a site it is filed under on the address that names the site's logo", async () => {
		/* The filings route carries the site's cover fields and, while nobody has chosen the site a
		   picture, the shipped logo's token: the one address the server keeps that logo under
		   (`kernel/covers.py names_the_shipped`). */
		served.filings = [
			{
				username_id: 'ac-1',
				site_id: 's-1',
				site: 'Northlight Raw',
				username: null,
				person_id: null,
				source: null,
				source_name: null,
				art: 'st',
				cover_asset_id: null,
				cover_upload_id: null,
				cover_at_ms: null,
				icon: 'dev-0123456789abcdef'
			}
		];
		await show();
		await vi.waitFor(() => {
			flushSync();
			const sources = [...host.querySelectorAll('img')].map((one) => one.getAttribute('src'));
			expect(sources).toContain('/api/sites/s-1/cover?v=st.dev-0123456789abcdef');
		});
	});

	it("draw the file's song as a chip that opens the song's page", async () => {
		/* A song with no cover: its chip leads to its page and wears the music glyph rather than
		   the song's letter. */
		served.songs = [{ id: 'g-1', name: 'Lantern Hum', cover_asset_id: null, art: 'st' }];
		await show();
		await vi.waitFor(() => {
			flushSync();
			const chip = host.querySelector<HTMLAnchorElement>('a[href="/songs/g-1"]');
			expect(chip?.textContent).toContain('Lantern Hum');
			expect(chip?.querySelector('.monogram .icon')).not.toBeNull();
		});
	});
});

/*
 * WHERE THE SONG'S NAME CAME FROM, under the Music field itself: the file screen's half of the
 * line `MusicSource` draws. Its sentences are that component's test; what is held here is that the
 * screen hands it the file's own `music_source` and the one Site it is filed under, and that the
 * line lands in the Music cell rather than anywhere else on the record.
 */
describe('the line under Music', () => {
	function filing(site: string, id: string) {
		return {
			username_id: `ac-${id}`,
			site_id: id,
			site,
			username: null,
			person_id: null,
			source: null,
			source_name: null,
			art: null,
			cover_asset_id: null,
			cover_upload_id: null,
			cover_at_ms: null,
			icon: null
		};
	}

	/** The line drawn inside the Music cell, under its value; empty where there is none. */
	function musicLine(): string {
		const row = [...pane().querySelectorAll('.fact')].find(
			(one) => one.querySelector('dt')?.textContent?.trim() === 'Music'
		);
		if (!row) throw new Error('there is no Music row');
		return row.querySelector('dd .said')?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
	}

	it('names the one Site the file is filed under as the page that named its song', async () => {
		served.detail = detail({ music: 'A song - A band', music_source: 'site', music_from: null });
		served.filings = [filing('Northlight Raw', 's-1')];

		await show();

		await vi.waitFor(() => {
			flushSync();
			expect(musicLine()).toBe("Named from Northlight Raw's page");
		});
	});

	it('says its download page where the file is under two Sites', async () => {
		served.detail = detail({ music: 'A song - A band', music_source: 'site', music_from: null });
		served.filings = [filing('Northlight Raw', 's-1'), filing('Bramblecast', 's-2')];

		await show();

		await vi.waitFor(() => {
			flushSync();
			expect(musicLine()).toBe('Named from its download page');
		});
	});

	it('draws nothing under a name somebody typed', async () => {
		served.detail = detail({ music: 'A song - A band', music_source: 'typed', music_from: null });

		await show();
		flushSync();

		expect(pane().textContent).toContain('A song - A band');
		expect(musicLine()).toBe('');
	});
});

describe('the fold on a file that is on nothing', () => {
	const NOTHING = 'No People, Sites, Collections, Photo Sets, tags or song on this file yet.';

	/* A fold that opens onto nothing reads as a fold that failed; it says what is missing, and only
	   once the lists have answered. */
	it('says what is missing when the file is on nothing', async () => {
		await show();
		await vi.waitFor(() => {
			flushSync();
			if (!host.querySelector('.entities')?.textContent?.includes(NOTHING)) {
				throw new Error('not said yet');
			}
		});
	});

	it('says nothing of the kind when somebody is on it', async () => {
		served.people = [
			{ id: 'p-1', name: 'Neve Arbogast', cover_asset_id: 'a-9', cover_at_ms: null, art: 'st' }
		];
		await show();
		await vi.waitFor(() => {
			flushSync();
			if (!host.querySelector('.entities')?.textContent?.includes('Neve Arbogast')) {
				throw new Error('no person yet');
			}
		});
		expect(host.querySelector('.entities')?.textContent).not.toContain(NOTHING);
	});
});

/** Edit, on the File info heading: the whole record as boxes. */
function pressEdit(): void {
	const edit = [...host.querySelectorAll<HTMLButtonElement>('.section-heading button')].find(
		(one) => words(one) === 'Edit'
	);
	if (!edit) throw new Error('there is no Edit');
	edit.click();
	flushSync();
}
