/* The Activity pane at a phone's width: every row a card, with the same words the columns hold. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { imports } from '$lib/library/imports.svelte';
import { phoneWidth } from '$lib/components/common/phone-width.svelte';
import type { Job, JobsPage } from './family';
import JobsScreen from './JobsScreen.svelte';

/* The queue's reads answered; the Tasks tab's own reads, around Activity, refused. */
const DOWN = { ok: false, status: 503, json: async () => ({}) } as Response;
function answer(page: JobsPage): Response {
	return { ok: true, status: 200, json: async () => page } as Response;
}

function job(id: string, state: Job['state'], name: string, attempts = 0): Job {
	return {
		id,
		state,
		name,
		type: 'thumbnail',
		attempts,
		max_attempts: 3,
		created_at: 1_700_000_000,
		updated_at: 1_700_000_000,
		error: null,
		note: null,
		parent_id: null,
		position: null,
		progress: 0.5,
		run_after: null,
		subject: name,
		subject_id: null,
		steps: null,
		waits_for_password: false,
		reason: null
	};
}

const PAGE: JobsPage = {
	jobs: [
		job('j1', 'running', 'wren-halloway-one.mp4'),
		job('j2', 'failed', 'odile-fenwick.mp4', 2)
	],
	total: 2,
	counts: { running: 1, failed: 1 },
	tallies: { running: 1, failed: 1, all: 2 },
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

let host: HTMLElement;
let screen: Record<string, unknown> | null = null;

beforeEach(() => {
	// Activity is drawn under the tasks on the Tasks tab, where the section opens.
	history.replaceState(null, '', '/settings/tasks');
	imports.page = null;
	imports.problem = null;
	imports.live = false;
	imports.forgetRefusal();
	vi.stubGlobal(
		'fetch',
		vi.fn(async (url: URL) => (String(url).includes('/api/jobs') ? answer(PAGE) : DOWN))
	);
});

afterEach(() => {
	if (screen) void unmount(screen, { outro: false });
	screen = null;
	host?.remove();
	phoneWidth.yes = false;
	vi.unstubAllGlobals();
});

async function open(): Promise<HTMLElement> {
	host = document.createElement('div');
	document.body.append(host);
	screen = mount(JobsScreen, { target: host });
	await vi.waitFor(() => expect(host.textContent).toContain('wren-halloway-one.mp4'));
	flushSync();
	return host.querySelector<HTMLElement>('ul[aria-label="Activity"]')!;
}

describe('the Activity list on a phone', () => {
	it('draws each job as one card holding its name, its state, when it finished, its tries and its bar', async () => {
		phoneWidth.yes = true;
		const list = await open();

		const cards = [...list.querySelectorAll('.card')];
		expect(cards).toHaveLength(2);
		const running = cards.find((one) => one.textContent?.includes('wren-halloway-one.mp4'))!;
		expect(running.querySelector('.card-facts .badge')).not.toBeNull();
		// A row still going has no time to say: when it finished is a finished row's.
		expect(running.querySelector('.when')).toBeNull();
		expect(running.querySelector('.track')).not.toBeNull();
		const failed = cards.find((one) => one.textContent?.includes('odile-fenwick.mp4'))!;
		expect(failed.querySelector('.when')).not.toBeNull();
		expect(failed.querySelector('.attempts')?.textContent).toBe('try 2/3');
	});

	it('declares one column and no heading row over it', async () => {
		phoneWidth.yes = true;
		const list = await open();

		expect(list.style.getPropertyValue('--data-tracks')).toBe(
			'minmax(0, 1fr) var(--control-height-sm)'
		);
		expect(list.querySelector('.heads')).toBeNull();
	});

	it('keeps the columns and their headings on a desktop window', async () => {
		const list = await open();

		expect(list.querySelector('.card')).toBeNull();
		expect(list.querySelector('.heads')?.textContent).toContain('Status');
	});
});
