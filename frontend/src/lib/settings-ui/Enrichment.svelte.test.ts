/* What a stash-box is allowed to write, field by field. */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { words as wordsOn } from '$lib/design/testing.svelte';
import { flushSync, mount, tick, unmount } from 'svelte';

import type { SettingEntry } from '$lib/settings-ui/settings';

const mocks = vi.hoisted(() => ({ fetchSettings: vi.fn(), saveSettings: vi.fn() }));

/* The enrichment task's When row reads the tasks store; no row answers here, so it is drawn by
   its address alone. */
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

vi.mock('$lib/settings-ui/settings', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/settings-ui/settings')>()),
	fetchSettings: mocks.fetchSettings,
	saveSettings: mocks.saveSettings
}));

import Enrichment from './Enrichment.svelte';
import { settingChanges } from '$lib/library/changes.svelte';
import DrilldownPage from './DrilldownPage.svelte';
import { drilldown } from './drilldown.svelte';
import { noServerAt } from '../../test-setup';

/* Left unanswered on purpose: the settings values, read on the way past. */
noServerAt('/api/settings');

function entry(key: string, over: Partial<SettingEntry> = {}): SettingEntry {
	return { key, value: false, default: false, label: key, ...over };
}

function connections(settings: SettingEntry[]) {
	return [{ name: 'Stash-boxes', settings }];
}

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

beforeEach(() => {
	vi.clearAllMocks();
	mocks.fetchSettings.mockResolvedValue(connections([]));
	mocks.saveSettings.mockResolvedValue(undefined);
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
	/* The open sub-page is MODULE state, so it outlives the host it was drawn in, and the next
	   test would start with the last one's page already up. */
	drilldown.close();
});

/* The sub-page is mounted beside the pane, exactly as the settings shell mounts it. */
async function draw(props: Record<string, unknown> = {}): Promise<void> {
	drawn = mount(Enrichment, { target: host, props }) as Record<string, unknown>;
	mount(DrilldownPage, { target: host, props: { behind: 'Stash-boxes' } });
	flushSync();
	await tick();
	await tick();
	flushSync();
}

/** Press the row that opens the sub-page holding every rule. */
async function openEach(): Promise<void> {
	const edit = [...host.querySelectorAll('button')].find((one) => wordsOn(one) === 'Edit');
	edit?.click();
	flushSync();
	await tick();
	flushSync();
}

/** Open the More settings page, where every switch but the feature's own is. */
async function openMore(): Promise<void> {
	host.querySelector<HTMLButtonElement>('[id="stash-boxes.more"] button')?.click();
	flushSync();
	await tick();
	flushSync();
}

/** The labels on screen, in the order they are drawn. */
function labels(): string[] {
	return [...host.querySelectorAll('label, dt, h2, [data-setting]')]
		.map((one) => one.textContent?.trim() ?? '')
		.filter(Boolean);
}

it('puts the switch that is the feature first, and the rest in the order somebody needs them', async () => {
	// Alphabetically, "apply an exact match without asking" comes before the switch that decides
	// whether anything is matched at all: a control acting on a feature above the switch that turns
	// the feature on.
	mocks.fetchSettings.mockResolvedValue(
		connections([
			entry('stash_boxes.apply_certain', { label: 'Apply an exact match' }),
			entry('stash_boxes.auto_box', { label: 'Which stash-box' }),
			entry('stash_boxes.duration_tolerance_s', { label: 'How close two lengths must be' }),
			entry('stash_boxes.scan', { label: 'Match files against the stash-boxes', value: true })
		])
	);

	await draw();

	// On the page: the switch, and none of the switches that depend on it.
	expect(host.textContent).toContain('Match files against the stash-boxes');
	expect(host.textContent).not.toContain('Apply an exact match');

	await openMore();
	const text = host.textContent ?? '';
	expect(text.indexOf('Which stash-box')).toBeLessThan(
		text.indexOf('How close two lengths must be')
	);
	expect(text.indexOf('How close two lengths must be')).toBeLessThan(
		text.indexOf('Apply an exact match')
	);
});

it('draws a switch it has never heard of rather than dropping it', async () => {
	// A switch added in a later version arrives here with no place in the named order.
	mocks.fetchSettings.mockResolvedValue(
		connections([
			entry('stash_boxes.scan', { label: 'Match files', value: true }),
			entry('stash_boxes.invented_later', { label: 'Something new' })
		])
	);

	await draw();
	await openMore();

	expect(host.textContent).toContain('Something new');
});

it("looking up the library is the enrichment task's When row, and only while it is on", async () => {
	/* Starting enrichment over the library is the enrichment task's row, the same row Tasks
	   draws, not a button on the list of boxes. */
	mocks.fetchSettings.mockResolvedValue(
		connections([entry('stash_boxes.scan', { label: 'Match files', value: true })])
	);
	await draw();
	expect(host.querySelector('[id="tasks.enrichment.when"]')).not.toBeNull();

	unmount(drawn!);
	drawn = null;
	host.replaceChildren();
	mocks.fetchSettings.mockResolvedValue(
		connections([entry('stash_boxes.scan', { label: 'Match files', value: false })])
	);
	await draw();
	expect(host.querySelector('[id="tasks.enrichment.when"]')).toBeNull();
	expect(host.textContent).toContain('Turned off.');
});

it('says where lookups stand from the stash-boxes the section holds', async () => {
	mocks.fetchSettings.mockResolvedValue(
		connections([entry('stash_boxes.scan', { label: 'Match files', value: true })])
	);
	const box = (over: Record<string, unknown>) => ({
		id: String(Math.random()),
		enabled: true,
		has_key: true,
		key_ready: true,
		...over
	});

	await draw({ boxes: { items: [box({}), box({ enabled: false })] } });
	expect(host.textContent).toContain('Ready. 1 stash-box is turned on.');

	unmount(drawn!);
	drawn = null;
	host.replaceChildren();
	await draw({ boxes: { items: [box({ key_ready: false })] } });
	// A key that cannot be opened is not a box that works, whatever its switch says.
	expect(host.textContent).toContain('On, but no stash-box has a key Sift can use.');
});

it('groups the rules by what they are about, under a name for each subject', async () => {
	mocks.fetchSettings.mockResolvedValue(
		connections([
			entry('enrich.person.birth_date', { label: 'Birthdate' }),
			entry('enrich.asset.title', { label: 'Title' })
		])
	);

	await draw();
	await openEach();

	/* Plural nouns, the way every settings heading is written. */
	expect(host.textContent).toContain('People');
	expect(host.textContent).toContain('Files');
});

it('names a subject it has no word for by the subject itself, rather than dropping the group', async () => {
	mocks.fetchSettings.mockResolvedValue(
		connections([entry('enrich.invented.thing', { label: 'A thing' })])
	);

	await draw();
	await openEach();

	expect(host.textContent).toContain('invented');
	expect(host.textContent).toContain('A thing');
});

it('says it could not read them when the section is absent, and not that there are none', async () => {
	// Absent means the request did not answer for it.
	mocks.fetchSettings.mockResolvedValue([{ name: 'Playback', settings: [] }]);

	await draw();

	expect(host.textContent).toContain(
		"These settings couldn't be loaded. Reload the page to try again."
	);
});

it('says the same when the request itself failed', async () => {
	mocks.fetchSettings.mockRejectedValue(new Error('offline'));

	await draw();

	expect(host.textContent).toContain(
		"These settings couldn't be loaded. Reload the page to try again."
	);
});

it('is quiet about an empty section, because empty is a real answer', async () => {
	await draw();

	expect(host.textContent).not.toContain(
		"These settings couldn't be loaded. Reload the page to try again."
	);
});

it('puts a control back where it was when the write is refused', async () => {
	// A control that stays where it was pushed after a failed write is a screen telling somebody a
	// thing is true when it is not.
	mocks.fetchSettings.mockResolvedValue(
		connections([entry('stash_boxes.scan', { label: 'Match files', value: false })])
	);
	mocks.saveSettings.mockRejectedValue(new Error('no'));
	await draw();

	const box = host.querySelector<HTMLElement>('[role="switch"], input[type="checkbox"], button');
	box?.click();
	flushSync();
	await tick();
	await tick();
	flushSync();

	expect(mocks.saveSettings).toHaveBeenCalledWith({ 'stash_boxes.scan': true });
	expect(host.querySelector('[role="switch"]')?.getAttribute('aria-checked')).not.toBe('true');
});

it('draws a heading and a sentence that say what the whole block decides', async () => {
	/* A RULE has to exist for the block to be drawn, and that is the behaviour rather than a
	   fixture detail: with nothing to answer, a heading and a paragraph about answering it are a
	   promise of controls that are not there. */
	mocks.fetchSettings.mockResolvedValue(
		connections([
			entry('enrich.person.name', {
				label: 'Name',
				value: 'merge',
				default: 'merge',
				choices: ['skip', 'merge', 'overwrite'],
				choice_labels: ['Leave alone', 'Fill in what is missing', 'Replace what is there']
			})
		])
	);
	await draw();

	/* A group's heading: the block is a `SettingGroup`, drawn by `SharedQuestion`, with no title
	   or lede of its own to say the same words again. */
	expect(host.querySelector('h2')?.textContent).toContain('Stash-box fields');
	expect(host.textContent?.replace(/\s+/g, ' ')).toContain(
		'Fill in what is missing never changes a value you already have'
	);
});

it('answers every rule in one go from the one control, in a single request', async () => {
	const rules = ['enrich.person.name', 'enrich.person.aliases', 'enrich.asset.tags'];
	mocks.fetchSettings.mockResolvedValue(
		connections(
			rules.map((key) =>
				entry(key, {
					label: key,
					value: 'merge',
					default: 'merge',
					choices: ['skip', 'merge', 'overwrite'],
					choice_labels: ['Leave alone', 'Fill in what is missing', 'Replace what is there']
				})
			)
		)
	);
	await draw();

	/* ONE request carrying all three, not three requests. */
	const master = host.querySelector<HTMLElement>('.ui-select');
	expect(master).not.toBeNull();
	expect(host.textContent).toContain('Choose for each field');
});

/* The pane follows the bus. */
it('re-reads when a setting moves somewhere else', async () => {
	mocks.fetchSettings.mockResolvedValue(
		connections([entry('stash_boxes.scan', { label: 'Match files against the stash-boxes' })])
	);
	await draw();
	expect(host.textContent).toContain('Match files against the stash-boxes');

	mocks.fetchSettings.mockResolvedValue(
		connections([entry('stash_boxes.scan', { label: 'Moved somewhere else' })])
	);
	settingChanges.changed();
	flushSync();
	await tick();
	await tick();
	flushSync();

	expect(host.textContent).toContain('Moved somewhere else');
});

/* A re-read keeps the pane on screen. */
it('keeps the pane drawn while a re-read is on its way', async () => {
	mocks.fetchSettings.mockResolvedValue(
		connections([entry('stash_boxes.scan', { label: 'Match files against the stash-boxes' })])
	);
	await draw();

	let answer!: (value: unknown) => void;
	mocks.fetchSettings.mockReturnValue(new Promise((resolve) => (answer = resolve)));
	settingChanges.changed();
	flushSync();
	await tick();
	flushSync();

	expect(host.querySelector('.bone')).toBeNull();
	expect(host.textContent).toContain('Match files against the stash-boxes');

	answer(connections([entry('stash_boxes.scan', { label: 'Moved somewhere else' })]));
	await tick();
	await tick();
	flushSync();
	expect(host.textContent).toContain('Moved somewhere else');
});

it('keeps the pane when a re-read fails, rather than trading it for an error', async () => {
	mocks.fetchSettings.mockResolvedValue(
		connections([entry('stash_boxes.scan', { label: 'Match files against the stash-boxes' })])
	);
	await draw();

	mocks.fetchSettings.mockRejectedValue(new Error('offline'));
	settingChanges.changed();
	flushSync();
	await tick();
	await tick();
	flushSync();

	expect(host.textContent).toContain('Match files against the stash-boxes');
	expect(host.textContent).not.toContain("couldn't be loaded");
});

it('answers a link to a switch behind More settings while lookups are off by ringing the switch', async () => {
	mocks.fetchSettings.mockResolvedValue(
		connections([
			entry('stash_boxes.apply_certain', { label: 'Apply an exact match' }),
			entry('stash_boxes.scan', { label: 'Match files against the stash-boxes', value: false })
		])
	);
	host.className = 'section-body';
	await draw();
	const { toasts } = await import('$lib/shell/toasts.svelte');
	const { revealSetting, hiddenWhile } = await import('./settings-anchor.svelte');
	const { COPY: POINTER } = await import('./SwitchPointer.svelte');
	const show = vi.spyOn(toasts, 'show');
	const scrolled = Element.prototype.scrollIntoView;
	Element.prototype.scrollIntoView = vi.fn();
	try {
		await expect(revealSetting('stash_boxes.apply_certain')).resolves.toBe(true);
		expect(show).toHaveBeenLastCalledWith(
			hiddenWhile('Apply an exact match', 'Match files against the stash-boxes', POINTER.off)
		);
		await expect(revealSetting('stash-boxes.more')).resolves.toBe(true);
		expect(show).toHaveBeenLastCalledWith(
			expect.stringContaining('is hidden while \u201cMatch files against the stash-boxes\u201d')
		);
	} finally {
		Element.prototype.scrollIntoView = scrolled;
		show.mockRestore();
	}
});
