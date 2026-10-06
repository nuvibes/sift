import { flushSync } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/* The signal that reaches a grid the Library screen cannot. Each screen builds its own Library, so
 * there is no instance for the grid to watch; removing a folder in Settings has to reach a grid
 * mounted behind it, and it does it by bumping one shared number. What is worth pinning down is that
 * the folder-shape changes actually bump it: a removal enqueues no job, so nothing else would. */

const del = vi.fn();
const post = vi.fn();
const get = vi.fn();
vi.mock('$lib/api/client', () => ({
	api: {
		del: (...args: unknown[]) => del(...args),
		post: (...args: unknown[]) => post(...args),
		get: (...args: unknown[]) => get(...args)
	},
	request: vi.fn(),
	ApiError: class ApiError extends Error {}
}));
vi.mock('$lib/shell/toasts.svelte', () => ({ toasts: { show: vi.fn() } }));

const {
	libraryChanges,
	recorded,
	rereadOnHistoryChange,
	reloadOnLibraryChange,
	HeldNewcomers,
	HISTORY_SETTLE_MS
} = await import('./changes.svelte');
const { imports } = await import('./imports.svelte');
const { Library } = await import('./library.svelte');

function root(id = 'r1') {
	return {
		id,
		name: 'Videos',
		path: '/media/videos',
		kind: 'local' as const,
		vault: false,
		created_at: 0,
		reachable: true,
		presence: 'here' as const,
		shared: false,
		restricted: false,
		shared_here: false,
		restricted_here: false,
		// Which disk it is on. Null is what the server answers where the site will not say, and
		// what this test is about is a removal signal rather than a badge, so null is the honest
		// stand-in rather than a made-up drive letter.
		machine: null
	};
}

beforeEach(() => {
	del.mockReset().mockResolvedValue(undefined);
	post.mockReset().mockResolvedValue(undefined);
	// load() asks for the folders then the roots; one object with both keys answers both calls.
	get.mockReset().mockResolvedValue({ folders: [], roots: [] });
	libraryChanges.generation = 0;
});

describe('the shared library-change signal', () => {
	it('bumps its number when told the shape changed', () => {
		const before = libraryChanges.generation;
		libraryChanges.changed();
		expect(libraryChanges.generation).toBe(before + 1);
	});

	it('is bumped when a folder is removed, so the grid re-reads', async () => {
		// A removed folder's tiles must leave the grid without a reload: removal enqueues no job,
		// so the grid has to be watching the shared signal rather than the Library instance.
		const library = new Library();
		const before = libraryChanges.generation;

		await library.removeRoot(root());

		expect(del).toHaveBeenCalledWith('/library/roots/r1');
		expect(libraryChanges.generation).toBe(before + 1);
	});
});

describe('a history thread re-reading itself', () => {
	/* Set up the way a history does it: inside a component's setup, which `$effect.root` stands in
	   for. Returns the teardown a screen going away would run. */
	function watching(reread: () => void): () => void {
		const stop = $effect.root(() => {
			rereadOnHistoryChange(reread);
		});
		flushSync();
		return stop;
	}

	beforeEach(() => {
		vi.useFakeTimers();
	});

	afterEach(() => {
		vi.useRealTimers();
	});

	it('asks once for a burst of bells, after the settle window and not before', () => {
		// One press rings both (this tab's own `recorded`, then the server's announcement a beat
		// later), and a chunked bulk write rings once per chunk. Each of those is one read, not N.
		const reread = vi.fn();
		const stop = watching(reread);

		recorded.changed();
		flushSync();
		libraryChanges.changed();
		flushSync();
		recorded.changed();
		flushSync();

		vi.advanceTimersByTime(HISTORY_SETTLE_MS - 1);
		expect(reread, 'asked before the window closed').not.toHaveBeenCalled();
		vi.advanceTimersByTime(1);
		expect(reread).toHaveBeenCalledTimes(1);
		stop();
	});

	it('asks for nothing on the first run, because the screen is loading anyway', () => {
		const reread = vi.fn();
		const stop = watching(reread);

		vi.advanceTimersByTime(HISTORY_SETTLE_MS * 4);
		expect(reread).not.toHaveBeenCalled();
		stop();
	});

	it('does not ask for itself after the thread has left the screen', () => {
		const reread = vi.fn();
		const stop = watching(reread);

		recorded.changed();
		flushSync();
		stop();

		vi.advanceTimersByTime(HISTORY_SETTLE_MS * 4);
		expect(reread).not.toHaveBeenCalled();
	});
});

describe('a list re-reading when the files on their way in have landed', () => {
	function watching(reload: () => void): () => void {
		const stop = $effect.root(() => {
			reloadOnLibraryChange(reload);
		});
		flushSync();
		return stop;
	}

	it('asks once when the work ends, and never while files are still arriving', () => {
		const reload = vi.fn();
		const stop = watching(reload);

		imports.busy = 3;
		imports.settled += 1;
		flushSync();
		expect(reload, 'asked while files were still arriving').not.toHaveBeenCalled();

		imports.busy = 0;
		imports.settled += 1;
		flushSync();
		expect(reload).toHaveBeenCalledTimes(1);
		stop();
	});

	it('asks nothing on the first run', () => {
		const reload = vi.fn();
		const stop = watching(reload);
		flushSync();
		expect(reload).not.toHaveBeenCalled();
		stop();
	});
});

describe('a newcomer to a list somebody is reading', () => {
	/** A list drawn in a box that scrolls, scrolled `down` pixels, rows a hundred high. */
	function list(down: number) {
		const box = document.createElement('div');
		box.style.overflowY = 'auto';
		Object.defineProperty(box, 'scrollHeight', { value: 1000 });
		Object.defineProperty(box, 'clientHeight', { value: 100 });
		box.scrollTop = down;
		const wall = document.createElement('div');
		const row = document.createElement('div');
		Object.defineProperty(row, 'offsetHeight', { value: 100 });
		wall.append(row);
		box.append(wall);
		document.body.append(box);
		const held = new HeldNewcomers<{ id: string }>();
		const stop = held.watch(wall);
		held.take([{ id: 'b' }, { id: 'c' }], false);
		return { box, held, stop };
	}

	it('comes straight in while the top of the list is on screen', () => {
		const { box, held, stop } = list(0);
		held.take([{ id: 'a' }, { id: 'b' }, { id: 'c' }], true);
		expect(held.shown.map((row) => row.id)).toEqual(['a', 'b', 'c']);
		expect(held.waiting).toBe(0);
		stop();
		box.remove();
	});

	it('is held with a count while somebody reads further down, and comes in at the top', () => {
		const { box, held, stop } = list(500);
		held.take([{ id: 'a' }, { id: 'b' }, { id: 'c' }], true);
		expect(
			held.shown.map((row) => row.id),
			'the rows being read moved'
		).toEqual(['b', 'c']);
		expect(held.waiting).toBe(1);

		box.scrollTop = 0;
		box.dispatchEvent(new Event('scroll'));
		expect(held.shown.map((row) => row.id)).toEqual(['a', 'b', 'c']);
		expect(held.waiting).toBe(0);
		stop();
		box.remove();
	});

	it('draws a new question as it is, wherever the list was scrolled', () => {
		const { box, held, stop } = list(500);
		held.take([{ id: 'x' }, { id: 'y' }], false);
		expect(held.shown.map((row) => row.id)).toEqual(['x', 'y']);
		stop();
		box.remove();
	});
});
