/* The Tasks pane: quiet hours as one row at the top, Import tasks, every other task folded under
 * them, Activity under that, and every setting filed here drawn.
 *
 * ## What is worth pinning here
 *
 * The row itself is `TaskWhen` and is proved beside it. What this file owns is the arrangement the
 * whole model rests on: quiet hours come first as one row whose page holds the two times (a link to
 * either still lands, because the row's page opens for it), the stages come before everything else,
 * every task gets its row with its own When key as its address (Activity's "Run in Tasks" rings
 * those), a task's own settings sit under its row, and a setting filed in this section that no task
 * claims is still drawn rather than silently on no screen.
 */

import { afterEach, beforeAll, beforeEach, expect, it, vi } from 'vitest';

import type { SettingEntry, SettingSection } from '$lib/settings-ui/settings';
import type { TaskView, TasksView } from '$lib/jobs/tasks.svelte';

/* Svelte itself is imported AFTER each test resets the module registry, beside the component: the
   store is the app's one copy, so every test needs a fresh one, and a component compiled against
   one copy of the runtime cannot be mounted by another (`effect_orphan`). */
let svelte: typeof import('svelte');

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), fetchSettings: vi.fn() }));

vi.mock('$lib/api/client', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/client')>()),
	api: { get: mocks.get, post: mocks.post }
}));

/* Import tasks' counts and folders are proved beside `Importing`; here they answer nothing. */
vi.mock('$lib/library/importing', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/library/importing')>()),
	fetchBuildSheet: vi.fn(() => new Promise(() => {})),
	fetchFolderAnswers: vi.fn(() => new Promise(() => {}))
}));

vi.mock('$lib/settings-ui/settings', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/settings-ui/settings')>()),
	fetchSettings: mocks.fetchSettings,
	saveSettings: vi.fn()
}));

const NOW = 1_700_000_000;

function task(id: string, over: Partial<TaskView> = {}): TaskView {
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
		when: 'work',
		when_key: `tasks.${id}.when`,
		whens: [
			{ value: 'work', label: 'As soon as there is work' },
			{ value: 'quiet', label: 'During quiet hours' },
			{ value: 'press', label: 'Only when I press it' }
		],
		on: true,
		cadence: 'As soon as there is work',
		setting_keys: [],
		drawn_keys: [],
		set_in: '',
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

function tasks(...rows: TaskView[]): TasksView {
	return {
		folders: [],
		quiet_hours: {
			starts: '23:00',
			ends: '07:00',
			open: true,
			opens_at: NOW,
			closes_at: NOW + 3600
		},
		keep_awake: true,
		awake_now: true,
		tasks: rows
	};
}

function entry(key: string, over: Partial<SettingEntry> = {}): SettingEntry {
	return { key, value: true, default: true, label: `Label of ${key}`, ...over };
}

/* The first import of the component compiles its whole graph, which can cost more than one
   test's time limit. Paid once here, so no test is timed on the compiler; each test still
   evaluates a fresh copy after the reset below. */
beforeAll(async () => {
	await import('./ScheduledTasks.svelte');
}, 60_000);

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

beforeEach(async () => {
	vi.resetModules();
	svelte = await import('svelte');
	vi.clearAllMocks();
	vi.setSystemTime(NOW * 1000);
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (drawn) svelte.unmount(drawn);
	drawn = null;
	host?.remove();
	vi.useRealTimers();
});

async function draw(
	answer: TasksView,
	sections: SettingSection[],
	props: Record<string, unknown> = {}
): Promise<void> {
	mocks.get.mockResolvedValue(answer);
	mocks.fetchSettings.mockResolvedValue(sections);
	const { default: ScheduledTasks } = await import('./ScheduledTasks.svelte');
	drawn = svelte.mount(ScheduledTasks, { target: host, props }) as Record<string, unknown>;
	svelte.flushSync();
	await svelte.tick();
	await svelte.tick();
	await svelte.tick();
	svelte.flushSync();
}

const SECTION = 'Scheduled tasks';

const QUIET = [
	entry('tasks.quiet_from', { value: '23:00', default: '23:00' }),
	entry('tasks.quiet_until', { value: '07:00', default: '07:00' }),
	entry('tasks.keep_awake')
];

it('draws quiet hours first, as one row saying the range', async () => {
	await draw(tasks(task('scan')), [{ name: SECTION, settings: QUIET }]);

	const quiet = host.querySelector<HTMLElement>('[id="tasks.quiet-hours"]');
	const scan = host.querySelector('[id="tasks.scan.when"]');
	// The words every "During quiet hours" on a task row carries after it.
	expect(quiet?.textContent).toContain('11 PM to 7 AM');
	// Above every task: the range is what every "During quiet hours" below is read against.
	expect(quiet!.compareDocumentPosition(scan!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
	expect(quiet?.textContent).toContain('Quiet hours are on now');
	expect(quiet?.textContent).toContain('This device is being kept awake');
	// The two times are the row's page, not rows on the pane.
	expect(host.querySelector('[id="tasks.quiet_from"]')).toBeNull();
});

it("opens the two times and the keep-awake switch on the row's page, for a link to either", async () => {
	/* A link naming `tasks.quiet_from` (a search result, an old bookmark, the budget page on
	   Importing) has to land: the row's page claims both times, so the hunt opens it first. */
	await draw(tasks(task('scan')), [{ name: SECTION, settings: QUIET }]);
	const { drilldown } = await import('./drilldown.svelte');
	const { default: DrilldownPage } = await import('./DrilldownPage.svelte');
	svelte.mount(DrilldownPage, { target: host, props: { behind: 'Tasks' } });

	expect(drilldown.reveal('tasks.quiet_from')).toBe(true);
	svelte.flushSync();
	await svelte.tick();
	svelte.flushSync();
	for (const key of ['tasks.quiet_from', 'tasks.quiet_until', 'tasks.keep_awake']) {
		expect(host.querySelector(`[id="${key}"]`), key).not.toBeNull();
	}
	drilldown.close();
});

it('puts the three stages first and folds every other task under them', async () => {
	await draw(
		tasks(task('scan'), task('generate'), task('identify'), task('faces'), task('backup')),
		[{ name: SECTION, settings: [] }]
	);

	const stages = host.querySelector('[id="tasks.stages"]')?.closest('section.group');
	const fold = host.querySelector<HTMLDetailsElement>('details[id="tasks.others"]');
	expect(stages, 'no Import tasks group').toBeTruthy();
	for (const id of ['scan', 'generate', 'identify']) {
		expect(stages?.querySelector(`[id="tasks.${id}.when"]`), id).toBeTruthy();
	}
	for (const id of ['faces', 'backup']) {
		expect(fold?.querySelector(`[id="tasks.${id}.when"]`), id).not.toBeNull();
	}
	expect(fold?.open).toBe(false);
	expect(fold?.querySelector('summary')?.textContent).toBe('Other tasks');
	// The one fold, not a disclosure drawn by hand.
	expect(fold?.classList.contains('fold')).toBe(true);
});

it('draws Activity right under Other tasks, when the screen hands it in', async () => {
	const activity = svelte.createRawSnippet(() => ({
		render: () => '<div data-probe="queue">the queue</div>'
	}));
	await draw(
		tasks(task('scan'), task('faces')),
		[{ name: SECTION, settings: [entry('x.loose')] }],
		{
			activity
		}
	);

	const heading = host.querySelector('[id="tasks.activity"]');
	expect(heading?.textContent).toBe('Activity');
	const fold = host.querySelector('details[id="tasks.others"]')!;
	const queue = host.querySelector('[data-probe="queue"]')!;
	const loose = host.querySelector('[id="x.loose"]')!;
	const after = (a: Node, b: Node) => Boolean(a.compareDocumentPosition(b) & 4);
	expect(after(fold, heading!)).toBe(true);
	expect(after(heading!, queue)).toBe(true);
	/* Other settings stays last, under the queue. */
	expect(after(queue, loose)).toBe(true);
});

it('draws no Activity heading when nothing is handed in', async () => {
	await draw(tasks(task('scan')), [{ name: SECTION, settings: [] }]);
	expect(host.querySelector('[id="tasks.activity"]')).toBeNull();
});

it('gives every task its row, addressed by its own When key', async () => {
	await draw(tasks(task('scan'), task('backup'), task('faces')), [{ name: SECTION, settings: [] }]);

	for (const id of ['scan', 'backup', 'faces']) {
		expect(host.querySelector(`[id="tasks.${id}.when"]`), id).not.toBeNull();
	}
});

it("draws a task's own settings under its row, and nothing a task claims twice", async () => {
	await draw(
		tasks(
			task('backup', {
				setting_keys: ['backup.every_days', 'backup.at'],
				drawn_keys: ['backup.every_days', 'backup.at']
			})
		),
		[
			{
				name: SECTION,
				settings: [
					entry('backup.every_days', { value: 1, default: 1 }),
					entry('backup.at', { value: '03:00', default: '03:00' }),
					entry('tasks.backup.when', { value: 'work', default: 'press' })
				]
			}
		]
	);

	expect(host.querySelectorAll('[id="backup.every_days"]')).toHaveLength(1);
	expect(host.querySelectorAll('[id="backup.at"]')).toHaveLength(1);
	// The When is the task's row, never also a plain setting row under "Other settings".
	expect(host.querySelectorAll('[id="tasks.backup.when"]')).toHaveLength(1);
	expect(host.textContent).not.toContain('Other settings');
});

it('draws only the settings the server says mean something under the When', async () => {
	/* During quiet hours the range's opening is the time, so the backup's Time of day is not drawn;
	   and a row the task claims but does not draw never falls through to Other settings. */
	await draw(
		tasks(
			task('backup', {
				when: 'quiet',
				setting_keys: ['backup.every_days', 'backup.at'],
				drawn_keys: ['backup.every_days']
			})
		),
		[
			{
				name: SECTION,
				settings: [
					entry('backup.every_days', { value: 7, default: 1 }),
					entry('backup.at', { value: '03:00', default: '03:00' })
				]
			}
		]
	);

	expect(host.querySelector('[id="backup.every_days"]')).not.toBeNull();
	expect(host.querySelector('[id="backup.at"]')).toBeNull();
	expect(host.textContent).not.toContain('Other settings');
});

it('still draws a setting filed here that no task claims', async () => {
	/* Registered, stored, acted on, and on no screen anybody can open, unless this draws it. */
	await draw(tasks(task('scan')), [
		{ name: SECTION, settings: [entry('tasks.something_new', { label: 'Something new' })] }
	]);

	expect(host.textContent).toContain('Other settings');
	expect(host.querySelector('[id="tasks.something_new"]')).not.toBeNull();
});

it('says it could not read the tasks rather than that nothing runs', async () => {
	mocks.fetchSettings.mockResolvedValue([]);
	mocks.get.mockRejectedValue(new Error('unreachable'));
	const { default: ScheduledTasks } = await import('./ScheduledTasks.svelte');
	drawn = svelte.mount(ScheduledTasks, { target: host }) as Record<string, unknown>;
	svelte.flushSync();
	await vi.waitFor(() => expect(host.textContent).toContain("Couldn't load the tasks"));
});

it("never draws a task's When as a plain row, for upkeep the list leaves out", async () => {
	/* The server leaves the prunes and the update check out of the list and keeps their Whens filed
	   here. Drawn under Other settings they would be the control the list does not offer. */
	await draw(tasks(task('scan')), [
		{
			name: SECTION,
			settings: [
				entry('tasks.update-check.when', { value: 'work', default: 'work' }),
				entry('tasks.quarantine-prune.when', { value: 'quiet', default: 'quiet' })
			]
		}
	]);

	expect(host.querySelector('[id="tasks.update-check.when"]')).toBeNull();
	expect(host.querySelector('[id="tasks.quarantine-prune.when"]')).toBeNull();
	expect(host.textContent).not.toContain('Other settings');
});

it('draws nothing under Other settings until the list of tasks has come back', async () => {
	mocks.fetchSettings.mockResolvedValue([
		{ name: SECTION, settings: [entry('backup.every_days', { value: 1, default: 1 })] }
	]);
	mocks.get.mockReturnValue(new Promise(() => {}));
	const { default: ScheduledTasks } = await import('./ScheduledTasks.svelte');
	drawn = svelte.mount(ScheduledTasks, { target: host }) as Record<string, unknown>;
	await vi.waitFor(() => expect(mocks.fetchSettings).toHaveBeenCalled());
	await svelte.tick();
	svelte.flushSync();

	// The backup task claims it once the list is here; until then it is nobody's to draw.
	expect(host.querySelector('[id="backup.every_days"]')).toBeNull();
	expect(host.textContent).not.toContain('Other settings');
});

it('opens Other tasks and rings the row for a link that arrives before the list does', async () => {
	/* A cold load of /settings/schedule#tasks.faces.when: the settings answer first, the list after.
	   The hunt must wait for the task's own row, inside the fold, and open the fold for it. */
	const scrolled = vi.fn();
	Element.prototype.scrollIntoView = scrolled;
	let answer: (value: TasksView) => void = () => {};
	mocks.get.mockReturnValue(new Promise<TasksView>((settle) => (answer = settle)));
	mocks.fetchSettings.mockResolvedValue([
		{ name: SECTION, settings: [...QUIET, entry('tasks.faces.when', { value: 'work' })] }
	]);
	const { default: ScheduledTasks } = await import('./ScheduledTasks.svelte');
	const { revealSetting } = await import('$lib/settings-ui/settings-anchor.svelte');
	drawn = svelte.mount(ScheduledTasks, { target: host }) as Record<string, unknown>;
	const found = revealSetting('tasks.faces.when');
	await vi.waitFor(() => expect(mocks.fetchSettings).toHaveBeenCalled());
	await svelte.tick();

	answer(tasks(task('scan'), task('faces')));
	await expect(found).resolves.toBe(true);
	const row = host.querySelector<HTMLElement>('[id="tasks.faces.when"]');
	expect(row?.closest('details[id="tasks.others"]')).not.toBeNull();
	expect(host.querySelector<HTMLDetailsElement>('details[id="tasks.others"]')?.open).toBe(true);
	await vi.waitFor(() => expect(row?.dataset.siftFound).toBe(''));
	expect(host.querySelectorAll('[id="tasks.faces.when"]')).toHaveLength(1);
});

it('says the quiet-hours times the way the range says them, never a second shape', async () => {
	const closed = tasks(task('scan'));
	closed.quiet_hours = { ...closed.quiet_hours, open: false, opens_at: NOW + 7200 };
	await draw(closed, [{ name: SECTION, settings: QUIET }]);

	const quiet = host.querySelector<HTMLElement>('[id="tasks.quiet-hours"]');
	expect(quiet?.textContent).toContain('Quiet hours start at 11 PM.');
});

it('says when open quiet hours end in the same shape as the range', async () => {
	await draw(tasks(task('scan')), [{ name: SECTION, settings: QUIET }]);
	const quiet = host.querySelector<HTMLElement>('[id="tasks.quiet-hours"]');
	expect(quiet?.textContent).toContain('Quiet hours are on now, until 7 AM.');
});
