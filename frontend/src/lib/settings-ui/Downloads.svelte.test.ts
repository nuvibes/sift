import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { SettingSection } from '$lib/settings-ui/settings';
import { words } from '$lib/design/testing.svelte';
import Downloads from './Downloads.svelte';
import DrilldownPage from './DrilldownPage.svelte';
import { drilldown } from './drilldown.svelte';
import { noServerAt } from '../../test-setup';

/* Left unanswered on purpose: the naming preview, which this file does not draw. */
noServerAt('/api/site-options/preview');

/* Settings, Downloads: what is set ONCE about downloading.
 *
 * Three things are guarded. The groups a person comes for are on the pane, in their order. The
 * knobs nobody should be invited to turn (downloads at the same time, the speed limit, the pacing,
 * the timeout, the retries and the back-off) are NOT on the pane but one row away, on the More
 * settings page, and a deep link to one of them opens that page first. And a setting registered
 * later that nothing here names still lands somewhere (the More settings page) rather than on no
 * screen at all.
 */

const fetchSettings = vi.fn<() => Promise<SettingSection[]>>();

vi.mock('$lib/settings-ui/settings', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/settings-ui/settings')>()),
	fetchSettings: () => fetchSettings(),
	saveSettings: vi.fn(async () => undefined),
	onSettingsSaved: vi.fn()
}));

vi.mock('$lib/api/client', async (importOriginal) => {
	const actual = await importOriginal<typeof import('$lib/api/client')>();
	return {
		...actual,
		api: { ...actual.api, get: vi.fn(async () => Promise.reject(new Error('not here'))) }
	};
});

function row(key: string, label: string, value: unknown = true) {
	return { key, label, value, default: value, kind: 'bool', help: '' };
}

const DECLARED = [
	row('download.quality', 'Video quality', 'compatible'),
	row('download.remember', 'Skip links you have already downloaded'),
	row('download.people_from_usernames', 'Add creators to People'),
	row('download.skip_smaller_mb', 'Skip files smaller than', 0),
	row('download.skip_larger_mb', 'Skip files larger than', 0),
	row('download.min_free_gb', 'Minimum free space', 5),
	row('download.finished_message', 'Say when a download finishes', 'off'),
	row('download.sound', 'Play a sound when finished', false),
	row('download.sound_volume', 'Sound volume', 40),
	row('download.at_once', 'Downloads at the same time', 0),
	row('download.bandwidth_kbps', 'Download speed limit', 0),
	row('download.pace_ms', 'Wait between requests', 500),
	row('download.timeout_seconds', 'Connection timeout', 30),
	row('download.retries', 'Retries per download', 3),
	row('download.backoff_seconds', 'Wait after a rate limit', 60),
	row('download.paused', 'Pause downloads', false),
	// Registered later and named nowhere in the pane: it must still land on a screen.
	row('download.someday', 'A setting nobody placed', false)
];

let host: HTMLElement;
let drawn: ReturnType<typeof mount>[] = [];

beforeEach(() => {
	fetchSettings.mockResolvedValue([
		{ name: 'Downloads', settings: DECLARED } as unknown as SettingSection
	]);
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	for (const one of drawn) unmount(one);
	drawn = [];
	drilldown.close();
	host.remove();
	document.body.innerHTML = '';
});

async function draw() {
	drawn.push(mount(Downloads, { target: host }));
	drawn.push(mount(DrilldownPage, { target: host, props: { behind: 'Downloads' } }));
	flushSync();
	await vi.waitFor(() => expect(words(host)).toContain('Video quality'));
}

const headings = () => [...host.querySelectorAll('h2, h3')].map((one) => words(one));

it('draws what is set once, grouped as somebody reaches for it', async () => {
	await draw();
	expect(headings()).toEqual([
		'What gets downloaded',
		'Limits',
		'When a download finishes',
		'Folders and file names'
	]);
	for (const label of [
		'Skip links you have already downloaded',
		'Minimum free space',
		'Say when a download finishes'
	]) {
		expect(words(host)).toContain(label);
	}
});

it('keeps the expert knobs one row away, on the More settings page', async () => {
	await draw();
	for (const label of ['Downloads together', 'Wait between requests', 'Retries per download']) {
		expect(words(host)).not.toContain(label);
	}
	// And never the pause, which is the queue's own control on the Downloads screen.
	expect(words(host)).not.toContain('Pause downloads');

	const more = [...host.querySelectorAll('button')].find((one) => words(one) === 'Edit');
	expect(more, 'no way in to More settings').toBeTruthy();
	more?.click();
	flushSync();

	expect(drilldown.title).toBe('More settings');
	for (const label of [
		'Downloads at the same time',
		'Download speed limit',
		'Wait between requests',
		'Connection timeout',
		'Retries per download',
		'Wait after a rate limit',
		'A setting nobody placed'
	]) {
		expect(words(host)).toContain(label);
	}
});

it('puts More settings last, below where downloads go and what they are called', async () => {
	await draw();
	const naming = [...host.querySelectorAll('h2, h3')].find(
		(one) => words(one) === 'Folders and file names'
	);
	const more = [...host.querySelectorAll('button')].find((one) => words(one) === 'Edit');
	expect(naming && more).toBeTruthy();
	expect(naming!.compareDocumentPosition(more!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

it('opens the More settings page for a deep link to one of its rows', async () => {
	await draw();
	expect(drilldown.reveal('download.retries')).toBe(true);
	expect(drilldown.title).toBe('More settings');
	// Including one nothing here names, which is claimed with the rest.
	drilldown.close();
	expect(drilldown.reveal('download.someday')).toBe(true);
});
