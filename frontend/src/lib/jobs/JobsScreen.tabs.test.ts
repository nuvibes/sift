/*
 * Settings > Tasks and Activity opens on Tasks: the section's first tab is the one a person sets,
 * with Activity, the queue, drawn under the tasks and read there.
 *
 * A link to a task's row is followed from any tab: the row is one per task, from the server, so no
 * table can list it ahead of time, and the section's own claim (`drilldown.ownSection`) turns the
 * screen back to Tasks before the hunt looks for the row.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { drilldown } from '$lib/settings-ui/drilldown.svelte';
import { COPY as TASKS } from '$lib/settings-ui/ScheduledTasks.search';
import JobsScreen from './JobsScreen.svelte';

let host: HTMLElement;
let screen: Record<string, unknown> | null = null;
let asked: string[] = [];

beforeEach(() => {
	asked = [];
	history.replaceState(null, '', '/settings/tasks');
	vi.stubGlobal(
		'fetch',
		vi.fn(async (url: URL) => {
			asked.push(url.toString());
			return { ok: false, status: 503, json: async () => ({}) } as Response;
		})
	);
});

afterEach(() => {
	if (screen) void unmount(screen, { outro: false });
	screen = null;
	host?.remove();
	vi.unstubAllGlobals();
	history.replaceState(null, '', '/');
});

function open(): void {
	host = document.createElement('div');
	document.body.append(host);
	screen = mount(JobsScreen, { target: host });
	flushSync();
}

const tabs = () =>
	[...host.querySelector('[role="tablist"]')!.querySelectorAll<HTMLElement>('[role="tab"]')].map(
		(one) => [one.textContent?.trim(), one.getAttribute('aria-selected')]
	);

describe('the tabs of Tasks and Activity', () => {
	it('opens on Tasks, drawing when each task runs and Activity under them', async () => {
		open();
		expect(tabs()).toEqual([
			['Tasks', 'true'],
			['App History', 'false'],
			['Logs', 'false']
		]);
		expect(host.querySelector('#activity-tab')?.textContent).toContain(TASKS.lede);
		await vi.waitFor(() =>
			expect(asked.filter((one) => one.includes('/api/jobs')).length).toBeGreaterThan(0)
		);
	});

	it('turns back to Tasks for a link to a task row followed from another tab', () => {
		history.replaceState(null, '', '/settings/tasks?show=history');
		open();
		expect(tabs()[1]).toEqual(['App History', 'true']);
		expect(host.querySelector('#activity-tab')?.textContent).not.toContain(TASKS.lede);

		expect(drilldown.reveal('tasks.scan.when', 'tasks')).toBe(true);
		flushSync();

		expect(tabs()[0]).toEqual(['Tasks', 'true']);
		expect(host.querySelector('#activity-tab')?.textContent).toContain(TASKS.lede);
	});

	it("keeps a row another tab owns on that tab, and claims no other section's rows", () => {
		open();
		expect(drilldown.reveal('activity.saved', 'tasks')).toBe(true);
		flushSync();
		expect(tabs()[1]).toEqual(['App History', 'true']);
		// The claim is the section's: a key looked for on another section is not this screen's.
		expect(drilldown.reveal('faces.unclaimed', 'faces')).toBe(false);
	});
});
