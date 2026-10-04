/* The Watermarks pane, on the one Recognition layout.
 *
 * Two promises, each one somebody relies on without reading it: where reading stands is one line
 * whose every number is the server's, and reading the library is the watermark task's own When
 * row, not a button here starting a second walk of the library for this feature alone.
 */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';
import type { SettingEntry } from '$lib/settings-ui/settings';

const mocks = vi.hoisted(() => ({
	fetchSettings: vi.fn(),
	saveSettings: vi.fn(),
	watermarkStatus: vi.fn()
}));

vi.mock('$lib/settings-ui/settings', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/settings-ui/settings')>()),
	fetchSettings: mocks.fetchSettings,
	saveSettings: mocks.saveSettings
}));

vi.mock('$lib/library/watermarks.svelte', () => ({
	WATERMARKS_ENABLED_KEY: 'watermarks.enabled',
	WATERMARKS_DEVICE_KEY: 'watermarks.device',
	watermarkStatus: mocks.watermarkStatus,
	fetchWatermarkModels: vi.fn(),
	forgetWatermarkReads: vi.fn()
}));

vi.mock('$lib/jobs/watermarks-runs.svelte', () => ({
	modelFetch: {
		running: false,
		fraction: 0,
		status: '',
		outcome: null,
		resume: async () => {},
		follow: () => {},
		couldNotStart: () => {}
	}
}));

vi.mock('$lib/jobs/tasks.svelte', () => ({
	taskList: {
		row: () => undefined,
		pressing: {},
		failed: false,
		ensure: async () => {},
		setWhen: async () => {}
	},
	pressTask: vi.fn(async () => true)
}));

import Watermarks from './Watermarks.svelte';

function entry(key: string, over: Partial<SettingEntry> = {}): SettingEntry {
	return { key, value: false, default: false, label: key, ...over };
}

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

beforeEach(() => {
	vi.clearAllMocks();
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host.remove();
});

async function draw(enabled: boolean, state: Record<string, unknown>): Promise<void> {
	mocks.fetchSettings.mockResolvedValue([
		{
			name: 'Watermarks',
			settings: [
				entry('watermarks.enabled', { label: 'Scan files for Site watermarks', value: enabled }),
				entry('watermarks.device', {
					label: 'Run watermark scans on',
					value: 'cpu',
					choices: ['cpu', 'nvidia'],
					choice_labels: ['CPU', 'GPU']
				})
			]
		}
	]);
	mocks.watermarkStatus.mockResolvedValue({
		enabled,
		ready: true,
		device: 'cpu',
		read_files: 0,
		marks_found: 0,
		waiting_files: 0,
		running_jobs: 0,
		problem: null,
		installed: [],
		...state
	});
	drawn = mount(Watermarks, { target: host }) as Record<string, unknown>;
	await vi.waitFor(() => expect(mocks.watermarkStatus).toHaveBeenCalled(), { interval: 1 });
	for (let turn = 0; turn < 6; turn += 1) await tick();
	flushSync();
}

function line(): string {
	return host.querySelector('.status')?.textContent?.trim() ?? '';
}

it('says where reading stands in one line, every number from the server', async () => {
	await draw(true, { read_files: 40, marks_found: 21, waiting_files: 500 });

	expect(line()).toBe(
		'Ready. Running on the CPU. 40 files scanned, 21 with a watermark. At least 500 not scanned yet.'
	);
});

it("reads the library through the task's own When row, with no button of its own", async () => {
	await draw(true, {});

	expect(host.querySelector('[id="tasks.watermarks.when"]')).not.toBeNull();
	const presses = [...host.querySelectorAll('button')].map((one) => one.textContent?.trim());
	expect(presses).not.toContain('Run now');
	expect(presses).not.toContain('Running\u2026');
});

it('says it is off as its status, and keeps what it found', async () => {
	await draw(false, { enabled: false, read_files: 3 });

	expect(line()).toBe('Turned off. The watermark results are kept.');
	expect(host.querySelector('[id="tasks.watermarks.when"]')).toBeNull();
	// The way out stays: switching it off is not deleting what it found.
	expect(host.querySelector('[id="watermarks.forget"]')).not.toBeNull();
});

it('answers a link to the way out before anything is scanned by saying why it is not there', async () => {
	host.className = 'section-body';
	await draw(true, { read_files: 0 });
	expect(host.querySelector('[id="watermarks.forget"]')).toBeNull();
	const { toasts } = await import('$lib/shell/toasts.svelte');
	const { revealSetting, NOTHING_TO_DELETE } = await import('./settings-anchor.svelte');
	const said = vi.spyOn(toasts, 'show');
	const scrolled = Element.prototype.scrollIntoView;
	Element.prototype.scrollIntoView = vi.fn();
	try {
		await expect(revealSetting('watermarks.forget')).resolves.toBe(true);
		expect(said).toHaveBeenCalledWith(NOTHING_TO_DELETE);
	} finally {
		Element.prototype.scrollIntoView = scrolled;
		said.mockRestore();
	}
});
