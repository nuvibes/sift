/*
 * The page of files several products gave up on names the product on each file's line, so a file
 * left out twice reads as two reasons rather than one said twice.
 */
import { afterEach, expect, it, vi } from 'vitest';
import { createRawSnippet, flushSync, mount, unmount } from 'svelte';

const mocks = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock('$lib/api/client', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/client')>()),
	api: { get: mocks.get }
}));

import TasksLeftOut from './TasksLeftOut.svelte';
import DrilldownPage from './DrilldownPage.svelte';
import { drilldown } from './drilldown.svelte';

let host: HTMLElement;
const drawn: Record<string, unknown>[] = [];

afterEach(() => {
	for (const one of drawn.splice(0)) void unmount(one);
	drilldown.close();
	host?.remove();
});

it("says each file's product before why, where the page lists several", async () => {
	mocks.get.mockImplementation((_path: string, options: { query: { left_out: string } }) =>
		Promise.resolve({
			items: [{ id: 'A1', original_filename: 'clip.mp4', left_out: "It wouldn't open." }],
			total: options.query.left_out.length > 0 ? 1 : 0
		})
	);
	host = document.createElement('div');
	document.body.append(host);
	const children = createRawSnippet(() => ({ render: () => '<span>2 left out</span>' }));
	drawn.push(mount(DrilldownPage, { target: host, props: { behind: 'Tasks' } }));
	drawn.push(
		mount(TasksLeftOut, {
			target: host,
			props: {
				products: [
					{ key: 'faces', label: 'Faces' },
					{ key: 'watermarks', label: 'Watermarks' }
				],
				title: 'Identify',
				tone: 'quiet',
				children
			}
		})
	);
	host.querySelector('button')!.click();
	flushSync();
	await vi.waitFor(() =>
		expect([...host.querySelectorAll('.why')].map((one) => one.textContent)).toEqual([
			"Faces: It wouldn't open.",
			"Watermarks: It wouldn't open."
		])
	);
	expect(host.querySelector('.more')).toBeNull();
});
