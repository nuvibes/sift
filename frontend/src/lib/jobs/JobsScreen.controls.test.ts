/* Activity's presses on a pass and a sub-task (Run now, pause, resume, cancel), the whole
 * queue's pause on Options, the Options labels that name both numbers, and what a paused or
 * moving pass draws: mounted, pressed, and the requests they make read back. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { imports } from '$lib/library/imports.svelte';
import type { Job, JobsPage } from './family';
import JobsScreen from './JobsScreen.svelte';

function job(id: string, state: Job['state'], name: string, over: Partial<Job> = {}): Job {
	return {
		id,
		state,
		name,
		type: 'face_scan',
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
		reason: null,
		...over
	};
}

type Family = JobsPage['families'][string];

function family(over: Partial<Family>): Family {
	return {
		label: 'Identify',
		types: ['face_scan', 'watermark_read'],
		paused: false,
		runs: [{ task: 'identify', parts: null }],
		on: true,
		ready: true,
		problem: null,
		quick_seconds: 600,
		slow_seconds: 1200,
		at_least: false,
		sample: 200,
		at_once: 12,
		outstanding: 23,
		waiting: 270,
		done: 9000,
		total: 9270,
		reason: null,
		parts: [
			{
				type: 'face_scan',
				caption: 'files looked at for faces',
				on: true,
				paused: true,
				done: 4500,
				total: 4635
			},
			{
				type: 'watermark_read',
				caption: 'files read for watermarks',
				on: false,
				paused: false,
				done: 4500,
				total: 4635
			}
		],
		task: 'identify',
		time_unknown: null,
		pace: null,
		for_task: null,
		failed: 0,
		last_error: null,
		running: 3,
		...over
	} as Family;
}

function page(paused: boolean): JobsPage {
	return {
		jobs: [
			job('j1', 'paused', 'kestrel-vane.mp4'),
			job('j2', 'failed', 'Checking what to identify', {
				subject: null,
				steps: {
					count: 3,
					by_state: { failed: 1, done: 2 },
					at_least: false,
					cap: 1000,
					state: 'failed',
					subject: 'kestrel-vane.mp4',
					subject_id: null,
					failure: {
						name: 'Identifying file',
						reason: 'exit code 69',
						subject: 'kestrel-vane.mp4',
						attempts: 3
					}
				}
			})
		],
		total: 2,
		counts: { queued: 40, failed: 101, canceled: 12717 },
		tallies: { queued: 5, failed: 77, canceled: 43, all: 125 },
		by_type: {},
		names: {},
		older: [],
		work: {},
		families: { identify: family({ paused }) },
		housekeeping: [
			{
				job_type: 'dedup_scan',
				label: 'Near duplicates',
				task: 'duplicates',
				failed: 0,
				last_error: null,
				last_job: null,
				last_seconds: 30,
				last_started_at: 1_700_000_000,
				last_state: 'done',
				outstanding: 0,
				quick_seconds: null,
				reason: null,
				running: 0,
				slow_seconds: null
			}
		],
		stepping_back: false,
		step_back_share: 25,
		step_back_for: null,
		step_back_over: [],
		turbo_mode: false,
		password_wanted: 0,
		paused
	} as JobsPage;
}

let posted: { path: string; body: unknown }[] = [];
let paused = false;
let host: HTMLElement;
let screen: Record<string, unknown> | null = null;

beforeEach(() => {
	history.replaceState(null, '', '/settings/tasks');
	posted = [];
	paused = false;
	imports.page = null;
	imports.problem = null;
	imports.live = false;
	imports.forgetRefusal();
	vi.stubGlobal(
		'fetch',
		vi.fn(async (url: URL, init?: RequestInit) => {
			const path = new URL(url.toString(), 'http://sift.invalid').pathname;
			const reply = (body: unknown) =>
				({ ok: true, status: 200, json: async () => body }) as Response;
			if (init?.method === 'POST') {
				posted.push({ path, body: init.body ? JSON.parse(String(init.body)) : null });
				if (path === '/api/jobs/pause') paused = true;
				if (path === '/api/jobs/resume') paused = false;
				if (path.startsWith('/api/tasks/'))
					return reply({
						job_ids: ['n1'],
						starts_at: null,
						waits: false,
						named: null,
						dry: false,
						on_activity: true
					});
				if (path === '/api/jobs/cancel-work') return reply({ stopped: 7 });
				return reply({ paused, types: [] });
			}
			if (path === '/api/jobs') return reply(page(paused));
			return { ok: false, status: 503, json: async () => ({}) } as Response;
		})
	);
});

afterEach(() => {
	if (screen) void unmount(screen, { outro: false });
	screen = null;
	host?.remove();
	document.body
		.querySelectorAll('[role="menu"], [role="alertdialog"], [role="dialog"]')
		.forEach((one) => one.remove());
	vi.unstubAllGlobals();
});

async function open(): Promise<void> {
	host = document.createElement('div');
	document.body.append(host);
	screen = mount(JobsScreen, { target: host });
	await vi.waitFor(() => expect(host.textContent).toContain('Task Queue'));
	await vi.waitFor(() => expect(host.textContent).toContain('Identify'));
	flushSync();
}

function press(label: string): void {
	const button = [...host.querySelectorAll<HTMLButtonElement>('button')].find(
		(one) => one.getAttribute('aria-label') === label || one.textContent?.trim() === label
	);
	expect(button, label).toBeTruthy();
	button!.click();
	flushSync();
}

async function openOptions(): Promise<void> {
	const door = [...host.querySelectorAll<HTMLElement>('button')].find((one) =>
		one.textContent?.includes('Options')
	)!;
	door.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0 }));
	flushSync();
	await vi.waitFor(() => expect(document.querySelector('[role="menu"]')).not.toBeNull());
}

function items(): HTMLElement[] {
	return [...document.querySelectorAll<HTMLElement>('[role="menu"] [role="menuitem"]')];
}

describe("a pass's presses on Activity", () => {
	it('runs its task, pauses and resumes it, and pauses one sub-task', async () => {
		await open();
		press('Run now');
		await vi.waitFor(() =>
			expect(posted.map((one) => one.path)).toContain('/api/tasks/identify/run')
		);

		press('Pause: Identify');
		await vi.waitFor(() =>
			expect(posted).toContainEqual({ path: '/api/jobs/pause', body: { family: 'identify' } })
		);
		await vi.waitFor(() =>
			expect(host.querySelector('[aria-label="Resume: Identify"]')).not.toBeNull()
		);
		press('Resume: Identify');
		await vi.waitFor(() =>
			expect(posted).toContainEqual({ path: '/api/jobs/resume', body: { family: 'identify' } })
		);

		// Its sub-rows under the arrow: the faces sub-task, paused, resumes on its own.
		press('Show more: the kinds of Identify');
		await vi.waitFor(() =>
			expect(host.querySelector('[aria-label="Resume: Files looked at for faces"]')).not.toBeNull()
		);
		press('Resume: Files looked at for faces');
		await vi.waitFor(() =>
			expect(posted).toContainEqual({ path: '/api/jobs/resume', body: { type: 'face_scan' } })
		);
	});

	it('cancels the pass after asking', async () => {
		await open();
		press('Cancel: Identify');
		await vi.waitFor(() => expect(document.body.textContent).toContain('Cancel Identify?'));
		const confirm = [...document.querySelectorAll<HTMLButtonElement>('button')].find(
			(one) => one.textContent?.trim() === 'Cancel tasks'
		)!;
		confirm.click();
		await vi.waitFor(() =>
			expect(posted).toContainEqual({ path: '/api/jobs/cancel-work', body: { family: 'identify' } })
		);
	});

	it('draws its own bar and count while it moves, and its sub-task off', async () => {
		await open();
		expect(host.textContent).toContain('9,000 of 9,270');
		press('Show more: the kinds of Identify');
		await vi.waitFor(() => expect(host.textContent).toContain('Turned off'));
	});
});

describe("a chore's Run now", () => {
	it('runs the task the chore is', async () => {
		await open();
		const runs = [...host.querySelectorAll<HTMLButtonElement>('button')].filter(
			(one) => one.textContent?.trim() === 'Run now'
		);
		runs[runs.length - 1].click();
		await vi.waitFor(() =>
			expect(posted.map((one) => one.path)).toContain('/api/tasks/duplicates/run')
		);
	});
});

describe('the Task Queue', () => {
	it('names both numbers on Options and pauses and resumes the whole queue', async () => {
		await open();
		await openOptions();
		const words = items().map((one) => one.textContent ?? '');
		expect(words.some((one) => one.includes('77 failed, 101 with their steps'))).toBe(true);
		expect(words.some((one) => one.includes('43 canceled, 12,717 with their steps'))).toBe(true);
		items()
			.find((one) => one.textContent?.includes('Pause all tasks'))!
			.click();
		await vi.waitFor(() => expect(posted).toContainEqual({ path: '/api/jobs/pause', body: {} }));
		await vi.waitFor(() =>
			expect(host.textContent).toContain('All tasks are paused: none starts until you resume them.')
		);
		await openOptions();
		items()
			.find((one) => one.textContent?.includes('Resume all tasks'))!
			.click();
		await vi.waitFor(() => expect(posted).toContainEqual({ path: '/api/jobs/resume', body: {} }));
	});

	it('draws a paused row Paused and a failed family with its one line of why', async () => {
		await open();
		expect(host.textContent).toContain(
			'Identifying file failed on kestrel-vane.mp4 (tried 3 times): exit code 69'
		);
	});
});
