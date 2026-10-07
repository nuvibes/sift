/* What a download row says, and what it offers to do about it.
 *
 * ## The words
 *
 * The row does not own most of these: the state, its colour and its word live in `Badge`, because
 * the jobs list shows the same states and two screens each deciding what amber means is how one
 * of them ends up disagreeing. What the row owns is the handful of cases where it genuinely knows
 * more than the state does: a download is only ever blocked on COOKIES, and `skipped` and
 * `duplicate` are not job states at all.
 *
 * So the line this file draws is between an override that adds a FACT and one that is a synonym.
 * A second name for the same status ("Set aside" for "Quarantined") is not more information, and
 * a second name is the thing the shared component exists to prevent. The same rule is why
 * `queued` reads "Queued" and not "Waiting": that would be a synonym, and the second line of the
 * figures column is where a waiting row's extra fact belongs.
 *
 * ## The verbs
 *
 * Every act the row offers is declared once and rendered three ways: the glyph buttons, the
 * three-dot menu and the right-click. The property worth testing is not that a button exists but
 * that THE THREE CANNOT COME APART, so the sweep below opens the menu on every state and checks
 * that everything visible on the row is in it. A row that wrote its own buttons would pass any
 * assertion about a button existing.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { words } from '$lib/design/testing.svelte';
import { exactly } from '$lib/shell/when';
import codepoints from '$lib/generated/icon-codepoints.json';
import { applyStyles, removeStyles } from '$lib/design/testing-styles';

import DownloadRow from './DownloadRowProbe.test.svelte';
import { downloadColumns } from './DownloadRow.svelte';
import rowSource from './DownloadRow.svelte?raw';
import type { DownloadItem } from './queue.svelte';

let host: HTMLElement;
let instance: ReturnType<typeof mount> | null = null;

function takeDown() {
	if (instance) unmount(instance);
	instance = null;
	host?.remove();
	document.body.innerHTML = '';
}

afterEach(takeDown);

/* Every field, spelled out. A partial cast would compile and then hand the component `undefined`
   where it reads a string, which fails as a blank row rather than as a missing field. */
function item(status: string, known: Partial<DownloadItem> = {}): DownloadItem {
	return {
		// Where in the line a waiting row stands; null for every other state.
		position: null,
		// The job that runs it, for the Activity screen's link; null once nothing does.
		job_id: null,
		// The failure sentence the server chooses; null is what every row not failed carries.
		sentence: null,
		username: null,
		asset_id: null,
		created_at: 0,
		creator_scope: null,
		dest_folder_id: null,
		folder: null,
		error: null,
		error_code: null,
		error_tier: null,
		filename: 'clip.mp4',
		// How many an album offered and lost for good; null on a row that lost none.
		files_offered: null,
		files_left_out: null,
		reads_refused: null,
		finished_at: null,
		id: 'one',
		person_id: null,
		site: null,
		site_id: null,
		progress: null,
		remembered_filename: null,
		shown_url: 'example.test/clip',
		site_key: null,
		site_name: null,
		size_bytes: null,
		status,
		url: 'https://example.test/clip',
		via: null,
		via_address: null,
		...known
	};
}

/* Every progress field, for the same reason the item above spells every one of its own out: a
   partial literal compiles as a wider type and then hands the component `undefined` where it reads
   a number, which fails as a blank figure rather than as a missing field. */
function progress(known: Partial<NonNullable<DownloadItem['progress']>> = {}) {
	return {
		bytes_per_second: null,
		done_bytes: 0,
		done_files: 0,
		seconds_left: null,
		total_bytes: null,
		total_files: null,
		total_is_estimated: false,
		...known
	};
}

/* Every handler, so a state's verbs are decided by the STATE rather than by which handler a test
   remembered to pass. A verb with no handler is deliberately not built at all (that is how a
   caller says "this list does not do that"), so a sweep missing one would report a missing verb. */
const HANDLERS = {
	oncookies: vi.fn(),
	oncancel: vi.fn(),
	onpause: vi.fn(),
	onresume: vi.fn(),
	onretry: vi.fn(),
	onfirst: vi.fn(),
	onanyway: vi.fn(),
	onremove: vi.fn(),
	onopen: vi.fn(),
	onexpand: vi.fn(),
	onselect: vi.fn()
};

function render(one: DownloadItem, more: Record<string, unknown> = {}) {
	takeDown();
	host = document.createElement('div');
	document.body.append(host);
	instance = mount(DownloadRow, {
		target: host,
		props: { item: one, now: 600, ...HANDLERS, ...more }
	});
	flushSync();
}

/** The glyph buttons on the row, by the name each one says out loud. The three dots is not one of
 *  them: it is the door to the rest. The chevron is not among them either: `DataRow` draws it at
 *  the end of the row. */
function glyphs(): string[] {
	const actions = host.querySelector('.controls');
	return [...(actions?.querySelectorAll('button[aria-label]') ?? [])]
		.map((button) => button.getAttribute('aria-label') ?? '')
		.filter((name) => !name.startsWith('More for'));
}

/** The one verb with words on it: the fix, where the row has one. */
function fix(): string | null {
	const button = host.querySelector('.fix button');
	return button ? words(button) : null;
}

/** The rows behind the three dots, which is where every verb has to be. */
async function menu(): Promise<string[]> {
	const more = host.querySelector('button.more') as HTMLButtonElement | null;
	more?.click();
	let rows: string[] = [];
	await vi.waitFor(() => {
		// The icon is a LIGATURE, so its glyph is a character of the row's own text: a private-use
		// codepoint sitting in front of the words.
		rows = [...document.querySelectorAll('[role="menuitem"]')].map((row) =>
			(row.textContent ?? '').replace(/[\uE000-\uF8FF]/g, '').trim()
		);
		expect(rows.length).toBeGreaterThan(0);
	});
	return rows;
}

function word(status: string): string {
	render(item(status));
	return words(host.querySelector('.badge'));
}

describe('the states the row says for itself, because it knows more than the state does', () => {
	it.each([
		// "Waiting for cookies", not "Waiting for login": Sift signs in to nothing and holds no
		// account. It is handed an export of a browser's cookies, so "login" names a thing this
		// surface does not have.
		['blocked', 'Waiting for cookies'],
		['skipped', 'Skipped'],
		['duplicate', 'Already in library']
	])('%s reads "%s"', (status, said) => {
		expect(word(status)).toBe(said);
	});

	it('says nothing about a login anywhere on the row', () => {
		render(item('blocked', { site: 'example.test' }));
		expect(host.textContent?.toLowerCase()).not.toContain('login');
		expect(host.textContent).toContain('Add cookies');
	});
});

describe('the states whose word comes from Badge, so both screens say one thing', () => {
	it.each([
		['queued', 'Queued'],
		['running', 'In progress'],
		// Not "Held", not "On hold": the jobs list says "Paused" for the same state, and a second
		// wording here is exactly the drift the shared component exists to prevent.
		['paused', 'Paused'],
		['done', 'Done'],
		['canceled', 'Canceled'],
		['failed', 'Failed'],
		['quarantined', 'Quarantined']
	])('%s reads "%s"', (status, said) => {
		expect(word(status)).toBe(said);
	});
});

describe('a status this build has never heard of', () => {
	/* Queued, not blank and not the raw word. A server one version ahead can send a status this
	   build has no case for, and the row still has to say something a person can read. */
	it('is drawn as queued rather than as nothing', () => {
		expect(word('some_status_from_a_later_build')).toBe('Queued');
	});
});

describe('the three marks this screen knows better than the state does', () => {
	function markOf(status: string): string | undefined {
		render(item(status));
		return host.querySelector('.badge .icon')?.textContent ?? undefined;
	}

	function glyph(name: keyof typeof codepoints): string {
		return String.fromCodePoint(parseInt(codepoints[name], 16));
	}

	it.each([
		['blocked', 'chronic'],
		['skipped', 'skip_next'],
		['duplicate', 'toll']
	] as const)('%s draws %s', (status, name) => {
		expect(markOf(status)).toBe(glyph(name));
	});

	it('leaves the rest to Badge rather than restating them', () => {
		// `canceled` here must be the SAME mark the jobs list draws for it. A second opinion about a
		// state both screens hold is the drift the shared component exists to prevent.
		expect(markOf('paused')).toBe(glyph('pause'));
		expect(markOf('canceled')).toBe(glyph('stop_circle'));
		expect(markOf('failed')).toBe(glyph('cancel'));
	});
});

describe('an album that landed without some of its files', () => {
	/* One dead file does not fail the album, and the row, not only the log, says one was left
	   out. */
	it('says how many landed of how many, and how many were left out', () => {
		render(item('done', { asset_id: 'a1', files_offered: 219, files_left_out: 1 }));
		expect(words(host.querySelector('.left-out'))).toBe('218 of 219 files, 1 left out');
	});

	it('says nothing of the kind for a row that lost none, or one still going', () => {
		render(item('done', { asset_id: 'a1', files_offered: null, files_left_out: null }));
		expect(host.querySelector('.left-out')).toBeNull();
		render(item('running', { files_offered: 219, files_left_out: 1 }));
		expect(host.querySelector('.left-out')).toBeNull();
	});
});

describe('a read after the file landed that its tunnel refused', () => {
	// Each mount numbers its ids afresh; everything else is compared as drawn.
	const drawn = () => host.innerHTML.replace(/bits-\w+/g, 'bits-n');
	const said =
		"Sift didn't read who posted it. The tunnel Sweden is turned off, so nothing was sent. Turn it on, or route this directly.";

	it('is said in one quiet line under a row that stays done', () => {
		render(item('done', { asset_id: 'a1', reads_refused: said }));
		expect(words(host.querySelector('.refused'))).toBe(said);
		expect(host.querySelector('.why')).toBeNull();
	});

	it('leaves a row whose reads all went through exactly as it was', () => {
		const earlier = Object.entries(item('done', { asset_id: 'a1' }));
		render(Object.fromEntries(earlier.filter(([key]) => key !== 'reads_refused')) as DownloadItem);
		const before = drawn();
		render(item('done', { asset_id: 'a1', reads_refused: null }));
		expect(drawn()).toBe(before);
		expect(host.querySelector('.refused')).toBeNull();
		render(item('running', { reads_refused: said }));
		expect(host.querySelector('.refused')).toBeNull();
	});
});

describe('the name comes first, and it is cut in the middle', () => {
	it('leads with the filename rather than with the Site', () => {
		render(item('done', { site: 'example.test', asset_id: 'a1' }));
		expect(words(host.querySelector('.named'))).toBe('clip.mp4');
	});

	it('keeps the extension on a name too long for the row', () => {
		render(item('running', { filename: `${'a'.repeat(90)}.mp4` }));
		const drawn = words(host.querySelector('.named'));
		// The cut is in the MIDDLE, so the end (which says what kind of file it is) survives.
		// `text-overflow: ellipsis` takes exactly that end, which is why it is not the answer here.
		expect(drawn.endsWith('.mp4')).toBe(true);
		expect(drawn).toContain('\u2026');
		expect(drawn.length).toBeLessThan(50);
	});

	it('counts the files when a gallery has not produced one name yet', () => {
		render(
			item('running', {
				filename: null,
				progress: progress({ done_files: 3, total_files: 12 })
			})
		);
		expect(words(host.querySelector('.named'))).toBe('12 files');
	});

	it('falls back to the address when there is neither', () => {
		render(item('queued', { filename: null }));
		expect(words(host.querySelector('.named'))).toBe('example.test/clip');
	});
});

describe('every state draws the verbs that state has', () => {
	it.each([
		/* The same two places on every live row: hold-or-let-go, then stop. Move to the front is in
		   the menu: three candidates, two places, and it is the rarest of the three. */
		['queued', {}, null, ['Pause', 'Cancel']],
		['running', {}, null, ['Pause', 'Cancel']],
		// Resume is the held row's fix, in words; the glyph pair is what is left of the live pair.
		['paused', {}, 'Resume', ['Cancel', 'Copy link']],
		['blocked', { site: 'example.test' }, 'Add cookies', ['Cancel', 'Copy link']],
		['failed', {}, 'Try again', ['Copy link']],
		['canceled', {}, 'Try again', ['Copy link']],
		['skipped', {}, 'Download it anyway', ['Copy link']],
		// No fix at all: the NAME opens the file, and an Open button beside it would say it twice.
		['duplicate', { asset_id: 'a1' }, null, ['Copy link']],
		['done', { asset_id: 'a1' }, null, ['Copy link']],
		['quarantined', {}, 'Open quarantine', ['Copy link']]
	] as const)('%s', (status, known, fixWord, onRow) => {
		render(item(status, known));
		expect(fix()).toBe(fixWord);
		expect(glyphs()).toEqual([...onRow]);
	});

	it.each([
		['blocked', { site: 'example.test' }, ['Add cookies', 'Cancel', 'Copy link']],
		// Pause is not here: a blocked row is already stopped, waiting on a person rather than on
		// the machine, so holding it would change nothing and hide what it is waiting for.
		['queued', {}, ['Move to the front', 'Pause', 'Cancel', 'Copy link']],
		['running', {}, ['Pause', 'Cancel', 'Copy link']],
		['paused', {}, ['Resume', 'Cancel', 'Copy link']],
		['failed', {}, ['Try again', 'Copy link', 'Remove from the list']],
		[
			'duplicate',
			{ asset_id: 'a1' },
			['Open', 'Download it anyway', 'Copy link', 'Remove from the list']
		],
		['skipped', {}, ['Download it anyway', 'Copy link', 'Remove from the list']],
		['done', { asset_id: 'a1' }, ['Open', 'Copy link', 'Remove from the list']]
	] as const)('%s offers the same list behind the three dots', async (status, known, expected) => {
		render(item(status, known));
		// The same rows, whatever order the menu's groups put them in.
		expect([...(await menu())].sort()).toEqual([...expected].sort());
	});

	it('opens a finished download by its NAME, over the list', () => {
		render(item('done', { asset_id: 'a1', filename: 'clip.mp4' }));
		const name = host.querySelector('.named a');
		expect(name?.getAttribute('href')).toBe('/asset/a1');
		expect(words(name)).toBe('clip.mp4');
	});

	it('draws no Open button beside the name of a finished download', () => {
		for (const status of ['done', 'duplicate']) {
			render(item(status, { asset_id: 'a1', filename: 'clip.mp4' }));
			expect(fix(), status).toBeNull();
		}
	});

	it('leaves the name as text where there is no file to open', () => {
		render(item('failed'));
		expect(host.querySelector('.named a')).toBeNull();
	});

	it('draws the badge exactly as the gallery does, ground and all', () => {
		render(item('done', { asset_id: 'a1' }));
		const badge = host.querySelector('.status .badge');
		expect(badge).not.toBeNull();
		expect(badge?.classList.contains('plain')).toBe(false);
	});

	it('marks every row with its Site, from the pack by host, before anything has landed', () => {
		render(item('running', { url: 'https://www.example.test/watch/1', site: null }));
		const mark = host.querySelector('.mark img');
		expect(mark?.getAttribute('src')).toBe('/api/sites/icons/for?host=www.example.test');
	});

	it('asks for a mark the pack does not have once, and a row drawn after it does not ask', async () => {
		/* The picture is loaded by `new Image()`, which here answers as the pack's 404 does. */
		class Refused {
			onload: (() => void) | null = null;
			onerror: (() => void) | null = null;
			set src(_address: string) {
				setTimeout(() => this.onerror?.(), 0);
			}
		}
		vi.stubGlobal('Image', Refused);
		try {
			const url = 'https://nomark.example.test/watch/1';
			render(item('running', { url, site: null }));
			expect(host.querySelector('.mark img')).not.toBeNull();
			await new Promise((done) => setTimeout(done, 5));
			flushSync();
			render(item('running', { url, site: null }));
			expect(host.querySelector('.mark img')).toBeNull();
		} finally {
			vi.unstubAllGlobals();
		}
	});

	/* The Site's logo stands on nothing: no ground and no rounded tile behind it, the way a person's
	   links draw theirs. The letter keeps its tile: that is `Avatar`'s, and held there. */
	it("draws the Site's mark bare, with no plate behind it", () => {
		render(item('running', { url: 'https://www.example.test/watch/1', site: null }));
		expect(host.querySelector('.mark .avatar')?.classList.contains('bare')).toBe(true);
	});

	/* The mark's square is its column's width, so the two cannot disagree. */
	it("draws the Site's mark as a square as wide as its column", () => {
		render(item('running', { url: 'https://www.example.test/watch/1', site: null }));
		const mark = host.querySelector('.mark') as HTMLElement;
		applyStyles(rowSource, mark);
		const style = getComputedStyle(mark);
		expect(style.inlineSize).toBe('100%');
		expect(style.aspectRatio).toMatch(/^1( \/ 1)?$/);
		removeStyles();
	});

	it('never offers to take a running download off the list', async () => {
		// It would still be running, with nothing on screen saying so.
		render(item('running'));
		expect(await menu()).not.toContain('Remove from the list');
	});
});

describe('the fix on a narrow row', () => {
	/* Under the name, the fix follows the facts it answers. At the far end of the line it would
	   stand in the room kept for the actions, which are hidden until the pointer comes, and read as
	   loose. */
	it('stands after the facts on their line, not pushed to the far end', () => {
		render(item('failed'), { narrow: true });
		const fixCell = host.querySelector('.facts .fix') as HTMLElement;
		expect(fixCell).not.toBeNull();
		expect(fixCell.querySelector('button')).not.toBeNull();
		applyStyles(rowSource, fixCell);
		const style = getComputedStyle(fixCell);
		expect(style.marginInlineStart).not.toBe('auto');
		expect(style.justifyContent).toBe('flex-start');
		removeStyles();
	});
});

describe('the buttons and the menu cannot come apart', () => {
	/* The property the whole shape rests on. Both surfaces read one declaration, so anything on the
	   row has to be in the menu. A row that wrote its own buttons would pass any assertion about
	   a button existing, and this is the assertion it would fail. */
	it.each([
		'queued',
		'running',
		'paused',
		'blocked',
		'failed',
		'done',
		'skipped',
		'duplicate',
		'canceled'
	])('%s: everything on the row is behind the dots too', async (status) => {
		render(item(status, { site: 'example.test', asset_id: 'a1' }));
		const rows = await menu();
		for (const name of [...glyphs(), fix()].filter(Boolean)) {
			expect(rows, `${name} is on the row and not in the menu`).toContain(name);
		}
	});
});

describe('a download whose file has since been deleted', () => {
	/* The row stays (it is history, and history of a file that is gone is still history), but
	   nothing on it may pretend to lead anywhere. */
	const deleted = () =>
		item('done', {
			filename: null,
			asset_id: null,
			remembered_filename: 'clip.mp4',
			site: 'example.test',
			site_id: 's1',
			username: 'someone',
			person_id: 'p1'
		});

	it('keeps the name and strikes it through', () => {
		render(deleted());
		const name = host.querySelector('.named');
		expect(words(name)).toBe('clip.mp4');
		expect(name?.className).toContain('gone');
	});

	it('offers nothing that opens it, on either surface', async () => {
		render(deleted());
		expect(fix()).not.toBe('Open');
		expect(await menu()).not.toContain('Open');
	});

	it('draws the Site and the username as plain text rather than as links', () => {
		render(deleted());
		expect(host.querySelector('a.where')).toBeNull();
		expect(host.querySelector('a.whose')).toBeNull();
	});
});

describe('cancelling asks only where something is lost', () => {
	/* A waiting download has fetched nothing, so stopping it costs a click to start again and the
	   page says so with a toast that undoes. A running one has bytes on disk that go with it, and
	   there is no resume, so the next attempt starts from nothing. */
	function pressCancel() {
		const button = [...host.querySelectorAll('button[aria-label]')].find(
			(one) => one.getAttribute('aria-label') === 'Cancel'
		) as HTMLButtonElement;
		button.click();
		flushSync();
	}

	it('asks on a running row, and does not cancel until the question is answered', async () => {
		HANDLERS.oncancel.mockClear();
		render(
			item('running', {
				progress: progress({ done_bytes: 148_000_000 })
			})
		);
		pressCancel();
		await vi.waitFor(() => {
			expect(document.body.textContent).toContain('Cancel this download?');
		});
		expect(document.body.textContent).toContain(
			'148.0 MB of clip.mp4 is downloaded. Canceling drops it; Try again starts from nothing.'
		);
		expect(HANDLERS.oncancel).not.toHaveBeenCalled();
	});

	it('asks on a held row too, because a pause is the promise that the bytes are kept', async () => {
		HANDLERS.oncancel.mockClear();
		render(item('paused', { progress: progress({ done_bytes: 148_000_000 }) }));
		pressCancel();
		await vi.waitFor(() => {
			expect(document.body.textContent).toContain('Cancel this download?');
		});
		expect(HANDLERS.oncancel).not.toHaveBeenCalled();
	});

	it('cancels a waiting row immediately, with no question', () => {
		HANDLERS.oncancel.mockClear();
		render(item('queued'));
		pressCancel();
		expect(HANDLERS.oncancel).toHaveBeenCalledWith('one');
		expect(document.body.textContent).not.toContain('Cancel this download?');
	});
});

describe('the tick', () => {
	it('reports the row it belongs to and which way it went', () => {
		HANDLERS.onselect.mockClear();
		render(item('done', { asset_id: 'a1' }));
		const box = host.querySelector('.tick button') as HTMLButtonElement;
		box.click();
		// And the press itself, so the list can read a held Shift and pick a range.
		expect(HANDLERS.onselect).toHaveBeenCalledWith('one', true, expect.any(MouseEvent));
	});

	it('is not drawn at all where the list does not pick', () => {
		/* The TRACK stays (an empty cell, so the columns of a list that does not pick stand where
		   they do in one that does), and the box is not in it. */
		render(item('done', { asset_id: 'a1' }), { onselect: undefined });
		expect(host.querySelector('.tick')?.children.length).toBe(0);
	});
});

describe('a download held where it stands', () => {
	/* The three things a pause has to say, and the reason each one is here: the state, that the
	   bytes survived it, and the way to let it go again. A row that merely stopped moving and said
	   nothing would read as a download that had got stuck. */
	function paused(known: Partial<DownloadItem> = {}) {
		return item('paused', {
			progress: progress({ done_bytes: 148_000_000, total_bytes: 240_000_000 }),
			...known
		});
	}

	it('keeps the bar where it is, greyed, rather than emptying it or sweeping', () => {
		render(paused());
		const fill = host.querySelector('.fill');
		expect(host.querySelector('.track')?.className).toContain('paused');
		// Not the sweep: an animation on a held row says the opposite of what the word says.
		expect(fill?.className).not.toContain('indeterminate');
		expect((fill as HTMLElement)?.style.inlineSize).toBe('61.66666666666667%');
	});

	it('draws no bar at all where nothing says how big the file is', () => {
		// An empty grey track would be a claim that nought was fetched, rather than that the size
		// is not known.
		render(paused({ progress: null }));
		expect(host.querySelector('.track')).toBeNull();
	});

	it('says how much is KEPT, which is the question a pause raises', () => {
		render(paused());
		expect(words(host.querySelector('.size'))).toBe('148.0 MB of 240.0 MB kept');
	});

	it('says what is kept after a restart, when only the disk knows and the total is gone', () => {
		// The server measures the job's workspace for a paused row the progress registry lost with
		// the process, and has only the bytes: no total, so no bar, and the figure alone.
		render(paused({ progress: progress({ done_bytes: 148_000_000 }) }));
		expect(words(host.querySelector('.size'))).toBe('148.0 MB kept');
		expect(host.querySelector('.track')).toBeNull();
	});

	it('falls back to when it was added where there are no bytes to report', () => {
		render(paused({ progress: null }));
		expect(words(host.querySelector('.second'))).toBe('10 minutes ago');
	});
});

describe('where a waiting row is in the queue', () => {
	/* Nothing without a position from the server: "next in line" on every one of forty rows
	   would be false for thirty-nine of them. */
	/* One plain name rather than a table of three, because the sweep that guards this rule finds a
	   test by the words in its name and a `%i` in a title is not a name anything can look up. */
	it('counts the rows in front of it, and names the first one', () => {
		const at = (position: number) => {
			render(item('queued', { position }));
			return words(host.querySelector('.second'));
		};
		expect(at(1)).toBe('next in line');
		expect(at(2)).toBe('1 ahead');
		expect(at(8)).toBe('7 ahead');
	});

	it('says nothing where the server has not counted it', () => {
		render(item('queued'));
		expect(host.querySelector('.second')).toBeNull();
	});
});

describe('P holds it, and lets it go again', () => {
	/* From the ROW, which is the tab stop Tab lands on first: a key that only worked on the span
	   holding the tick would pass a test pressed there while doing nothing on the row itself.
	   `div.row` is `DataRow`'s own element. */
	function press(key: string, held: KeyboardEventInit = {}) {
		const row = host.querySelector('div.row') as HTMLElement;
		row.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, ...held }));
		flushSync();
	}

	it('pauses a running row', () => {
		HANDLERS.onpause.mockClear();
		render(item('running'));
		press('p');
		expect(HANDLERS.onpause).toHaveBeenCalledWith('one');
	});

	it('resumes a held one, from the same key', () => {
		HANDLERS.onresume.mockClear();
		HANDLERS.onpause.mockClear();
		render(item('paused'));
		press('P');
		expect(HANDLERS.onresume).toHaveBeenCalledWith('one');
		expect(HANDLERS.onpause).not.toHaveBeenCalled();
	});

	it('does nothing at all on a row that can do neither', () => {
		// A press that quietly did the wrong thing would be worse than a press that does nothing.
		HANDLERS.onpause.mockClear();
		HANDLERS.onresume.mockClear();
		render(item('done', { asset_id: 'a1' }));
		press('p');
		expect(HANDLERS.onpause).not.toHaveBeenCalled();
		expect(HANDLERS.onresume).not.toHaveBeenCalled();
	});

	it('leaves Ctrl+P to the browser, which means print by it', () => {
		HANDLERS.onpause.mockClear();
		render(item('running'));
		press('p', { ctrlKey: true });
		expect(HANDLERS.onpause).not.toHaveBeenCalled();
	});

	it('is heard from the tick as well, the other place the keyboard sits in a row', () => {
		HANDLERS.onpause.mockClear();
		render(item('running'));
		const tick = host.querySelector('span.tick button') as HTMLElement;
		tick.dispatchEvent(new KeyboardEvent('keydown', { key: 'p', bubbles: true }));
		flushSync();
		expect(HANDLERS.onpause).toHaveBeenCalledWith('one');
	});
});

describe('Delete, from the row itself', () => {
	function press(key: string) {
		const row = host.querySelector('div.row') as HTMLElement;
		row.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
		flushSync();
	}

	it('stops a row that is still waiting', () => {
		HANDLERS.oncancel.mockClear();
		render(item('queued'));
		press('Delete');
		expect(HANDLERS.oncancel).toHaveBeenCalledWith('one');
	});

	it('takes a finished row off the list', () => {
		HANDLERS.onremove.mockClear();
		render(item('done', { asset_id: 'a1' }));
		press('Delete');
		expect(HANDLERS.onremove).toHaveBeenCalledWith('one');
	});

	it('does not open the row, which is what Enter is for', () => {
		HANDLERS.onexpand.mockClear();
		render(item('done', { asset_id: 'a1' }));
		press('Delete');
		expect(HANDLERS.onexpand).not.toHaveBeenCalled();
	});
});

describe('the bar', () => {
	it('sweeps rather than sitting at zero while nothing has moved yet', () => {
		// The silence before the first byte: the page is being fetched, a challenge answered, the
		// formats listed. A bar at zero would claim the transfer had started and stalled.
		render(item('running', { progress: null }));
		expect(host.querySelector('.fill')?.className).toContain('indeterminate');
	});

	it('is gone the moment the download has stopped', () => {
		render(item('done', { asset_id: 'a1', size_bytes: 38_000_000 }));
		expect(host.querySelector('.track')).toBeNull();
	});
});

describe('the line under the badge gives its moment on hover, like every other moment', () => {
	/** The bubble over the line under the badge, or null where that line wears none. */
	async function hoverUnder(): Promise<string | null> {
		const line = host.querySelector<HTMLElement>('.when .second');
		const wrap = line?.closest<HTMLElement>('.wrap');
		if (!wrap) return null;
		wrap.dispatchEvent(new PointerEvent('pointermove', { bubbles: true }));
		await vi.waitFor(() => {
			flushSync();
			if (!document.querySelector('[role="tooltip"]')) throw new Error('no label yet');
		});
		return document.querySelector('[role="tooltip"]')?.textContent?.trim() ?? null;
	}

	it('says the exact moment a finished download landed', async () => {
		render(item('done', { created_at: 100, finished_at: 400, size_bytes: 1_000_000 }));
		expect(await hoverUnder()).toBe(exactly(400));
	});

	it('says when a failed one stopped, falling back to when it was asked for', async () => {
		render(item('failed', { created_at: 100 }));
		expect(await hoverUnder()).toBe(exactly(100));
	});

	it('wears none where the line says a speed or a place rather than a moment', async () => {
		render(item('running', { progress: progress({ bytes_per_second: 2048 }) }));
		expect(await hoverUnder()).toBeNull();
		render(item('queued', { position: 2 }));
		expect(await hoverUnder()).toBeNull();
	});
});

describe("a row at a phone's width", () => {
	it('leaves its glyphs to the three dots, which list the same verbs, so the name has the room', () => {
		render(item('failed'));
		expect(glyphs()).toEqual(['Copy link']);
		render(item('failed'), { phone: true });
		expect(glyphs()).toEqual([]);
		expect(fix()).toBe('Try again');
	});
});

/* The detail under an opened row is one cell of the row's subgrid. Unplaced it would stand in the
   pick track, twenty pixels wide, and a link would break at every letter down the page. It starts
   on the name's line and runs to the row's end, in every arrangement. */
describe('the detail under an opened row', () => {
	it.each([false, true])('runs from the name to the end of the row (narrow %s)', (narrow) => {
		render(item('failed', { url: 'https://cdn.example.test/attachments/1/2/clip.mp4' }), {
			expanded: true,
			narrow
		});
		const opened = host.querySelector('.opened') as HTMLElement;
		expect(opened).not.toBeNull();
		const name = downloadColumns(narrow).findIndex((column) => column.id === 'name') + 1;
		expect(opened.style.gridColumn).toBe(`${name} / -1`);
		expect(opened.querySelector('.detail')).not.toBeNull();
	});
});

describe('a failed row, opened', () => {
	const SAID = 'The Site stopped answering partway through.';

	function sentences(): number {
		return (host.textContent ?? '').split(SAID).length - 1;
	}

	function tryAgains(): number {
		return [...host.querySelectorAll('button')].filter((one) => words(one) === 'Try again').length;
	}

	it('says the failure and offers Try again once, in the detail', () => {
		render(item('failed', { sentence: SAID, site: 'Harbor' }), { expanded: true });
		expect(sentences()).toBe(1);
		expect(tryAgains()).toBe(1);
		expect(host.querySelector('.opened')?.textContent).toContain(SAID);
		expect(host.querySelector('.fix button')).toBeNull();
		// The row keeps its facts in place of the sentence.
		expect(host.querySelector('.under .where')?.textContent).toContain('Harbor');
	});

	it('says both on the row while it is shut', () => {
		render(item('failed', { sentence: SAID }), { expanded: false });
		expect(sentences()).toBe(1);
		expect(fix()).toBe('Try again');
	});
});
