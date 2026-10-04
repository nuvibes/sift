import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import DownloadTools from './DownloadTools.svelte';

/* The download tools on Settings > Updates. What is pinned: one row per tool with the version the
 * server read, the copy Sift ships told apart from the machine's own, the engine's "none found" said
 * as that rather than as a version, and the check for a newer yt-dlp happening ONLY when pressed. */

type Tools = components['schemas']['DownloadTools'];

const SHIPPED: Tools = {
	tools: [
		{ key: 'ffmpeg', name: 'ffmpeg', version: 'n7.1-12-g0123456789-20260801', shipped: true },
		{ key: 'yt-dlp', name: 'yt-dlp', version: '2026.08.19', shipped: true },
		{ key: 'gallery-dl', name: 'gallery-dl', version: '1.32.13', shipped: true },
		{ key: 'js-runtime', name: 'QuickJS-NG', version: '0.17.0', shipped: true }
	]
};

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

async function show(tools: Tools = SHIPPED) {
	const answer = Promise.resolve(tools);
	vi.spyOn(api, 'get').mockReturnValue(answer as never);
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(DownloadTools, { target: host });
	flushSync();
	await answer;
	await Promise.resolve();
	flushSync();
}

function text(): string {
	return host.textContent ?? '';
}

function checkButton(): HTMLButtonElement {
	const button = host.querySelector<HTMLButtonElement>(
		'[id="updates.download_tools.yt-dlp"] button[aria-label="Check for a newer yt-dlp"]'
	);
	if (!button) throw new Error('no check button');
	return button;
}

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	vi.restoreAllMocks();
	host?.remove();
});

describe('the rows', () => {
	it('names every tool and the version it gave', async () => {
		await show();

		for (const version of ['n7.1-12-g0123456789-20260801', '2026.08.19', '1.32.13']) {
			expect(text()).toContain(version);
		}
		expect(text()).toContain('JavaScript engine');
		expect(text()).toContain('QuickJS-NG 0.17.0');
		expect(text()).toContain('Shipped with Sift.');
	});

	it('says when a tool is the copy on the machine and not the copy Sift ships', async () => {
		await show({
			tools: [{ key: 'yt-dlp', name: 'yt-dlp', version: '2026.03.17', shipped: false }]
		});

		expect(text()).toContain('own copy');
		expect(text()).not.toContain('Shipped with Sift.');
	});

	it('says an engine yt-dlp could not find is not there, rather than showing a version', async () => {
		await show({
			tools: [{ key: 'js-runtime', name: 'none', version: null, shipped: false }]
		});

		expect(text()).toContain('None found');
	});
});

describe("yt-dlp's version", () => {
	it('stands in the column every tool version stands in, with the check a row of its own', async () => {
		await show();
		const version = [...host.querySelectorAll('.fact')].find(
			(one) => one.textContent === '2026.08.19'
		);
		expect(version, 'the version left the facts column').toBeDefined();
		const check = host.querySelector('[id="updates.download_tools.yt-dlp"]');
		expect(check?.querySelector('.note'), 'the version is stacked on the press again').toBeNull();
		expect(check?.textContent).not.toContain('2026.08.19');
	});
});

describe('the check for a newer yt-dlp', () => {
	it('is never made by opening the pane', async () => {
		const post = vi.spyOn(api, 'post');
		await show();

		expect(post).not.toHaveBeenCalled();
	});

	it('says a newer release is out when pressed, and names it', async () => {
		const answer = Promise.resolve({
			key: 'yt-dlp',
			running: '2026.08.19',
			latest: '2026.09.20',
			newer: true
		});
		const post = vi.spyOn(api, 'post').mockReturnValue(answer as never);
		await show();

		checkButton().click();
		await answer;
		await Promise.resolve();
		flushSync();

		expect(post).toHaveBeenCalledWith('/download-tools/latest', { body: { tool: 'yt-dlp' } });
		expect(host.querySelector('[role="status"]')?.textContent).toContain('2026.09.20 is out');
	});

	it('says so plainly when this is already the newest', async () => {
		const answer = Promise.resolve({
			key: 'yt-dlp',
			running: '2026.08.19',
			latest: '2026.08.19',
			newer: false
		});
		vi.spyOn(api, 'post').mockReturnValue(answer as never);
		await show();

		checkButton().click();
		await answer;
		await Promise.resolve();
		flushSync();

		expect(host.querySelector('[role="status"]')?.textContent).toContain('the newest yt-dlp');
	});
});
