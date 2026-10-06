import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { imports } from '$lib/library/imports.svelte';
import { session, type Viewer } from '$lib/shell/session.svelte';
import type { Job, JobsPage } from './family';
import JobsScreen from './JobsScreen.svelte';

/* A task parked for the password says which key in its own sentence and offers the field on its
 * row, so a run of starter pictures does not sit on this screen saying only that it is blocked; a
 * wait for anything else (the face models, say) keeps its own words and is offered no password.
 */

const SEALED = 'Waiting for your password to unlock the stash-box keys.';

function job(id: string, error: string, waits: boolean): Job {
	return {
		id,
		state: 'blocked',
		name: 'Adding starter pictures from stash-boxes',
		type: 'face_starters',
		attempts: 0,
		max_attempts: 3,
		created_at: 1_700_000_000,
		updated_at: 1_700_000_000,
		error,
		note: null,
		parent_id: null,
		position: null,
		progress: 0,
		run_after: null,
		subject: null,
		subject_id: null,
		steps: null,
		waits_for_password: waits
	};
}

const PAGE: JobsPage = {
	jobs: [job('j1', SEALED, true), job('j2', 'The models are not on disk.', false)],
	total: 2,
	counts: { blocked: 2 },
	tallies: { blocked: 2, all: 2 },
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
	password_wanted: 1
};

const VIEWER: Viewer = {
	id: 'u1',
	username: 'wren',
	role: 'admin',
	csrf_token: 't',
	can_save_to_device: false,
	secrets_locked: true,
	pin_unlock_offered: false,
	locked: false,
	zone: null,
	boot: null
};

let host: HTMLElement;
let screen: Record<string, unknown> | null = null;

beforeEach(() => {
	// The queue is the Activity tab; Tasks is where the section opens without one.
	history.replaceState(null, '', '/settings/tasks?show=now');
	imports.page = null;
	imports.problem = null;
	imports.live = false;
	imports.forgetRefusal();
	session.viewer = { ...VIEWER };
	vi.stubGlobal(
		'fetch',
		vi.fn(async () => ({ ok: true, status: 200, json: async () => PAGE }) as Response)
	);
});

afterEach(() => {
	if (screen) void unmount(screen, { outro: false });
	screen = null;
	host?.remove();
	vi.unstubAllGlobals();
	session.viewer = undefined;
});

async function open() {
	host = document.createElement('div');
	document.body.append(host);
	screen = mount(JobsScreen, { target: host });
	await vi.waitFor(() => expect(host.textContent).toContain(SEALED));
	flushSync();
}

const presses = () =>
	[...host.querySelectorAll('button')].filter((one) => one.textContent?.trim() === 'Unlock');

describe('a task parked for the password, on Now', () => {
	it('offers Unlock on its row and on no other', async () => {
		await open();

		expect(presses(), 'the row parked for the password offered no Unlock').toHaveLength(1);
		expect(host.textContent).toContain('The models are not on disk.');
	});

	it('opens the password field where the row is', async () => {
		await open();
		expect(host.querySelector('input[type="password"]')).toBeNull();

		presses()[0].click();
		flushSync();

		const box = host.querySelector('input[type="password"]');
		expect(box, 'Unlock opened no field').not.toBeNull();
		expect(document.activeElement).toBe(box);
	});

	it('offers nothing once the keys are unlocked', async () => {
		session.viewer = { ...VIEWER, secrets_locked: false };
		await open();

		expect(presses()).toHaveLength(0);
	});
});

describe('a benchmark Sift stopped for other work', () => {
	const STOPPED =
		"Sift stopped the full benchmark because this device is in use. It runs again by itself once Sift has nothing else to do and nobody's using this device.";
	const RUNNING = 'Benchmarking this device so Sift can make the best use of it.';
	const canceled = (id: string, error: string | null): Job => ({
		...job(id, '', false),
		state: 'canceled',
		name: 'Benchmarking this device',
		type: 'performance_benchmark',
		error,
		note: RUNNING
	});

	it("says why on its row, whole; a person's own Cancel says only Canceled", async () => {
		const page = { ...PAGE, jobs: [canceled('b1', STOPPED), canceled('b2', null)], total: 2 };
		vi.stubGlobal(
			'fetch',
			vi.fn(async () => ({ ok: true, status: 200, json: async () => page }) as Response)
		);
		host = document.createElement('div');
		document.body.append(host);
		screen = mount(JobsScreen, { target: host });
		await vi.waitFor(() => expect(host.textContent).toContain(STOPPED));
		flushSync();

		const why = [...host.querySelectorAll('.why')];
		expect(why.map((one) => one.textContent)).toEqual([STOPPED]);
		expect(why[0].classList.contains('whole')).toBe(true);
		expect(host.textContent).not.toContain(RUNNING);
	});
});
