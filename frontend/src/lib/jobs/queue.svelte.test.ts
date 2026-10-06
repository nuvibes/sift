import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { isFinished, OLDER_KINDS, Queue, QUEUE_PAGE } from './queue.svelte';
import type { JobsPage } from './family';
import { imports } from '$lib/library/imports.svelte';

/* Where the screen's numbers come from, and (the part worth guarding) when it stops asking.
 *
 * There is no timer and no connection of its own here. The default view is a page of families
 * (`?fold=true`); choosing a state asks for the families whose row shows it, folded too. Both are
 * asked when the connection says the queue has moved, which is why an idle library costs nothing.
 *
 * The default view is not the busy indicator's page: the indicator counts every step, this screen
 * draws families, so these tests hold the fold.
 */

const EMPTY: JobsPage = {
	jobs: [],
	total: 0,
	counts: {},
	tallies: {},
	by_type: {},
	names: {},
	older: [],
	work: {},
	families: {},
	housekeeping: [],
	stepping_back: false,
	step_back_share: 25,
	step_back_for: null,
	step_back_over: [],
	turbo_mode: false,
	password_wanted: 0
};

function page(over: Partial<JobsPage> = {}): JobsPage {
	return { ...EMPTY, ...over };
}

let fetched: string[] = [];

/** The server answering normally. */
function serveFetch(answer: JobsPage = page({ total: 7 })) {
	vi.stubGlobal(
		'fetch',
		vi.fn(async (url: URL) => {
			fetched.push(url.toString());
			return { ok: true, status: 200, json: async () => answer } as Response;
		})
	);
}

/** The server saying no. The address is admin-only and the nav item is hidden, not the address. */
function refuseFetch(status: number) {
	vi.stubGlobal(
		'fetch',
		vi.fn(async (url: URL) => {
			fetched.push(url.toString());
			return { ok: false, status, json: async () => ({}) } as Response;
		})
	);
}

/** The server not answering at all. Unreachable rather than unwilling. */
function failFetch() {
	vi.stubGlobal(
		'fetch',
		vi.fn(async () => {
			throw new TypeError('network');
		})
	);
}

beforeEach(() => {
	fetched = [];
	// The shared page is a module singleton, so a test that left one behind would hand it to the
	// next test as if the server had said it.
	imports.page = null;
	imports.problem = null;
	imports.live = false;
	imports.forgetRefusal();
	serveFetch();
});

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('the default view', () => {
	it('is a page of families, folded, read once', async () => {
		const queue = new Queue();

		await queue.refresh();

		expect(queue.page?.total).toBe(7);
		const asked = fetched.filter((url) => url.includes('/jobs'));
		expect(asked).toHaveLength(1);
		expect(asked[0]).toContain('fold=true');
		expect(queue.folded).toBe(true);
	});

	it('says it is live once a read has landed', async () => {
		const queue = new Queue();

		await queue.refresh();

		expect(queue.live).toBe(true);
	});
});

describe('choosing a state', () => {
	it('asks the server that question rather than filtering what it already has', async () => {
		/* Filtering the fifty rows in hand would put a number above the table that disagrees with
		   it: the tallies describe the whole queue, so "Failed 61" over the three failures that
		   happened to be in the last fifty rows is a screen that lies quietly. */
		const queue = new Queue();
		queue.setFilter('failed');
		await vi.waitFor(() => expect(fetched.some((url) => url.includes('state=failed'))).toBe(true));

		expect(queue.filter).toBe('failed');
	});

	/* One universe for every tab: All counts families, so a state's tab lists the families whose
	   row shows that state and its number counts them. Flat steps under a tab counted in
	   families would read Done and Failed together as more than All. */
	it('asks for the families whose row shows the state, folded like All', async () => {
		const queue = new Queue();
		queue.setFilter('failed');
		await vi.waitFor(() => expect(fetched.some((url) => url.includes('state=failed'))).toBe(true));

		expect(fetched.find((url) => url.includes('state=failed'))).toContain('fold=true');
		expect(queue.folded).toBe(true);
	});

	/* The tallies and the task rows above the list read the held page. Emptied on every press, the
	   whole tab would blank for a read and the screen jump to its top under the reader. */
	it('keeps the page it has until the new filter has its own, and only the list waits', async () => {
		const queue = new Queue();
		await queue.refresh();
		const held = queue.page;
		expect(queue.listed).toBe(held);

		const landed = queue.setFilter('failed');
		expect(queue.page).toBe(held);
		expect(queue.listed).toBeNull();

		await landed;
		expect(queue.listed).not.toBeNull();
		expect(fetched.some((url) => url.includes('state=failed'))).toBe(true);
	});

	it('goes back to the folded page when the filter is cleared', async () => {
		const queue = new Queue();
		queue.setFilter('failed');
		await vi.waitFor(() => expect(fetched.length).toBeGreaterThan(0));

		queue.setFilter(null);
		await vi.waitFor(() => expect(fetched.some((url) => url.includes('fold=true'))).toBe(true));
		await vi.waitFor(() => expect(queue.page).not.toBeNull());
	});
});

describe('choosing a kind of task', () => {
	/* The list's Type narrowing. A kind asks flat, even under All: a step of an import is a task of
	   its own kind, and a folded page counts only families that START with that kind. */
	it('asks for that kind flat, with the state beside it when one is chosen', async () => {
		const queue = new Queue();
		await queue.setKind('preview');
		const asked = fetched.find((url) => url.includes('type=preview'));
		expect(asked).toBeDefined();
		expect(asked).not.toContain('fold');
		expect(queue.folded).toBe(false);
		expect(queue.listed).not.toBeNull();

		await queue.setFilter('failed');
		const both = fetched.at(-1) ?? '';
		expect(both).toContain('type=preview');
		expect(both).toContain('state=failed');
	});

	it('asks for the older kinds as one choice, never by a stored id', async () => {
		const queue = new Queue();
		await queue.setKind(OLDER_KINDS);
		const asked = fetched.at(-1) ?? '';
		expect(asked).toContain('older=true');
		expect(asked).not.toContain('type=');
		expect(asked).not.toContain('fold');
	});

	it('holds no page for a kind while the one in hand answers another question', async () => {
		const queue = new Queue();
		await queue.refresh();
		const landed = queue.setKind('preview');
		expect(queue.listed).toBeNull();
		await landed;
		expect(queue.listed).not.toBeNull();

		await queue.setKind(null);
		expect(fetched.at(-1)).toContain('fold=true');
		expect(queue.folded).toBe(true);
	});
});

describe('when the server says no', () => {
	it('stops asking, rather than asking again on behalf of somebody never getting in', async () => {
		refuseFetch(403);
		const queue = new Queue();

		await queue.refresh();
		const asked = fetched.length;
		await queue.refresh();

		expect(queue.problem).toMatch(/admin/);
		expect(fetched).toHaveLength(asked);
	});

	it('keeps asking when the server is merely unreachable', async () => {
		failFetch();
		const queue = new Queue();

		await queue.refresh();
		const problem = queue.problem;
		serveFetch();
		await queue.refresh();

		// Unreachable is not unwilling: the answer came back the moment the server did.
		expect(problem).not.toBeNull();
		expect(queue.page?.total).toBe(7);
	});
});

describe('a family opened out', () => {
	it('is folded until somebody opens it, and its steps are read then and not before', async () => {
		const queue = new Queue();
		await queue.refresh();

		expect(queue.isOpen('top')).toBe(false);
		expect(fetched.some((url) => url.includes('/steps'))).toBe(false);

		serveSteps();
		await queue.toggle('top');

		expect(queue.isOpen('top')).toBe(true);
		expect(fetched.some((url) => url.includes('/jobs/top/steps'))).toBe(true);
		expect(queue.stepsOf('top')?.total).toBe(8);
	});

	it('forgets its steps when it is folded again', async () => {
		const queue = new Queue();
		serveSteps();
		await queue.toggle('top');
		await queue.toggle('top');

		expect(queue.isOpen('top')).toBe(false);
		expect(queue.stepsOf('top')).toBeNull();
	});
});

/** The server answering a family's steps, and the page for anything else. */
function serveSteps() {
	vi.stubGlobal(
		'fetch',
		vi.fn(async (url: URL) => {
			const address = url.toString();
			fetched.push(address);
			const answer = address.includes('/steps')
				? { jobs: [], total: 8, at_least: false }
				: page({ total: 7 });
			return { ok: true, status: 200, json: async () => answer } as Response;
		})
	);
}

describe('whether a job is over', () => {
	it('knows the three states a job ends in', () => {
		expect(isFinished('done')).toBe(true);
		expect(isFinished('failed')).toBe(true);
		expect(isFinished('canceled')).toBe(true);
	});

	it('does not treat a name Sift has no state for as finished', () => {
		/* `done` is the finished state. A download watched for a state Sift does not have would
		   sit where it was forever, and the only way to learn the models had arrived would be a
		   reload. */
		expect(isFinished('succeeded')).toBe(false);
		expect(isFinished('running')).toBe(false);
	});
});

/* THE LIST IS PAGED BY NUMBER, so every page of a big import's queue can be reached: a
   page is a fixed fifty, so the pager under it can name its pages, and a new filter starts at the
   first. */
describe('the pages of the list', () => {
	it('reads the page it is turned to, at a fixed size', async () => {
		serveFetch(page({ total: 3068 }));
		const queue = new Queue();

		await queue.goTo(QUEUE_PAGE * 2);

		const asked = fetched.filter((url) => url.includes('/jobs')).at(-1) ?? '';
		expect(asked).toContain(`offset=${QUEUE_PAGE * 2}`);
		expect(asked).toContain(`limit=${QUEUE_PAGE}`);
		expect(queue.offset).toBe(QUEUE_PAGE * 2);
	});

	it('starts a new filter at the first page', async () => {
		const queue = new Queue();
		await queue.goTo(QUEUE_PAGE);

		await queue.setFilter('failed');

		expect(queue.offset).toBe(0);
		expect(fetched.at(-1)).toContain('offset=0');
	});

	it('reads the last page there is when the one it was on has emptied', async () => {
		serveFetch(page({ total: 60 }));
		const queue = new Queue();

		await queue.goTo(QUEUE_PAGE * 3);

		expect(queue.offset).toBe(QUEUE_PAGE);
	});
});

describe('a busy queue', () => {
	it('asks one question at a time, and once more for everybody who asked meanwhile', async () => {
		const answers: Array<(page: JobsPage) => void> = [];
		vi.stubGlobal(
			'fetch',
			vi.fn((url: URL) => {
				fetched.push(url.toString());
				return new Promise<Response>((resolve) =>
					answers.push((answer) =>
						resolve({ ok: true, status: 200, json: async () => answer } as Response)
					)
				);
			})
		);
		const queue = new Queue();
		const asks = [queue.refresh(), queue.refresh(), queue.refresh()];
		await vi.waitFor(() => expect(answers).toHaveLength(1));

		answers[0](page({ total: 1 }));
		await vi.waitFor(() => expect(answers).toHaveLength(2));
		expect(queue.page?.total).toBe(1);
		answers[1](page({ total: 2 }));
		await Promise.all(asks);

		expect(fetched.filter((url) => url.includes('/jobs'))).toHaveLength(2);
		expect(queue.page?.total).toBe(2);
	});
});

describe('a cancel', () => {
	const running = page({ total: 1, jobs: [{ id: 'j1', state: 'running' } as never] });

	it('draws the row canceled on the press, and puts it back when the server refuses', async () => {
		const queue = new Queue();
		serveFetch(running);
		await queue.refresh();
		let refuse: () => void = () => {};
		vi.stubGlobal(
			'fetch',
			vi.fn(async (url: URL, init?: RequestInit) => {
				if (init?.method === 'POST') {
					await new Promise<void>((resolve) => (refuse = resolve));
					return { ok: false, status: 500, json: async () => ({}) } as Response;
				}
				return { ok: true, status: 200, json: async () => running } as Response;
			})
		);
		const canceling = queue.cancel('j1');
		expect(queue.page?.jobs[0]?.state).toBe('canceled');
		refuse();
		await expect(canceling).rejects.toBeDefined();
		expect(queue.page?.jobs[0]?.state).toBe('running');
	});
});
