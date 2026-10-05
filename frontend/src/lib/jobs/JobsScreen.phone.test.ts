/*
 * The Activity pane at a phone's width: every row a card, with the same words the columns hold.
 *
 * Five tracks across a phone would print "Time left" over "Progress" and stand a count as three
 * lines of one number each. A card keeps every fact the row says and gives each its own line, so this
 * mounts the pane at both widths and reads what each draws.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { imports } from '$lib/library/imports.svelte';
import { phoneWidth } from '$lib/components/common/phone-width.svelte';
import type { Job, JobsPage } from './family';
import JobsScreen from './JobsScreen.svelte';

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
		waits_for_password: false
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
	full_amount: false,
	password_wanted: 0
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
	vi.stubGlobal(
		'fetch',
		vi.fn(async () => ({ ok: true, status: 200, json: async () => PAGE }) as Response)
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
	it('draws each job as one card holding its name, its state, when, its tries and its bar', async () => {
		phoneWidth.yes = true;
		const list = await open();

		const cards = [...list.querySelectorAll('.card')];
		expect(cards).toHaveLength(2);
		const running = cards.find((one) => one.textContent?.includes('wren-halloway-one.mp4'))!;
		expect(running.querySelector('.card-facts .badge')).not.toBeNull();
		expect(running.querySelector('.when')).not.toBeNull();
		expect(running.querySelector('.track')).not.toBeNull();
		const failed = cards.find((one) => one.textContent?.includes('odile-fenwick.mp4'))!;
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
