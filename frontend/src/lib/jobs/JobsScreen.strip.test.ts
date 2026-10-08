/*
 * The Activity pane's state row, drawn and pressed.
 *
 * The source test beside this one says WHICH component draws the row; this one says the row does
 * its job on a real screen: every state with something in it is a tab with its count, the pile
 * waiting on a person wears the mark, and pressing a tab filters the list underneath to it, in
 * place, with no address changed, because this pane is inside the Settings sheet.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { imports } from '$lib/library/imports.svelte';
import type { Job, JobsPage } from './family';
import JobsScreen from './JobsScreen.svelte';
import { exactly } from '$lib/shell/when';
import { UNREACHABLE } from '$lib/shell/unreachable';

function job(id: string, state: Job['state'], name: string): Job {
	return {
		id,
		state,
		name,
		type: 'thumbnail',
		attempts: 0,
		max_attempts: 3,
		created_at: 1_700_000_000,
		updated_at: 1_700_000_000,
		error: null,
		note: null,
		parent_id: null,
		position: null,
		progress: 0,
		run_after: null,
		subject: name,
		subject_id: null,
		steps: null,
		waits_for_password: false,
		reason: null
	};
}

/* The strip's numbers are the server's `tallies`, which count families; `counts` is the rows the
   bulk actions act on, a larger universe, so a pill that read it would show the wrong number. */
function page(jobs: Job[], total = 9): JobsPage {
	return {
		jobs,
		total,
		counts: { running: 30, blocked: 20, failed: 40 },
		tallies: { running: 3, blocked: 2, failed: 4, all: 9 },
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
		password_wanted: 0,
		paused: false
	};
}

const EVERYTHING = page([
	job('j1', 'running', 'bryn-calloway-one.mp4'),
	job('j2', 'blocked', 'odile-fenwick-two.mp4'),
	job('j3', 'failed', 'bex-corrow-three.mp4')
]);
/* A row only the failed page carries, so a test can tell that page arrived from the list narrowed
   in place while it was out. */
const FAILED = page(
	[job('j3', 'failed', 'bex-corrow-three.mp4'), job('j4', 'failed', 'bex-corrow-four.mp4')],
	4
);

let asked: string[] = [];
let host: HTMLElement;
let screen: Record<string, unknown> | null = null;

beforeEach(() => {
	// Activity is drawn under the tasks on the Tasks tab, where the section opens.
	history.replaceState(null, '', '/settings/tasks');
	asked = [];
	imports.page = null;
	imports.problem = null;
	imports.live = false;
	imports.forgetRefusal();
	vi.stubGlobal(
		'fetch',
		vi.fn(async (url: URL) => {
			const address = url.toString();
			asked.push(address);
			// The Tasks tab's own reads, around Activity, refused: these tests are about the queue.
			if (!address.includes('/api/jobs'))
				return { ok: false, status: 503, json: async () => ({}) } as Response;
			const answer = address.includes('state=failed') ? FAILED : EVERYTHING;
			return { ok: true, status: 200, json: async () => answer } as Response;
		})
	);
});

afterEach(() => {
	if (screen) void unmount(screen, { outro: false });
	screen = null;
	host?.remove();
	vi.unstubAllGlobals();
});

async function open() {
	host = document.createElement('div');
	document.body.append(host);
	screen = mount(JobsScreen, { target: host });
	await vi.waitFor(() =>
		expect(host.querySelector('.filters [role="tablist"]')?.textContent).toContain('9')
	);
	flushSync();
	/* The state row, which filters the queue, under the screen's own tabs, which are a
	   row of the same kind and are not what these tests are about. */
	const tabs = () => [...host.querySelectorAll('.filters [role="tab"]')] as HTMLButtonElement[];
	return {
		tabs,
		tabFor: (label: string) => tabs().find((one) => one.textContent?.includes(label))!,
		counts: () => tabs().map((one) => one.querySelector('.count')?.textContent),
		selected: () =>
			tabs()
				.filter((one) => one.getAttribute('aria-selected') === 'true')
				.map((one) => one.textContent?.match(/^[A-Za-z ]+/)?.[0].trim())
	};
}

describe("the Activity pane's own tabs", () => {
	it('are Tasks, App History and Logs, with Activity drawn under the tasks', async () => {
		await open();
		// The first row on the screen; the state row is further down, under the tasks.
		const top = [...host.querySelector('[role="tablist"]')!.querySelectorAll('[role="tab"]')];

		expect(top.map((one) => one.textContent?.trim())).toEqual(['Tasks', 'App History', 'Logs']);
		expect(top.map((one) => one.getAttribute('aria-selected'))).toEqual(['true', 'false', 'false']);
	});
});

describe("the Activity pane's state row", () => {
	it('is a tab per state with something in it, each with its count', async () => {
		const row = await open();

		expect(row.tabs().map((one) => one.textContent?.match(/^[A-Za-z ]+/)?.[0].trim())).toEqual([
			'All',
			'In progress',
			'Blocked',
			'Failed'
		]);
		expect(row.counts()).toEqual(['9', '3', '2', '4']);
		expect(row.selected()).toEqual(['All']);
	});

	it('marks the pile that is waiting on a person, and only that one', async () => {
		const row = await open();

		expect(host.querySelectorAll('.attention')).toHaveLength(1);
		expect(row.tabFor('Blocked').querySelector('.attention')).not.toBeNull();
	});

	it('narrows the list in place when a tab is pressed', async () => {
		const row = await open();
		const before = location.href;
		expect(host.textContent).toContain('bryn-calloway-one.mp4');

		row.tabFor('Failed').click();
		flushSync();

		await vi.waitFor(() => expect(host.textContent).toContain('bex-corrow-three.mp4'));
		expect(row.selected()).toEqual(['Failed']);
		expect(host.textContent).not.toContain('bryn-calloway-one.mp4');
		expect(asked.some((address) => address.includes('state=failed'))).toBe(true);
		// In place: the address did not move, so the page behind the sheet is still there.
		expect(location.href).toBe(before);
	});

	/* One universe: every pill is the server's number, All included. On a state's tab the page's
	   total is that state's number, so an All that read it would say 4 over Failed 4. */
	it("keeps every number the server's while a state is chosen, All the sum of the states", async () => {
		const row = await open();

		row.tabFor('Failed').click();
		flushSync();
		await vi.waitFor(() => expect(row.selected()).toEqual(['Failed']));
		await vi.waitFor(() => expect(host.textContent).not.toContain('bryn-calloway-one.mp4'));
		await vi.waitFor(() => expect(host.textContent).toContain('bex-corrow-four.mp4'));

		expect(row.counts()).toEqual(['9', '3', '2', '4']);
		expect(asked.find((address) => address.includes('state=failed'))).toContain('fold=true');
	});
});

describe('a task waiting for its time', () => {
	it('says how far off it starts, in words, with the moment on the hover', async () => {
		const at = Math.floor(Date.now() / 1000) + 20 * 3600;
		const waiting = { ...job('j9', 'queued', 'Clearing old search records'), run_after: at };
		vi.stubGlobal(
			'fetch',
			vi.fn(async () => ({ ok: true, status: 200, json: async () => page([waiting]) }) as Response)
		);
		await open();

		const line = host.querySelector<HTMLElement>('.scheduled');
		expect(line?.textContent?.trim()).toBe('starts in 20 hours');

		line?.closest('.wrap')?.dispatchEvent(new PointerEvent('pointermove', { bubbles: true }));
		await vi.waitFor(() => {
			flushSync();
			expect(document.querySelector('[role="tooltip"]')?.textContent?.trim()).toBe(exactly(at));
		});
	});
});

describe('before the queue has answered', () => {
	const EMPTY_WORDS = 'No tasks are running or waiting.';

	it('draws no empty state while the read is still out, and none when it fails', async () => {
		/* A busy server can take seconds to answer, and the list must not say there is nothing to do. */
		vi.stubGlobal(
			'fetch',
			vi.fn(() => new Promise<Response>(() => {}))
		);
		host = document.createElement('div');
		document.body.append(host);
		screen = mount(JobsScreen, { target: host });
		flushSync();
		await new Promise((done) => setTimeout(done, 20));
		expect(host.textContent).not.toContain(EMPTY_WORDS);
		unmount(screen, { outro: false });
		host.remove();

		vi.stubGlobal(
			'fetch',
			vi.fn(async () => {
				throw new TypeError('Failed to fetch');
			})
		);
		host = document.createElement('div');
		document.body.append(host);
		screen = mount(JobsScreen, { target: host });
		await vi.waitFor(() => expect(host.textContent).toContain(UNREACHABLE));
		expect(host.textContent).not.toContain(EMPTY_WORDS);
	});
});

describe("a running or done row's note", () => {
	it('is drawn beside what the job is doing or ended with, and a failed row carries none', async () => {
		const [running, ended, failed] = EVERYTHING.jobs;
		const unread =
			'1 folder stopped answering partway through, so nothing in it was marked missing or unreadable.';
		running.note = 'Swap with device ABCD-EFGH';
		ended.state = 'done';
		ended.note = unread;
		failed.note = 'Not a doing';
		try {
			await open();
			const doing = [...host.querySelectorAll('.doing')].map((one) => one.textContent).join(' | ');

			expect(doing).toContain('Swap with device ABCD-EFGH');
			expect(doing).toContain(unread);
			expect(doing).not.toContain('Not a doing');
		} finally {
			running.note = ended.note = failed.note = null;
			ended.state = 'blocked';
		}
	});
});

describe('eco mode, under the strip', () => {
	const WORDS = "In eco mode while you're working: using a quarter of this device.";

	it("is said from the rail's read while something is running, and so is turbo mode", async () => {
		await open();
		imports.page = { ...EVERYTHING, stepping_back: true, step_back_for: 'input' };
		flushSync();
		expect(host.textContent).toContain(WORDS);
		imports.page = { ...EVERYTHING, turbo_mode: true, step_back_for: 'input' };
		flushSync();
		expect(host.textContent).toContain("Turbo mode on this device although you're working.");
	});

	it('is not said outside eco mode', async () => {
		await open();
		imports.page = EVERYTHING;
		flushSync();
		expect(host.querySelector('.stepping-back')).toBeNull();
	});
});
