/* Which browser a link out of Sift opens in. Two of the three claims here are decided in the
 * markup, where no server test can see them. */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount } from 'svelte';
import LinksOpenIn from './LinksOpenIn.svelte';
import type { BrowserChoice } from '$lib/bridge';

const canChooseBrowser = vi.hoisted(() => vi.fn(() => true));
const browsers = vi.hoisted(() => vi.fn());
const setBrowser = vi.hoisted(() => vi.fn());

vi.mock('$lib/bridge', () => ({
	bridge: { canChooseBrowser, browsers, setBrowser }
}));

const TWO: BrowserChoice = {
	chosen: null,
	browsers: [
		{ id: 'firefox', name: 'Firefox' },
		{ id: 'chrome', name: 'Google Chrome' }
	]
};

let host: HTMLDivElement;

beforeEach(() => {
	canChooseBrowser.mockReturnValue(true);
	browsers.mockResolvedValue(TWO);
	setBrowser.mockResolvedValue({ ...TWO, chosen: null });
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	host.remove();
	vi.clearAllMocks();
});

async function draw() {
	mount(LinksOpenIn, { target: host });
	for (let turn = 0; turn < 6; turn += 1) await Promise.resolve();
	flushSync();
	return host;
}

it('draws nothing at all in a browser, and does not even ask', async () => {
	canChooseBrowser.mockReturnValue(false);

	await draw();

	expect(host.textContent?.trim()).toBe('');
	expect(browsers).not.toHaveBeenCalled();
});

it('draws nothing when the shell can answer but found no browser to offer', async () => {
	browsers.mockResolvedValue({ chosen: null, browsers: [] });

	await draw();

	expect(browsers).toHaveBeenCalled();
	expect(host.textContent?.trim()).toBe('');
});

it('draws the row once the shell says which browsers this machine has', async () => {
	await draw();

	expect(host.textContent).toContain('Links');
	expect(host.textContent).toContain('Open links in');
});

it('says the choice is Sift only, because it reads like a system setting and is not', async () => {
	await draw();

	expect(host.textContent).toContain('This changes it for Sift only.');
});
