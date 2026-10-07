import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError } from '$lib/api/client';
import { signOut } from '$lib/shell/sign-out';
import { Imports, NOTHING_TO_GLANCE_AT, SETTLE_MS, imports } from './imports.svelte';
import type { components } from '$lib/api/schema';

vi.mock('$app/navigation', () => ({ goto: vi.fn() }));

/* What a screen is told, given what the queue answered with.
 *
 * The fetching is not the interesting part. What is worth pinning down is the reduction: which work
 * counts as a file arriving, when a grid is told to re-ask, and the one question whose obvious
 * answer is wrong.
 */

type Row = Pick<
	components['schemas']['JobView'],
	'id' | 'parent_id' | 'type' | 'state' | 'run_after' | 'subject_id'
>;

function job(
	id: string,
	type: string,
	state: Row['state'],
	parent: string | null = null,
	subject: string | null = null
): Row {
	/* `run_after` is sent on every job (null on one that may be taken now), so a row without it
	   is a row the queue could not have answered with. */
	return { id, parent_id: parent, type, state, run_after: null, subject_id: subject };
}

function push(...jobs: Row[]) {
	imports.apply({ jobs });
}

beforeEach(() => {
	// A module singleton, so each test starts it from nothing. Two pushes of an empty queue: the
	// first clears what is in flight, the second settles the counter it may have moved doing so.
	push();
	push();
	imports.settled = 0;
	// The Downloads row's facts are their own read, which a push does not touch. Reset them here.
	imports.glance(NOTHING_TO_GLANCE_AT);
});

describe('how busy Sift looks', () => {
	it('counts the work that ends in something new to look at', () => {
		push(
			job('a', 'import', 'running'),
			job('b', 'thumbnail', 'queued'),
			job('c', 'download', 'queued')
		);

		expect(imports.busy).toBe(3);
	});

	it('does not count a scan, which is Sift reading a folder rather than a file arriving', () => {
		/* A scan of a large library runs for minutes and produces nothing to draw until it finds
		 * something. Counted, it would leave "importing files" on the screen for the whole of it
		 * while the grid stayed exactly as it was. */
		push(job('a', 'scan', 'running'), job('b', 'fts_reindex', 'queued'));

		expect(imports.busy).toBe(0);
	});

	it('does not count work that has already finished or failed', () => {
		push(job('a', 'import', 'done'), job('b', 'thumbnail', 'failed'));

		expect(imports.busy).toBe(0);
	});
});

describe('the files arriving, which is what "Importing N files" says', () => {
	it('counts a file once, however many of its steps are in flight', () => {
		push(
			job('scan', 'scan', 'running'),
			job('p1', 'probe', 'running', 'scan', 'file-1'),
			job('t1', 'thumbnail', 'queued', 'p1', 'file-1'),
			job('v1', 'preview', 'queued', 'p1', 'file-1'),
			job('p2', 'probe', 'queued', 'scan', 'file-2'),
			job('drop', 'import', 'running')
		);

		expect(imports.arriving).toBe(3);
	});

	it('does not count a picture made again for a file already here', () => {
		/* A still cut again, "Rebuild thumbnails", a new hover clip shape: rows of the same types as
		 * a file arriving, asked for on their own rather than as a step of one. */
		push(
			job('t1', 'thumbnail', 'queued', null, 'file-1'),
			job('v1', 'preview', 'running', null, 'file-1'),
			job('s2', 'sprite', 'queued', null, 'file-2'),
			job('p3', 'probe', 'running', null, 'file-3')
		);

		expect(imports.arriving).toBe(0);
		expect(imports.busy, 'the tiles still know a picture is coming').toBe(4);
	});

	it('does not count a step that has finished', () => {
		push(job('t1', 'thumbnail', 'done', 'p1', 'file-1'), job('d', 'download', 'failed'));

		expect(imports.arriving).toBe(0);
	});
});

describe('downloads, counted apart for their own indicator', () => {
	it('turns for what the rows say is fetching or will start, and nothing else', () => {
		imports.glance({ ...NOTHING_TO_GLANCE_AT, downloading: 2 });
		expect(imports.downloading).toBe(2);
	});

	/* The queue's page is the newest fifty jobs of every kind, so a download can scroll off it behind
	   a few seconds of background work while it still waits. What turns the glyph is read off the
	   download rows, and a page of jobs moves nothing. */
	it('is not moved by a page of the work queue', () => {
		imports.glance({ ...NOTHING_TO_GLANCE_AT, downloading: 1 });
		push(job('a', 'download', 'running'), job('b', 'download', 'queued'));
		expect(imports.downloading).toBe(1);
		push();
		expect(imports.downloading, 'nor by a page with no download on it').toBe(1);
	});

	it('still counts a download as arriving work for the busy signal', () => {
		push(job('a', 'download', 'running'), job('c', 'import', 'running'));
		expect(imports.busy).toBe(2);
	});
});

describe('background work, which is what turns the gear', () => {
	it('counts face work too', () => {
		/* The gear follows all in-flight work, not only ARRIVING work (`busy`): a library-wide
		 * face pass of two hundred jobs must turn the one item that leads to the queue running
		 * them.
		 */
		push(
			job('a', 'face_scan', 'running'),
			job('b', 'face_scan', 'queued'),
			job('c', 'face_sweep', 'running')
		);

		expect(imports.working).toBe(3);
		expect(imports.busy, 'face work is still not "importing files"').toBe(0);
	});

	it('counts any kind of work, so a new one needs nothing added here', () => {
		// Derived from what is in flight rather than from a list of types. A list would have to be
		// added to for every new kind of background work, nothing would fail when it was not, and
		// the gear would quietly stop turning.
		push(job('a', 'something_nobody_has_written_yet', 'running'));

		expect(imports.working).toBe(1);
	});

	it('leaves out a download, which has an indicator of its own', () => {
		push(job('a', 'download', 'running'), job('b', 'face_scan', 'running'));

		expect(imports.working).toBe(1);
	});

	it('does not count work that has finished', () => {
		push(job('a', 'face_scan', 'done'), job('b', 'face_scan', 'failed'));

		expect(imports.working).toBe(0);
	});
});

describe('how many Sites are asking for cookies', () => {
	/* The rail's Downloads item wears this, in the warning colour, beside the spinner, and it is
	   the same number the Downloads screen's Edit cookies row wears. The half counted here is read with
	   the rest of the row's facts: a download whose Site needs cookies it cannot open is parked
	   `blocked` by the kernel, the fact the screen draws as "Waiting for cookies". The other half,
	   Sites whose saved cookies have run out, is a read of its own (see `readCookies`). */
	it('counts a download stopped waiting for cookies', () => {
		imports.glance({ ...NOTHING_TO_GLANCE_AT, waiting_for_cookies: 1 });
		expect(imports.waitingForCookies).toBe(1);
		expect(imports.downloading, 'a blocked download is not fetching').toBe(0);
	});

	it('adds the Sites whose saved cookies have run out', () => {
		imports.glance({ ...NOTHING_TO_GLANCE_AT, waiting_for_cookies: 1 });
		imports.expiredCookies = 2;
		expect(imports.cookiesWanted).toBe(3);
		imports.expiredCookies = 0;
	});
});

describe('the Downloads status light', () => {
	it('is nothing until a download has ended unseen', () => {
		imports.glance({ ...NOTHING_TO_GLANCE_AT, downloading: 1 });
		expect(imports.downloadStatus, 'a running download lights no dot').toBe('none');
	});

	it('goes green for one that landed and red for one that failed, and red outranks green', () => {
		imports.glance({ ...NOTHING_TO_GLANCE_AT, landed_unseen: 1 });
		expect(imports.downloadStatus).toBe('success');
		imports.glance({ ...NOTHING_TO_GLANCE_AT, landed_unseen: 1, failed_unseen: 1 });
		expect(imports.downloadStatus).toBe('error');
	});

	it('is not lit or put out by a page of the work queue', () => {
		imports.glance({ ...NOTHING_TO_GLANCE_AT, failed_unseen: 1 });
		push(job('a', 'download', 'done'));
		push();
		expect(imports.downloadStatus).toBe('error');
	});

	/* Marked on the rows, so the dot is out in every window and a reload does not bring it back:
	   a memory held in one window would come back on every restart, or never light at all. */
	it('goes out immediately when seen, and the rows are told', async () => {
		const post = vi.spyOn(api, 'post').mockResolvedValue(undefined);
		imports.glance({ ...NOTHING_TO_GLANCE_AT, landed_unseen: 2, failed_unseen: 1 });

		imports.clearDownloadStatus();

		expect(imports.downloadStatus).toBe('none');
		expect(post).toHaveBeenCalledWith('/downloads/seen', {});
		post.mockRestore();
	});
});

describe('telling a screen to re-ask', () => {
	it('says so when work finishes, because that is when there is something new to show', () => {
		push(job('a', 'import', 'running'), job('b', 'thumbnail', 'queued'));
		expect(imports.settled).toBe(0);

		push(job('a', 'import', 'done'), job('b', 'thumbnail', 'done'));

		expect(imports.settled).toBe(1);
	});

	it('stays quiet while work is only being taken on', () => {
		/* A file being accepted is not a file that can be drawn. Re-asking here fetches the same page
		 * again, once per file added, and finds nothing every time. */
		push(job('a', 'import', 'running'));
		push(job('a', 'import', 'running'), job('b', 'import', 'queued'));

		expect(imports.settled).toBe(0);
	});

	it('re-asks when a scan finishes, since a re-added folder links files with no arriving work', () => {
		/* Adding a folder Sift already knows links its files back into view but enqueues no
		 * probe, so without this the grid would never hear the catalog had changed and the files
		 * would stay off it until a reload. A scan is not "arriving" work, so its finish is what
		 * has to speak.
		 */
		imports.settled = 0;

		push(job('s', 'scan', 'running'));
		expect(imports.settled, 'a scan merely starting is not a reason to re-ask').toBe(0);

		push(job('s', 'scan', 'done'));
		expect(imports.settled, 'a scan finishing did not re-ask the grid').toBe(1);
	});
});

describe('whether one file is done with', () => {
	it('is false for a job nobody has seen yet, not true', () => {
		/* The important one. This is asked the instant a file is accepted (before the queue's next
		 * push carries it), and "not currently in flight" is indistinguishable from "already
		 * finished" at that moment. Answering the obvious way clears every placeholder the frame
		 * after it appears, for every file, every time. */
		expect(imports.landed('never-seen')).toBe(false);
	});

	it('is false while the job itself is still going', () => {
		push(job('import-1', 'import', 'running'));

		expect(imports.landed('import-1')).toBe(false);
	});

	it('is TRUE once the import is done, even though its thumbnail is not', () => {
		/* The library row exists as soon as the import finishes, so the grid already draws a real
		 * tile for the file, shimmering, because it has no picture yet. Holding the placeholder
		 * until the thumbnail lands would put both on screen at the same time: one file, drawn
		 * twice, in two different placeholder styles, under a header correctly saying one.
		 */
		push(job('import-1', 'import', 'done'), job('thumb-1', 'thumbnail', 'running', 'import-1'));

		expect(imports.landed('import-1')).toBe(true);
	});

	it('is true down a chain too: the descendants are not this job', () => {
		push(
			job('import-1', 'import', 'done'),
			job('probe-1', 'probe', 'done', 'import-1'),
			job('thumb-1', 'thumbnail', 'running', 'probe-1')
		);

		expect(imports.landed('import-1')).toBe(true);
	});

	it('is not confused by other work running at the same time', () => {
		push(
			job('import-1', 'import', 'done'),
			job('import-2', 'import', 'running'),
			job('thumb-2', 'thumbnail', 'queued', 'import-2')
		);

		expect(imports.landed('import-1')).toBe(true);
		expect(imports.landed('import-2')).toBe(false);
	});
});

describe('what counts as Sift working', () => {
	it('does not turn the gear for a job that is not due yet', async () => {
		/* Whole-library work is held back until the batch that asked for it stops arriving, so a
		 * thousand imported files rebuild the grouping once instead of a thousand times. That is
		 * a minute of sitting in the queue doing nothing, and the indicator must not say Sift is
		 * busy for all of it.
		 */
		const later = Math.floor(Date.now() / 1000) + 60;
		const store = new Imports();

		store.apply({
			jobs: [
				{ id: 'a', parent_id: null, type: 'face_regroup', state: 'queued', run_after: later },
				{ id: 'b', parent_id: null, type: 'face_scan', state: 'running', run_after: null }
			]
		});

		expect(store.working).toBe(1);
	});

	it('counts a job whose moment has come', async () => {
		const already = Math.floor(Date.now() / 1000) - 5;
		const store = new Imports();

		store.apply({
			jobs: [
				{ id: 'a', parent_id: null, type: 'face_regroup', state: 'queued', run_after: already }
			]
		});

		expect(store.working).toBe(1);
	});
});

describe('the read after the queue goes quiet', () => {
	const family = (outstanding: number, waiting: number) =>
		({ outstanding, waiting, on: true, ready: true, reason: null }) as never;
	const page = (families: Record<string, never>) => ({ jobs: [], families }) as never;

	it('reads once more, past both caches, when a pass shows work waiting and nothing outstanding', async () => {
		vi.useFakeTimers();
		try {
			const get = vi.spyOn(api, 'get').mockResolvedValue(page({ generate: family(0, 2) }));
			const store = new Imports();
			await store.refresh();
			expect(get).toHaveBeenCalledTimes(1);
			await vi.advanceTimersByTimeAsync(SETTLE_MS);
			expect(
				get,
				'the queue went quiet with a count that could be stale, and nothing re-read'
			).toHaveBeenCalledTimes(2);
			await vi.advanceTimersByTimeAsync(SETTLE_MS * 3);
			expect(get, 'the settling read went on re-reading an idle library').toHaveBeenCalledTimes(2);
		} finally {
			vi.restoreAllMocks();
			vi.useRealTimers();
		}
	});

	/* Sign out with the settling read still due: the login page must not draw a refused GET /jobs. */
	it('asks the queue nothing after signing out, not even the read it had due', async () => {
		vi.useFakeTimers();
		try {
			const get = vi.spyOn(api, 'get').mockResolvedValue(page({ generate: family(0, 2) }));
			vi.spyOn(api, 'post').mockResolvedValue(undefined as never);
			await imports.refresh();
			get.mockClear();
			await signOut();
			await vi.advanceTimersByTimeAsync(SETTLE_MS * 3);
			expect(get.mock.calls.map(([path]) => path)).toEqual([]);
		} finally {
			vi.restoreAllMocks();
			vi.useRealTimers();
		}
	});

	it('drops a refusal that lands after the session ended, so the next account still reads', async () => {
		let answer: (error: unknown) => void = () => undefined;
		const get = vi
			.spyOn(api, 'get')
			.mockImplementationOnce(() => new Promise((_, refuse) => (answer = refuse)));
		try {
			const out = imports.refresh();
			imports.stop();
			answer(new ApiError(401, 'Not signed in'));
			await out;
			get.mockResolvedValue(page({}));
			await imports.refresh();
			expect(get).toHaveBeenCalledTimes(2);
			expect(imports.live).toBe(true);
		} finally {
			vi.restoreAllMocks();
		}
	});

	it('reads nothing more while work is outstanding, or when nothing is waiting', async () => {
		vi.useFakeTimers();
		try {
			const get = vi.spyOn(api, 'get').mockResolvedValue(page({ generate: family(3, 2) }));
			const store = new Imports();
			await store.refresh();
			get.mockResolvedValue(page({ generate: family(0, 0) }));
			await store.refresh();
			await vi.advanceTimersByTimeAsync(SETTLE_MS * 2);
			expect(get, 'a running or finished library was re-read on a timer').toHaveBeenCalledTimes(2);
		} finally {
			vi.restoreAllMocks();
			vi.useRealTimers();
		}
	});
});

describe('a busy queue', () => {
	it('asks one question at a time, and once more for everybody who asked meanwhile', async () => {
		const answers: Array<(page: never) => void> = [];
		const get = vi
			.spyOn(api, 'get')
			.mockImplementation(() => new Promise((resolve) => answers.push(resolve as never)));
		try {
			const store = new Imports();
			const asks = [store.refresh(), store.refresh(), store.refresh()];
			await vi.waitFor(() => expect(answers).toHaveLength(1));
			expect(
				get,
				'the bell rang three times and three reads went out side by side'
			).toHaveBeenCalledTimes(1);

			answers[0]({ jobs: [job('a', 'import', 'running')] } as never);
			await vi.waitFor(() => expect(answers).toHaveLength(2));
			expect(store.busy).toBe(1);
			answers[1]({ jobs: [] } as never);
			await Promise.all(asks);

			expect(get).toHaveBeenCalledTimes(2);
			expect(store.busy).toBe(0);
		} finally {
			vi.restoreAllMocks();
		}
	});

	it('owes nothing to a session that ended while a read was out', async () => {
		let answer: (page: never) => void = () => undefined;
		const get = vi
			.spyOn(api, 'get')
			.mockImplementationOnce(() => new Promise((resolve) => (answer = resolve as never)));
		try {
			const store = new Imports();
			const out = store.refresh();
			void store.refresh();
			store.stop();
			answer({ jobs: [] } as never);
			await out;
			expect(get).toHaveBeenCalledTimes(1);
		} finally {
			vi.restoreAllMocks();
		}
	});
});
