/* Settings > Insights: the four recap switches drawn from the server's declarations in the period's
 * order, and the two rows that lead to the settings Insights leans on elsewhere. */

import { afterEach, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';

import { words } from '$lib/design/testing.svelte';

const mocks = vi.hoisted(() => ({ fetchSettings: vi.fn() }));

vi.mock('$lib/settings-ui/settings', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/settings-ui/settings')>()),
	fetchSettings: mocks.fetchSettings
}));

import Insights from './Insights.svelte';
import { COPY } from './Insights.search';

const PERIODS = ['year', 'day', 'month', 'week'];

let host: HTMLElement;
let drawn: Record<string, unknown> | undefined;

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = undefined;
	host?.remove();
});

it('draws a switch a period, day to year, whatever order the server declared them in', async () => {
	mocks.fetchSettings.mockResolvedValue([
		{
			name: 'Insights',
			settings: PERIODS.map((period) => ({
				key: `insights.recap_${period}`,
				value: period !== 'day',
				default: true,
				label: `Create a recap of each ${period}`,
				help: `Help for the ${period}.`
			}))
		}
	]);
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(Insights, { target: host });
	for (let turn = 0; turn < 5; turn += 1) await tick();
	flushSync();
	const switches = [...host.querySelectorAll<HTMLElement>('[role="switch"]')];
	expect(switches).toHaveLength(4);
	expect(switches.map((one) => one.getAttribute('aria-checked'))).toEqual([
		'false',
		'true',
		'true',
		'true'
	]);
	const text = words(host);
	const at = (label: string) => text.indexOf(label);
	expect(at('each day')).toBeLessThan(at('each week'));
	expect(at('each month')).toBeLessThan(at('each year'));
	/* The two settings Insights leans on, each a link to its own row. */
	const links = [...host.querySelectorAll('a')].map((link) => [
		words(link),
		link.getAttribute('href')
	]);
	expect(links).toEqual([
		[COPY.history.link, expect.stringContaining('/settings/privacy')],
		[COPY.pictures.link, expect.stringContaining('/settings/playback')]
	]);
	expect(links[0][1]).toContain('privacy.your_history');
	expect(links[1][1]).toContain('playback.screenshot_folder');
});
