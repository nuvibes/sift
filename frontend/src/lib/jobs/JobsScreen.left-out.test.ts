/* A pass with nothing left but files its products gave up on says how many, and the words open
 * those files: the page Import tasks' count opens, one press from Activity. */
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { JobsPage } from './family';
import JobsScreen from './JobsScreen.svelte';
import { drilldown } from '$lib/settings-ui/drilldown.svelte';

const PAGE = {
	jobs: [],
	total: 0,
	counts: {},
	tallies: { all: 0 },
	by_type: {},
	names: {},
	older: [],
	work: {},
	families: {
		identify: {
			label: 'Identify',
			types: ['face_scan'],
			outstanding: 0,
			waiting: 0,
			done: 10,
			total: 10,
			task: 'identify'
		}
	},
	housekeeping: [],
	stepping_back: false,
	step_back_share: 25,
	step_back_for: null,
	step_back_over: [],
	turbo_mode: false,
	password_wanted: 0,
	paused: false
} as unknown as JobsPage;

const SHEET = {
	rows: [{ key: 'faces', label: 'Faces', cannot: 1 }],
	files: 0,
	unread: 0,
	measure_first: false
};

let asked: string[] = [];
let host: HTMLElement;
let screen: Record<string, unknown> | null = null;

beforeEach(() => {
	history.replaceState(null, '', '/settings/tasks');
	asked = [];
	vi.stubGlobal(
		'fetch',
		vi.fn(async (url: URL) => {
			const address = url.toString();
			asked.push(address);
			const answer = address.includes('/api/jobs')
				? PAGE
				: address.includes('/api/importing/build')
					? SHEET
					: address.includes('/api/assets')
						? { items: [], total: 0 }
						: null;
			if (answer === null) return { ok: false, status: 503, json: async () => ({}) } as Response;
			return { ok: true, status: 200, json: async () => answer } as Response;
		})
	);
});

afterEach(() => {
	if (screen) void unmount(screen, { outro: false });
	screen = null;
	host?.remove();
	drilldown.close();
	vi.unstubAllGlobals();
});

it("says a finished pass's left-out files, and the words open them", async () => {
	host = document.createElement('div');
	document.body.append(host);
	screen = mount(JobsScreen, { target: host });
	const press = await vi.waitFor(() => {
		const found = [...host.querySelectorAll('button')].find((one) =>
			one.textContent?.includes('1 left out')
		);
		expect(found).toBeDefined();
		return found!;
	});
	press.click();
	flushSync();
	expect(drilldown.title).toBe('Left out: Identify');
	await vi.waitFor(() =>
		expect(asked.some((one) => one.includes('/api/assets') && one.includes('left_out=faces'))).toBe(
			true
		)
	);
});
