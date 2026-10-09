/* The three Recognition switches in one block, for Importing's Identify page. */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';
import type { SettingEntry } from '$lib/settings-ui/settings';

const mocks = vi.hoisted(() => ({
	fetchSettings: vi.fn(),
	saveSettings: vi.fn(),
	faceSettings: vi.fn(),
	semanticStatus: vi.fn(),
	watermarkStatus: vi.fn(),
	refresh: vi.fn(async () => {})
}));

vi.mock('$lib/settings-ui/settings', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/settings-ui/settings')>()),
	fetchSettings: mocks.fetchSettings,
	saveSettings: mocks.saveSettings
}));

vi.mock('$lib/people/faces.svelte', () => ({
	FACES_ENABLED_KEY: 'faces.enabled',
	FACES_DEVICE_KEY: 'faces.device',
	faceSettings: mocks.faceSettings
}));

vi.mock('$lib/search/semantic.svelte', () => ({
	SEMANTIC_ENABLED_KEY: 'semantic.enabled',
	SEMANTIC_DEVICE_KEY: 'semantic.device',
	semanticStatus: mocks.semanticStatus
}));

vi.mock('$lib/jobs/semantic-runs.svelte', () => ({
	availability: { refresh: mocks.refresh }
}));

vi.mock('$lib/library/watermarks.svelte', () => ({
	WATERMARKS_ENABLED_KEY: 'watermarks.enabled',
	WATERMARKS_DEVICE_KEY: 'watermarks.device',
	watermarkStatus: mocks.watermarkStatus
}));

import RecognitionSection from './RecognitionSection.svelte';

const DEVICE = { choices: ['cpu', 'nvidia'], choice_labels: ['CPU', 'GPU'] };

function entry(key: string, over: Partial<SettingEntry> = {}): SettingEntry {
	return { key, value: false, default: false, label: key, ...over };
}

function settings(on: { faces?: boolean; semantic?: boolean; watermarks?: boolean } = {}) {
	return [
		{
			name: 'Identify',
			settings: [
				entry('faces.enabled', { label: 'Recognize faces in your library', value: !!on.faces }),
				entry('faces.device', { value: 'cpu', ...DEVICE })
			]
		},
		{
			name: 'Smart Search',
			settings: [
				entry('semantic.enabled', { label: 'Search by meaning', value: !!on.semantic }),
				entry('semantic.device', { value: 'cpu', ...DEVICE })
			]
		},
		{
			name: 'Watermarks',
			settings: [
				entry('watermarks.enabled', {
					label: 'Scan files for Site watermarks',
					value: !!on.watermarks
				}),
				entry('watermarks.device', { value: 'cpu', ...DEVICE })
			]
		}
	];
}

function faces(over: Record<string, unknown> = {}) {
	return {
		enabled: false,
		ready: true,
		device: 'cpu',
		device_problem: null,
		last_run_at: null,
		never_scanned: 0,
		scanned_under_older_rules: 0,
		...over
	};
}

function semantic(over: Record<string, unknown> = {}) {
	return {
		supported: true,
		enabled: false,
		ready: true,
		device: 'cpu',
		indexed_frames: 0,
		described_files: 0,
		waiting_files: 0,
		problem: null,
		...over
	};
}

function watermarks(over: Record<string, unknown> = {}) {
	return {
		enabled: false,
		ready: true,
		device: 'cpu',
		read_files: 0,
		marks_found: 0,
		waiting_files: 0,
		problem: null,
		...over
	};
}

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

beforeEach(() => {
	vi.clearAllMocks();
	mocks.saveSettings.mockResolvedValue(undefined);
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host.remove();
});

async function draw(
	on: { faces?: boolean; semantic?: boolean; watermarks?: boolean },
	states: {
		faces?: Record<string, unknown>;
		semantic?: Record<string, unknown>;
		watermarks?: Record<string, unknown>;
	} = {}
): Promise<void> {
	mocks.fetchSettings.mockResolvedValue(settings(on));
	mocks.faceSettings.mockResolvedValue(faces(states.faces));
	mocks.semanticStatus.mockResolvedValue(semantic(states.semantic));
	mocks.watermarkStatus.mockResolvedValue(watermarks(states.watermarks));
	drawn = mount(RecognitionSection, { target: host }) as Record<string, unknown>;
	await vi.waitFor(() => expect(mocks.watermarkStatus).toHaveBeenCalled(), { interval: 1 });
	for (let turn = 0; turn < 6; turn += 1) await tick();
	flushSync();
}

/** The switches and the boxes, in the order they are drawn: `switch:<key>` or `note:<line>`. */
function order(): string[] {
	return [...host.querySelectorAll('[role="switch"], .recognition-note .status')].map((one) =>
		one.matches('[role="switch"]')
			? `switch:${one.closest('.row')?.id ?? ''}`
			: `note:${one.textContent?.trim() ?? ''}`
	);
}

it('draws the three switches, each with where it stands in the box under it', async () => {
	await draw(
		{ faces: true, watermarks: true },
		{ faces: { never_scanned: 12 }, watermarks: { read_files: 4, marks_found: 1 } }
	);

	expect(order()).toEqual([
		'switch:faces.enabled',
		'note:Ready. Running on the CPU. 12 files not scanned for faces yet.',
		'switch:semantic.enabled',
		'note:Turned off.',
		'switch:watermarks.enabled',
		'note:Ready. Running on the CPU. 4 files scanned, 1 with a watermark. Every file is scanned.'
	]);
	expect(host.textContent).toContain('Recognition');
});

it('turning a switch on writes that switch and nothing else, then reads where it stands', async () => {
	await draw({});
	mocks.faceSettings.mockClear();

	host.querySelector<HTMLElement>('[id="faces.enabled"] [role="switch"]')?.click();
	await vi.waitFor(() => expect(mocks.saveSettings).toHaveBeenCalled(), { interval: 1 });

	// The switch alone: its task's When is not written, so one somebody chose is never moved.
	expect(mocks.saveSettings).toHaveBeenCalledTimes(1);
	expect(mocks.saveSettings).toHaveBeenCalledWith({ 'faces.enabled': true });
	await vi.waitFor(() => expect(mocks.faceSettings).toHaveBeenCalledTimes(1), { interval: 1 });
});

it('says where to download the models, with the way to that row on its own section', async () => {
	await draw({ semantic: true }, { semantic: { ready: false, problem: null } });

	const note = [...host.querySelectorAll('.recognition-note')].find((one) =>
		one.textContent?.includes("On, but the models aren't downloaded yet.")
	);
	const link = note?.querySelector('a');
	expect(note?.textContent).toContain('Download the models in');
	expect(link?.getAttribute('href')).toBe('/settings/semantic#semantic.download');
	expect(link?.textContent).toBe('Smart Search');
});

it('offers no Smart Search switch on a device that cannot hold the index, only why', async () => {
	await draw({}, { semantic: { supported: false, problem: 'This copy cannot load the add-on.' } });

	expect(host.querySelector('[id="semantic.enabled"]')).toBeNull();
	expect(host.textContent).toContain('This copy cannot load the add-on.');
});
