/* Import tasks: what happens to a file as it arrives, and what is missing from the files here.
 *
 * ## What is worth pinning here
 *
 * The switches are drawn from the registry and are proved where the registry is. What this file
 * owns is the ARITHMETIC a person reads before pressing something that runs for a day, and the
 * arithmetic has one trap in it.
 *
 * **A file is one file however many things it lacks.** The sheet answers a count PER PRODUCT
 * (16,000 thumbnails, 6,500 hover previews), and adding those up is the number of pictures to
 * make, not the number of files to read: a library of nine thousand would be told it had "22,500
 * files", which is not a rounding error but a different question answered in the same words. So
 * the row beside the button names each product on its own and never totals them, and the sentence
 * after the press repeats the count the SERVER worked out, which is the union.
 *
 * ## And the row it is drawn on
 *
 * Each stage is its task's row on Tasks: its Edit before Run now, and what is missing after the
 * task's own facts on the foot line.
 */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { words as wordsOn } from '$lib/design/testing.svelte';
import { flushSync, mount, tick, unmount } from 'svelte';

import type { SettingEntry } from '$lib/settings-ui/settings';
import type { BuildRow, BuildSheet } from '$lib/library/importing';
import type { TaskView } from '$lib/jobs/tasks.svelte';

const mocks = vi.hoisted(() => ({
	fetchSettings: vi.fn(),
	saveSettings: vi.fn(),
	fetchBuildSheet: vi.fn(),
	retryBuild: vi.fn(),
	fetchFolderAnswers: vi.fn(),
	get: vi.fn(),
	post: vi.fn(),
	show: vi.fn(),
	showSection: vi.fn()
}));

vi.mock('$lib/settings-ui/settings-view', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/settings-ui/settings-view')>()),
	showSettingsSection: mocks.showSection
}));

vi.mock('$lib/settings-ui/settings', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/settings-ui/settings')>()),
	fetchSettings: mocks.fetchSettings,
	saveSettings: mocks.saveSettings
}));

vi.mock('$lib/library/importing', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/library/importing')>()),
	fetchBuildSheet: mocks.fetchBuildSheet,
	retryBuild: mocks.retryBuild,
	fetchFolderAnswers: mocks.fetchFolderAnswers
}));

/* The task list the stages' rows are drawn from. Through the client rather than a mocked store, so
   the rows are the real component reading the real store. */
vi.mock('$lib/api/client', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/client')>()),
	api: { get: mocks.get, post: mocks.post }
}));

vi.mock('$lib/shell/toasts.svelte', () => ({ toasts: { show: mocks.show } }));

/* The three recognition switches on Identify's page are their own component, proved beside it;
   here it stands in as nothing, so its status reads do not need answering. */
vi.mock('./RecognitionSection.svelte', () => ({ default: () => {} }));

import Importing from './Importing.svelte';
import DrilldownPage from './DrilldownPage.svelte';
import { drilldown } from './drilldown.svelte';

function row(key: string, label: string, over: Partial<BuildRow> = {}): BuildRow {
	return {
		key,
		label,
		help: label,
		switched_on: true,
		files: 0,
		seconds_per_file: null,
		quick_seconds: null,
		slow_seconds: null,
		jobs_at_once: null,
		cannot: 0,
		...over
	};
}

function sheet(over: Partial<BuildSheet> = {}): BuildSheet {
	return {
		rows: [],
		files: 0,
		identifying: 0,
		unread: 0,
		running: false,
		night_start: '23:00',
		measure_first: false,
		...over
	};
}

function setting(key: string, over: Partial<SettingEntry> = {}): SettingEntry {
	return { key, value: true, default: true, label: key, ...over };
}

/** One task row, as `/api/tasks` sends it. */
function taskRow(id: string, over: Partial<TaskView> = {}): TaskView {
	return {
		id,
		title: `Task ${id}`,
		unit: 'file',
		units: 'files',
		parts: [],
		locations: false,
		dry: false,
		dry_run: null,
		dry_running: false,
		explain: `What ${id} does.`,
		when: 'quiet',
		when_key: `tasks.${id}.when`,
		whens: [
			{ value: 'work', label: 'As soon as there is work' },
			{ value: 'quiet', label: 'In quiet hours' },
			{ value: 'press', label: 'Only when I press it' }
		],
		on: true,
		cadence: 'In quiet hours',
		setting_keys: [],
		drawn_keys: [],
		set_in: id === 'smart-search' ? 'semantic' : id,
		last: null,
		next_run: null,
		waiting: 0,
		held: 0,
		running: false,
		press: 'Run now',
		reads: [],
		off: null,
		...over
	};
}

const TASKS = [
	'scan',
	'generate',
	'identify',
	'suggestions',
	'duplicates',
	'shoots',
	'music',
	'faces',
	'smart-search',
	'watermarks'
];

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

beforeEach(() => {
	vi.clearAllMocks();
	mocks.fetchSettings.mockResolvedValue([
		{ name: 'Importing', settings: [setting('photo_sets.from_folders')] }
	]);
	mocks.saveSettings.mockResolvedValue(undefined);
	mocks.fetchBuildSheet.mockResolvedValue(sheet());
	mocks.retryBuild.mockResolvedValue({ forgotten: 0 });
	mocks.fetchFolderAnswers.mockResolvedValue({ folders: [] });
	mocks.get.mockResolvedValue({
		quiet_hours: { starts: '23:00', ends: '07:00', open: false, opens_at: 0, closes_at: null },
		keep_awake: true,
		awake_now: false,
		tasks: TASKS.map((id) => taskRow(id))
	});
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
	/* The open sub-page is MODULE state and outlives the host it was drawn in, so the next test
	   would start with the last one's page already up. */
	drilldown.close();
});

async function draw(): Promise<void> {
	drawn = mount(Importing, { target: host }) as Record<string, unknown>;
	flushSync();
	// One tick per awaited request in `load`, and one more for the render that follows the last of
	// them. A tick short leaves the pane drawn from nothing, which reads as a component that ignores
	// its data, so this count moves whenever `load` gains a call.
	await tick();
	await tick();
	await tick();
	flushSync();
}

/* Found by the id each stage's row carries (its task's When) rather than by its words, so a change
   of wording is not a change of what is being tested. */
function rowOf(id: string): HTMLElement {
	const row = host.querySelector<HTMLElement>(`[id="${id}"]`);
	if (row === null) throw new Error(`no row called ${id}`);
	return row;
}

const stageRow = (stage: string): HTMLElement => rowOf(`tasks.${stage}.when`);

/** What one stage says is still missing, on its row's foot line. Empty when it says nothing. */
async function noteOf(stage: string): Promise<string> {
	await vi.waitFor(() => stageRow(stage).querySelector('[data-fact="when"]'));
	return host.querySelector(`[data-count="${stage}"]`)?.textContent ?? '';
}

const GENERATE = 'generate';
const IDENTIFY = 'identify';

it('names each thing that is missing, and never adds them into a count of files', async () => {
	/* THE FAULT THIS GUARDS. Two products short on the same nine thousand files, added together
	   and worded as files, would tell a library of 9,000 that it had 22,500. */
	mocks.fetchBuildSheet.mockResolvedValue(
		sheet({
			files: 9000,
			rows: [
				row('thumbnails', 'Thumbnails', { files: 16000 }),
				row('previews', 'Hover previews', { files: 6500 })
			]
		})
	);

	await draw();

	const note = await noteOf(GENERATE);
	expect(note).toContain('16,000 thumbnails');
	expect(note).toContain('6,500 hover previews');
	expect(note).not.toContain('22,500');
});

it("says what could not be generated on the stage row's own foot line, never loose between rows", async () => {
	/* A sentence dropped between two rows would leave the next row with no line above it; as the row's
	   foot it sits under the row's help, inside the row, and the line rule holds. */
	mocks.fetchBuildSheet.mockResolvedValue(
		sheet({ files: 40, rows: [row('thumbnails', 'Thumbnails', { files: 0, cannot: 2 })] })
	);

	await draw();

	// And the count says nothing: "Nothing missing" beside two files that could not be made
	// would contradict the line after it.
	expect(await noteOf(GENERATE)).toBe('');
	const foot = stageRow('generate').querySelector('.foot');
	expect(foot?.textContent).toContain('2');
	expect(foot?.textContent).toContain('thumbnails');
	expect(document.querySelector('.gave-up')).toBeNull();
});

it('agrees with the number in front of it when there is only one', async () => {
	/* The server declares each product's label once and in the plural, because that is how it reads
	   everywhere else it is drawn; this is the one place a count goes in front of it. Left alone it
	   would say "1 thumbnails". "Meaning" is here because it is the label with no "s" to take
	   off, which is the case a rule about trailing letters gets wrong if it is written carelessly. */
	mocks.fetchBuildSheet.mockResolvedValue(
		sheet({
			rows: [
				row('thumbnails', 'Thumbnails', { files: 1 }),
				row('previews', 'Hover previews', { files: 1 }),
				row('sprites', 'Sprites', { files: 2 })
			]
		})
	);

	await draw();

	const note = await noteOf(GENERATE);
	expect(note).toContain('1 thumbnail,');
	expect(note).toContain('1 hover preview,');
	expect(note).toContain('2 sprites');
	expect(note).not.toContain('1 thumbnails');
});

it('leaves a label with nothing to take off exactly as it is', async () => {
	mocks.fetchBuildSheet.mockResolvedValue(
		sheet({ rows: [row('meaning', 'Meaning', { files: 1 })] })
	);

	await draw();

	expect(await noteOf(IDENTIFY)).toContain('1 meaning');
});

it('says the WAIT, and what the wait assumes about the machine', async () => {
	/* THE WALL TIME, NOT THE WORKER TIME. Worker-seconds (how hard the machine works per file)
	   presented as how long somebody would be waiting differ by however many jobs run at
	   once: faces at about four worker-seconds over 90,000 files would read as "4 days" for a run
	   that gets through the same library in less than a day. The server sends wall seconds, and
	   the number of jobs the measured run had travels with them, because that is what the
	   estimate assumes it gets. */
	mocks.fetchBuildSheet.mockResolvedValue(
		sheet({
			rows: [
				row('faces', 'Faces', {
					files: 90000,
					quick_seconds: 70000,
					slow_seconds: 85000,
					jobs_at_once: 8
				})
			]
		})
	);

	await draw();

	const note = await noteOf(IDENTIFY);
	expect(note).toContain('90,000 faces');
	expect(note).toContain('about 18 to 24 hours');
	expect(note).toContain('with 8 tasks at the same time');
	// What the worker-seconds arithmetic would say, and a reason nobody would press it.
	expect(note).not.toContain('days');
});

it('and claims nothing about the machine when the run never recorded it', async () => {
	/* A run from before the number was kept. The estimate is still the honest one; the clause that
	   says what it assumes is simply not there, because "with null tasks at the same time" is worse
	   than a sentence that stops. */
	mocks.fetchBuildSheet.mockResolvedValue(
		sheet({
			rows: [row('faces', 'Faces', { files: 100, quick_seconds: 600, slow_seconds: 600 })]
		})
	);

	await draw();

	const note = await noteOf(IDENTIFY);
	expect(note).toContain('about 10 minutes');
	expect(note).not.toContain('tasks at the same time');
});

it("says a stage's window as the sum of each row's cheapest and dearest runs", async () => {
	/* A run over photographs and a run over videos price hover previews far apart, so each row is
	   a window and the stage's is their sum: 15 to 60 minutes, never the newest run's one figure. */
	mocks.fetchBuildSheet.mockResolvedValue(
		sheet({
			rows: [
				row('thumbnails', 'Thumbnails', { files: 40, quick_seconds: 300, slow_seconds: 1800 }),
				row('previews', 'Hover previews', { files: 40, quick_seconds: 600, slow_seconds: 1800 })
			]
		})
	);
	await draw();
	expect(await noteOf(GENERATE)).toContain('about 15 minutes to an hour');
});

it('says it cannot tell the time yet while one of its rows has no window', async () => {
	mocks.fetchBuildSheet.mockResolvedValue(
		sheet({
			rows: [
				row('thumbnails', 'Thumbnails', { files: 40 }),
				row('previews', 'Hover previews', { files: 40, quick_seconds: 600, slow_seconds: 1800 })
			]
		})
	);
	await draw();
	expect(await noteOf(GENERATE)).toContain('40 hover previews');
	expect(await noteOf(GENERATE)).not.toContain('about');
	/* Below the sample the time is said as not known yet, never dropped without a word. */
	expect(await noteOf(GENERATE)).toContain('Not enough to say yet how long that takes.');
});

it("names each stage's Edit by its stage, so they are not all just Edit", async () => {
	await draw();
	await vi.waitFor(() => expect(stageRow('identify').querySelector('.facts')).not.toBeNull());

	const names = [...host.querySelectorAll('button')]
		.filter((one) => wordsOn(one) === 'Edit')
		.map((one) => one.getAttribute('aria-label'));
	expect(names).toEqual(['Edit Scan', 'Edit Generate', 'Edit Identify']);
});

it('says so plainly when a stage has nothing left to do', async () => {
	mocks.fetchBuildSheet.mockResolvedValue(
		sheet({ rows: [row('faces', 'Faces'), row('meaning', 'Meaning')] })
	);

	await draw();

	expect(await noteOf(IDENTIFY)).toContain('Nothing missing');
});

it("draws each stage as its task's row, its Edit before Run now and the When said on the foot", async () => {
	/* One place for a stage: what it does behind Edit, starting it and when it runs beside. */
	await draw();
	for (const stage of ['scan', 'generate', 'identify']) {
		const when = await vi.waitFor(() => {
			const found = stageRow(stage).querySelector('[data-fact="when"]');
			expect(found, stage).not.toBeNull();
			return found!;
		});
		expect(when.textContent?.trim()).toBe('In quiet hours (11 PM to 7 AM)');
		const presses = [...stageRow(stage).querySelectorAll('.when button')].map((one) =>
			wordsOn(one)
		);
		expect(presses.slice(0, 2), stage).toEqual(['Edit', 'Run now']);
		expect(stageRow(stage).querySelector(`[id="importing.${stage}-settings"]`)).not.toBeNull();
	}
	expect(host.querySelector('[id="tasks.stages"]')).not.toBeNull();
	expect(host.textContent).not.toContain('chosen on');
	expect(host.textContent).not.toMatch(/tonight|overnight|nightly/i);
});

it("says Identify's When as Mixed while its three tasks disagree", async () => {
	mocks.get.mockResolvedValue({
		quiet_hours: { starts: '23:00', ends: '07:00', open: false, opens_at: 0, closes_at: null },
		keep_awake: true,
		awake_now: false,
		tasks: TASKS.map((id) => taskRow(id, id === 'identify' ? { when: 'mixed' } : {}))
	});
	const { taskList } = await import('$lib/jobs/tasks.svelte');
	await taskList.load();
	await draw();
	await vi.waitFor(() =>
		expect(stageRow('identify').querySelector('[data-fact="when"]')?.textContent).toBe('Mixed')
	);
});

it('leaves quiet hours to Tasks and how much at the same time to Performance', async () => {
	await draw();
	await vi.waitFor(() =>
		expect(stageRow('scan').querySelector('[data-fact="when"]')).not.toBeNull()
	);

	for (const gone of ['importing.quiet-hours', 'importing.at-once', 'importing.limits']) {
		expect(host.querySelector(`[id="${gone}"]`), gone).toBeNull();
	}
	expect(host.textContent).not.toContain('Concurrency');
	expect(host.textContent).not.toContain('Quiet hours');
	/* Nothing here claims their rows, so a link to one goes where it is drawn. */
	for (const key of [
		'performance.worker_count',
		'faces.machine_budget',
		'tasks.quiet_from',
		'tasks.duplicates.when',
		'tasks.faces.when'
	]) {
		expect(drilldown.reveal(key), key).toBe(false);
	}
});

it('leaves the pane as it was when a read fails, rather than blanking every count', async () => {
	/* A pane that emptied itself over one dropped request would hide what it exists to show. */
	mocks.fetchBuildSheet.mockResolvedValue(
		sheet({ rows: [row('thumbnails', 'Thumbnails', { files: 5 })] })
	);
	await draw();
	expect(await noteOf(GENERATE)).toContain('5 thumbnails');

	mocks.fetchSettings.mockRejectedValue(new Error('unreachable'));
	mocks.fetchBuildSheet.mockRejectedValue(new Error('unreachable'));
	const { settingChanges } = await import('$lib/library/changes.svelte');
	settingChanges.changed();
	flushSync();
	await tick();

	expect(await noteOf(GENERATE)).toContain('5 thumbnails');
});

/* The per-stage switches are not on the pane: each stage holds its task row and an Edit, and the
 * rows are drawn by the settings SHELL on a sub-page. So a test about a row mounts the shell's page
 * beside the pane and opens it, by the key, which is the same door a deep link uses. */
async function drawAndOpen(key: string): Promise<void> {
	await draw();
	mount(DrilldownPage, { target: host, props: { behind: 'Importing' } });
	expect(drilldown.reveal(key), `nothing on this pane owns ${key}`).toBe(true);
	flushSync();
	await tick();
	flushSync();
}

it('files "Create Photo Sets from shoots" on the Scan page beside its sisters', async () => {
	mocks.fetchSettings.mockResolvedValue([
		{
			name: 'Importing',
			settings: [setting('photo_sets.from_folders'), setting('shoots.auto_file', { value: false })]
		}
	]);
	await drawAndOpen('shoots.auto_file');
	expect(host.querySelector('[id="shoots.auto_file"]')).not.toBeNull();
	expect(host.querySelector('[id="photo_sets.from_folders"]')).not.toBeNull();
});

it("opens Identify's page for a link to one of the three recognition switches", async () => {
	/* Faces, Smart Search and Watermarks each point at their switch here ("Change in Importing").
	   The switches sit on Identify's page, so the pane has to claim their keys for that page or
	   the link opens Importing at its top and rings nothing. */
	const { RECOGNITION_SWITCHES } = await import('./recognition-switches');
	expect([...RECOGNITION_SWITCHES]).toEqual([
		'faces.enabled',
		'semantic.enabled',
		'watermarks.enabled'
	]);
	await draw();
	for (const key of RECOGNITION_SWITCHES) {
		expect(drilldown.reveal(key), `nothing on this pane owns ${key}`).toBe(true);
		expect(drilldown.title, key).toBe('Identify settings');
		drilldown.close();
	}
});

it("gives each stage its task's own line, the one Tasks draws under the same task", async () => {
	/* One task, one sentence: the stage on Importing and the row on Tasks are two doors to it. */
	await draw();
	for (const stage of ['scan', 'generate', 'identify']) {
		await vi.waitFor(() => expect(stageRow(stage).textContent).toContain(`What ${stage} does.`));
	}
});

it('lets a sentence about files left out wrap, where the facts beside it keep to one line', async () => {
	/* On a phone a sentence kept to one line would run under the Edit button and off the pane,
	   and its Try again could not be reached. */
	const { applyStyles, removeStyles } = await import('$lib/design/testing-styles');
	const { default: taskWhen } = await import('./TaskWhen.svelte?raw');
	mocks.fetchBuildSheet.mockResolvedValue(
		sheet({ files: 40, rows: [row('thumbnails', 'Thumbnails', { files: 0, cannot: 22 })] })
	);
	await draw();
	const facts = await vi.waitFor(() => {
		const found = stageRow('generate').querySelector<HTMLElement>('.facts');
		expect(found?.textContent).toContain('left out');
		return found!;
	});
	applyStyles(taskWhen, facts);
	const sentence = [...facts.children].find((one) => one.textContent?.includes('left out'));
	expect(sentence?.classList.contains('sentence')).toBe(true);
	expect(getComputedStyle(sentence!).whiteSpace).not.toBe('nowrap');
	removeStyles();
});

it('keeps Try again on the line with the last word of its sentence', async () => {
	/* Wrapped alone under the sentence, the press reads as a stray line of help rather than as
	   something to press. */
	const { applyStyles, removeStyles } = await import('$lib/design/testing-styles');
	const { default: importing } = await import('./Importing.svelte?raw');
	mocks.fetchBuildSheet.mockResolvedValue(
		sheet({ files: 40, rows: [row('thumbnails', 'Thumbnails', { files: 0, cannot: 22 })] })
	);
	await draw();
	const sentence = await vi.waitFor(() => {
		const found = [...stageRow('generate').querySelectorAll<HTMLElement>('.sentence')].find((one) =>
			one.textContent?.includes('left out')
		);
		expect(found?.textContent).toContain('left out');
		return found!;
	});
	const joined = sentence.querySelector<HTMLElement>('.with-press')!;
	applyStyles(importing, joined);
	expect(joined.querySelector('button')?.textContent?.trim()).toBe('Try again');
	expect(joined.textContent?.trim().startsWith('out.')).toBe(true);
	expect(getComputedStyle(joined).whiteSpace).toBe('nowrap');
	/* The rest of the sentence is still free to wrap. */
	expect(sentence.textContent?.replace(/\s+/g, ' ')).toContain('are left out. Try again');
	removeStyles();
});

it('makes the count of files a product left out a link to them, beside Try again', async () => {
	/* A count of files nobody can see is a number with no way to act on it. The count opens the
	   Files wall filtered to exactly those files, each saying why under its tile; the key is the
	   row's own, which the query language takes as itself (a gate holds that). */
	mocks.fetchBuildSheet.mockResolvedValue(
		sheet({
			files: 40,
			rows: [
				row('thumbnails', 'Thumbnails', { files: 0, cannot: 24 }),
				row('faces', 'Faces', { files: 0, cannot: 1 })
			]
		})
	);
	await draw();
	const link = await vi.waitFor(() => {
		const found = stageRow('generate').querySelector<HTMLAnchorElement>('a[data-left-out]');
		expect(found).not.toBeNull();
		return found!;
	});
	expect(link.textContent?.trim()).toBe('24 files');
	expect(link.getAttribute('href')).toBe('/browse?left_out=thumbnails');
	const sentence = link.closest('.sentence')!;
	expect(sentence.textContent?.replace(/\s+/g, ' ').trim()).toBe(
		"24 files couldn't have thumbnails generated and are left out. Try again"
	);
	/* One file agrees with its number, on the other stage's row, with its own product's link. */
	const one = stageRow('identify').querySelector<HTMLAnchorElement>('a[data-left-out]')!;
	expect(one.getAttribute('href')).toBe('/browse?left_out=faces');
	expect(one.closest('.sentence')?.textContent?.replace(/\s+/g, ' ').trim()).toBe(
		"1 file couldn't be checked for faces and is left out. Try again"
	);
});

it('links the benchmark sentence to the row it runs from, by its breadcrumb', async () => {
	/* Saying the benchmark has not run is not enough: the reader has to find where it is run.
	   The link is a settings link, landing on the row. */
	mocks.fetchBuildSheet.mockResolvedValue(sheet({ measure_first: true }));

	await draw();

	const link = await vi.waitFor(() => {
		const found = host.querySelector<HTMLAnchorElement>('a.setting-link[href*="performance"]');
		if (found === null) throw new Error('no link to the benchmark');
		return found;
	});
	expect(link.textContent?.trim()).toBe('Settings > Performance > Benchmark this device');
	expect(link.getAttribute('href')).toContain('performance.measure');
	expect(link.parentElement?.textContent?.replace(/\s+/g, ' ')).toContain(
		"This device hasn't been benchmarked yet."
	);
});
