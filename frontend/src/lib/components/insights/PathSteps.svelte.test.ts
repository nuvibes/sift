/* A Get to know Sift step, pressed.
 *
 * The steps are drawn inside the Settings panel on Get to know Sift. A step whose place is in
 * Settings (Add your library is `/settings/library`) followed as a page load would change only
 * the route's parameters there, so the panel would fade out and leave the screen empty. It opens
 * the panel at that section instead; a step anywhere else stays an ordinary link.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import PathSteps from './PathSteps.svelte';
import type { PathGoal } from './your-path';

const opened = vi.hoisted(() => vi.fn((event: MouseEvent) => event.preventDefault()));
vi.mock('$lib/settings-ui/settings-view', () => ({ openSettingsInstead: opened }));

let host: HTMLElement;
let drawn: ReturnType<typeof mount> | null = null;

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
	opened.mockClear();
});

function goal(id: string, title: string, href: string): PathGoal {
	return { id, title, href, done: false, done_at: null, help: [] };
}

function pressed(title: string): void {
	const link = [...host.querySelectorAll('a')].find((one) => one.textContent?.trim() === title);
	if (!link) throw new Error(`no step called ${title}`);
	link.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 }));
}

describe('a Get to know Sift step', () => {
	it('opens the Settings panel at its section rather than loading the address', () => {
		host = document.createElement('div');
		document.body.append(host);
		drawn = mount(PathSteps, {
			target: host,
			props: {
				steps: [
					goal('add_library', 'Add your library', '/settings/library'),
					goal('sites', 'Open Sites', '/sites')
				]
			}
		});
		flushSync();

		pressed('Add your library');
		expect(opened).toHaveBeenCalledTimes(1);
		expect(opened.mock.calls[0].slice(1)).toEqual(['library', undefined, undefined]);

		pressed('Open Sites');
		expect(opened).toHaveBeenCalledTimes(1);
	});
});
