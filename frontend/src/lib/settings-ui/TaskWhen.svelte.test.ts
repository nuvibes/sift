/* One task's row, and the store both of its doors read. */

import { afterEach, beforeAll, beforeEach, expect, it, vi } from 'vitest';
import { wordsOf } from '$lib/components/common/toast-pieces';

import type { TaskView, TasksView } from '$lib/jobs/tasks.svelte';

/* Svelte itself is imported AFTER each test resets the module registry, beside the component:
   the store is the app's one copy, so every test needs a fresh one, and a component compiled
   against one copy of the runtime cannot be mounted by another (`effect_orphan`). */
let svelte: typeof import('svelte');

const mocks = vi.hoisted(() => ({
	get: vi.fn(),
	post: vi.fn(),
	saveSettings: vi.fn(),
	show: vi.fn()
}));

vi.mock('$lib/api/client', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/client')>()),
	api: { get: mocks.get, post: mocks.post }
}));

vi.mock('$lib/settings-ui/settings', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/settings-ui/settings')>()),
	saveSettings: mocks.saveSettings
}));

vi.mock('$lib/shell/toasts.svelte', () => ({ toasts: { show: mocks.show } }));

const NOW = 1_700_000_000;

function task(over: Partial<TaskView> = {}): TaskView {
	return {
		id: 'faces',
		title: 'Recognize faces',
		explain: 'Finds faces in your files.',
		unit: 'file',
		units: 'files',
		when: 'quiet',
		when_key: 'tasks.faces.when',
		whens: [
			{ value: 'work', label: 'As soon as there is work' },
			{ value: 'quiet', label: 'During quiet hours' },
			{ value: 'press', label: 'Only when I press it' }
		],
		on: true,
		cadence: 'During quiet hours',
		setting_keys: [],
		drawn_keys: [],
		set_in: 'faces',
		last: null,
		next_run: null,
		waiting: 0,
		held: 0,
		running: false,
		press: 'Run now',
		reads: [],
		off: null,
		parts: [],
		locations: false,
		dry: false,
		dry_run: null,
		dry_running: false,
		// What one waiting row is, as the route says it (`ScheduledTask.unit`).
		...({ unit: 'file', units: 'files' } as object),
		...over
	};
}

function list(...tasks: TaskView[]): TasksView {
	return {
		quiet_hours: {
			starts: '23:00',
			ends: '07:00',
			open: false,
			opens_at: NOW + 3600,
			closes_at: null
		},
		keep_awake: true,
		awake_now: false,
		tasks,
		folders: [{ key: 'root-a', label: 'Holidays', path: 'D:/Holidays' }]
	};
}

/* The press's trailing half opens its menu the way a pointer does: the library opens on
   pointerdown, not on click. */
async function openMenu(): Promise<void> {
	const buttons = host.querySelectorAll<HTMLButtonElement>('.press button');
	const door = buttons[buttons.length - 1];
	await vi.waitFor(() => expect(door.disabled).toBe(false));
	door.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0 }));
	svelte.flushSync();
	await vi.waitFor(() => expect(document.querySelector('[role="menu"]')).not.toBeNull());
}

/* A row of the open menu by its words, the glyph's ligature left out. */
function item(named: string): HTMLElement {
	const found = [
		...document.querySelectorAll<HTMLElement>('[role="menuitem"], [role="menuitemcheckbox"]')
	].find(
		(one) =>
			(one.textContent ?? '')
				.replace(/[\uE000-\uF8FF]|check_box\w*|schedule|play_arrow|checklist/g, '')
				.trim() === named
	);
	if (!found) throw new Error(`no menu row "${named}"`);
	return found;
}

async function choose(named: string): Promise<void> {
	await openMenu();
	item(named).click();
	svelte.flushSync();
}

/* The first import of the component compiles its whole graph, which can cost more than one
   test's time limit. */
beforeAll(async () => {
	await import('./TaskWhen.svelte');
}, 60_000);

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

beforeEach(async () => {
	vi.resetModules();
	svelte = await import('svelte');
	vi.clearAllMocks();
	vi.setSystemTime(NOW * 1000);
	mocks.saveSettings.mockResolvedValue(undefined);
	mocks.post.mockResolvedValue({ job_ids: ['j-1'], starts_at: null });
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (drawn) svelte.unmount(drawn);
	drawn = null;
	host?.remove();
	vi.useRealTimers();
});

/* A fresh store per test: the store is the app's one copy, so a module kept between tests would
   hand the next one the last one's list. */
async function draw(
	answer: TasksView,
	props: { task: string } & Record<string, unknown> = { task: 'faces' }
) {
	mocks.get.mockResolvedValue(answer);
	const { default: TaskWhen } = await import('./TaskWhen.svelte');
	drawn = svelte.mount(TaskWhen, { target: host, props }) as Record<string, unknown>;
	svelte.flushSync();
	await svelte.tick();
	await svelte.tick();
	svelte.flushSync();
}

it('carries the When key as its address, before the list has even come back', async () => {
	/* Activity's "Run in Tasks", a search result and Performance's pointer all ring this id, and
	   a deep link must not race the request, so it is on the row from the first frame. */
	mocks.get.mockReturnValue(new Promise(() => {}));
	const { default: TaskWhen } = await import('./TaskWhen.svelte');
	drawn = svelte.mount(TaskWhen, { target: host, props: { task: 'faces' } }) as Record<
		string,
		unknown
	>;
	svelte.flushSync();

	expect(host.querySelector('[id="tasks.faces.when"]')).not.toBeNull();
});

it('presses Run now and Run during quiet hours through the one run route, each with its half', async () => {
	await draw(list(task()));

	const buttons = host.querySelectorAll<HTMLButtonElement>('.press button');
	buttons[0].click();
	await vi.waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
	expect(mocks.post).toHaveBeenLastCalledWith('/tasks/faces/run', { body: { at: 'now' } });

	await choose('Run during quiet hours');
	await vi.waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(2));
	expect(mocks.post).toHaveBeenLastCalledWith('/tasks/faces/run', { body: { at: 'quiet' } });
});

it('says Run during quiet hours started when the run it landed on is not waiting for the range', async () => {
	/* Run during quiet hours pressed on a Scan already going must not say "Queued for quiet
	   hours" over a walk that is under way. */
	const { COPY } = await import('./ScheduledTasks.search');
	mocks.post.mockResolvedValue({ job_ids: ['j-1'], starts_at: null });
	await draw(list(task()));

	await choose('Run during quiet hours');
	await vi.waitFor(() => expect(mocks.show).toHaveBeenCalled());
	expect(wordsOf(mocks.show.mock.calls[0][0])).toBe('Started. Follow it in Activity.');

	mocks.show.mockClear();
	mocks.post.mockResolvedValue({ job_ids: ['j-2'], starts_at: NOW + 3600 });
	await choose('Run during quiet hours');
	await vi.waitFor(() => expect(mocks.show).toHaveBeenCalled());
	expect(wordsOf(mocks.show.mock.calls[0][0])).not.toBe('Started. Follow it in Activity.');
});

it("says the server's own sentence when a press is refused", async () => {
	/* "Smart Search is turned off. Turn it on under Smart Search." */
	const { ApiError } = await import('$lib/api/client');
	mocks.post.mockRejectedValue(
		new ApiError(409, 'Conflict', 'Smart Search is turned off. Turn it on under Smart Search.')
	);
	await draw(list(task()));

	host.querySelector<HTMLButtonElement>('.press button')?.click();
	await vi.waitFor(() => expect(mocks.show).toHaveBeenCalled());
	expect(mocks.show.mock.calls[0][0]).toBe(
		'Smart Search is turned off. Turn it on under Smart Search.'
	);
	expect(mocks.show.mock.calls[0][1]).toEqual({ tone: 'error' });
});

it('says a failed last run failed, rather than that it ran', async () => {
	await draw(
		list(
			task({
				last: {
					ended_at: NOW - 7200,
					outcome: 'failed',
					seconds: 30,
					said: 'No disk',
					report: null
				}
			})
		)
	);

	const facts = host.querySelector('.facts')?.textContent ?? '';
	expect(facts).toContain('Last run failed 2 hours ago');
	expect(host.querySelector('.facts .failed')).not.toBeNull();
});

it('is one line: the press on the right with the choice inside it, the facts under the help on the left', async () => {
	await draw(
		list(
			task({ last: { ended_at: NOW - 7200, outcome: 'ok', seconds: 3, said: null, report: null } })
		)
	);
	const { applyStyles, removeStyles } = await import('$lib/design/testing-styles');
	const { default: rowSource } = await import('$lib/components/common/LabelledRow.svelte?raw');
	const { default: ownSource } = await import('./TaskWhen.svelte?raw');
	const when = host.querySelector<HTMLElement>('.when');
	const row = host.querySelector<HTMLElement>('.row');
	applyStyles(ownSource, when);
	applyStyles(rowSource, row);
	try {
		// The facts are the row's foot, a line of its own under both columns at the reading
		// measure, and nothing of them is in the control.
		expect(host.querySelector('.row > .foot .facts')?.textContent).toContain(
			'Last ran 2 hours ago'
		);
		expect(host.querySelector('.control .facts')).toBeNull();
		// The press on one flex line in the wide column, and no chooser beside it: the When is in
		// the press's own menu.
		expect(row?.classList.contains('wide')).toBe(true);
		const laid = getComputedStyle(when as HTMLElement);
		expect(laid.display).toBe('flex');
		expect(laid.flexDirection).not.toBe('column');
		expect(when?.querySelector(':scope > .choice')).toBeNull();
		expect(when?.querySelector(':scope > .press')).not.toBeNull();
	} finally {
		removeStyles();
	}
});

it('says work held for quiet hours is waiting FOR them, and when the next run is', async () => {
	await draw(list(task({ waiting: 1200, held: 1200, next_run: NOW + 3600 })));

	const facts = host.querySelector('.facts')?.textContent ?? '';
	expect(facts).toContain('1,200 files waiting for quiet hours');
	expect(facts).toContain('Next in an hour');
});

/* Never "1 waiting": a number with nothing it counts. */
it('says what a waiting count counts, one run', async () => {
	await draw(list(task({ waiting: 1, ...({ unit: 'run', units: 'runs' } as object) })));
	expect(host.querySelector('.facts')?.textContent).toContain('1 run waiting');
});

it('says what a waiting count counts, several files', async () => {
	await draw(list(task({ waiting: 3, when: 'work' })));
	expect(host.querySelector('.facts')?.textContent).toContain('3 files waiting');
});

it("takes the owning pane's label, and the task's own words where none is given", async () => {
	await draw(list(task()), { task: 'faces', label: 'When it runs', help: '' });
	expect(host.textContent).toContain('When it runs');
	expect(host.textContent).not.toContain('Finds faces in your files.');
});

it('changes the When through the ordinary settings write, and puts it back on a refusal', async () => {
	mocks.get.mockResolvedValue(list(task()));
	const { taskList } = await import('$lib/jobs/tasks.svelte');
	await taskList.load();

	await taskList.setWhen('faces', 'work');
	expect(mocks.saveSettings).toHaveBeenCalledWith({ 'tasks.faces.when': 'work' });

	mocks.saveSettings.mockRejectedValue(new Error('refused'));
	await expect(taskList.setWhen('faces', 'press')).rejects.toThrow('refused');
	expect(taskList.row('faces')?.when).not.toBe('press');
});

it('never has two reads of the list in the air, and never drops the last ring', async () => {
	/* The queue rings many times a second while a scan runs. */
	let release: (value: TasksView) => void = () => {};
	mocks.get.mockImplementationOnce(() => new Promise<TasksView>((resolve) => (release = resolve)));
	mocks.get.mockResolvedValue(list(task({ waiting: 7 })));
	const { taskList } = await import('$lib/jobs/tasks.svelte');

	const first = taskList.load();
	void taskList.load();
	void taskList.load();
	expect(mocks.get).toHaveBeenCalledTimes(1);

	release(list(task()));
	await first;
	expect(mocks.get).toHaveBeenCalledTimes(2);
	expect(taskList.row('faces')?.waiting).toBe(7);
});

/* The music task as `/api/tasks` describes it, answering whichever When the server holds. */
function music(when: string): TaskView {
	return task({
		id: 'music',
		title: 'Generate music fingerprints',
		explain:
			"Lets Sift match files that share a song. Generating one reads the file's sound track.",
		when,
		when_key: 'tasks.music.when',
		set_in: 'importing'
	});
}

it("draws the music task's When from the server's answer: press out of the box", async () => {
	/* A fresh install answers "press" for music, and the row says so in the When's own words. */
	await draw(list(music('press')), { task: 'music', press: false });

	expect(host.querySelector('[data-task="music"]')?.textContent).toContain('Only when I press it');
});

it("draws the music task's When from the answer, never from a default of the row's own", async () => {
	/* An install somebody moved back to "work" reads that instead: the row has no answer of its
	   own to fall back on, so what the server holds is what it says. */
	await draw(list(music('work')), { task: 'music', press: false });

	const said = host.querySelector('[data-task="music"]')?.textContent ?? '';
	expect(said).toContain('As soon as there is work');
	expect(said).not.toContain('Only when I press it');
});

it('says Run now on every press, whatever the task calls its own and whatever its When', async () => {
	/* The press runs the task now under every When, so its words say that; a verb of the task's
	   own ("Scan now") and a When folded into the words would each say something else. */
	await draw(list(task({ id: 'scan', when_key: 'tasks.scan.when', press: 'Scan now' })), {
		task: 'scan'
	});
	const lead = host.querySelector<HTMLButtonElement>('.press button');
	expect(lead?.textContent?.replace(/[\uE000-\uF8FF]|play_arrow/g, '').trim()).toBe('Run now');
	expect(host.querySelector('.press')?.textContent).not.toContain('Scan now');
	expect(host.querySelector('[role="combobox"]')).toBeNull();
});

it('runs from the main half, writes the When from the chevron, and moves the tick', async () => {
	/* One split button per task. The main half is the press, the chevron's first group is the
	   When with the current answer ticked, and picking an answer is the ordinary settings write
	   the chooser makes. */
	await draw(list(task()));
	// At rest the row still says when it runs: the first fact, in the words the menu ticks.
	expect(host.querySelector('.facts [data-fact="when"]')?.textContent).toBe(
		'During quiet hours (11 PM to 7 AM)'
	);
	const lead = host.querySelector<HTMLButtonElement>('.press button');
	lead?.click();
	await vi.waitFor(() =>
		expect(mocks.post).toHaveBeenCalledWith('/tasks/faces/run', { body: { at: 'now' } })
	);

	await openMenu();
	const quiet = item('During quiet hours (11 PM to 7 AM)');
	expect(quiet.getAttribute('aria-checked')).toBe('true');
	expect(item('Only when I press it').getAttribute('aria-checked')).toBe('false');

	// What the server answers once the write has landed, as it would.
	mocks.get.mockResolvedValue(list(task({ when: 'press' })));
	item('Only when I press it').click();
	svelte.flushSync();
	await vi.waitFor(() =>
		expect(mocks.saveSettings).toHaveBeenCalledWith({ 'tasks.faces.when': 'press' })
	);
	await vi.waitFor(() =>
		expect(item('Only when I press it').getAttribute('aria-checked')).toBe('true')
	);
	expect(item('During quiet hours (11 PM to 7 AM)').getAttribute('aria-checked')).toBe('false');
	expect(host.querySelector('.facts [data-fact="when"]')?.textContent).toBe('Only when I press it');
});

it('draws no press where the owning pane says so, and what the pane puts beside the choice', async () => {
	/* Presses live on Tasks; Importing draws the same row with its stage's Edit instead. */
	const edit = svelte.createRawSnippet(() => ({
		render: () => '<button class="edit">Edit</button>'
	}));
	await draw(list(task()), { task: 'faces', press: false, beside: edit });

	expect(host.textContent).not.toContain('Run now');
	expect(host.querySelectorAll('.when button')).toHaveLength(
		host.querySelectorAll('.when .edit').length + 1
	);
	expect(host.querySelector('.when .edit')?.textContent).toBe('Edit');
});

it('reads Mixed for a stage whose tasks disagree, and does not offer it as an answer', async () => {
	await draw(
		list(
			task({
				id: 'identify',
				when_key: 'tasks.identify.when',
				when: 'mixed',
				reads: ['faces', 'smart-search', 'watermarks']
			})
		),
		{ task: 'identify' }
	);
	await openMenu();
	const mixed = item('Mixed');
	expect(mixed.getAttribute('aria-checked')).toBe('true');
	expect(mixed.getAttribute('data-disabled')).not.toBeNull();
});

it('says which switch is off and where to turn it on, and offers no press that would be refused', async () => {
	/* A task whose feature is off does nothing, so its row says why: it names the switch and the
	   pane, and the When stays live. */
	await draw(list(task({ off: { key: 'faces.enabled', section: 'Identify' } })));

	const said = host.querySelector('.facts .off')?.textContent?.replace(/\s+/g, ' ').trim();
	expect(said).toBe('Recognize faces is off. Turn it on under Faces.');
	expect(host.querySelector('.facts .off a')?.getAttribute('href')).toBe(
		'/settings/faces#faces.enabled'
	);
	expect(host.querySelector('.press')).toBeNull();
	expect(host.querySelector('[data-task="faces"] .choice')).not.toBeNull();
});

it("offers the task's own parts with ticks, and runs only the ticked ones", async () => {
	/* The menu is drawn from the task's declaration, the one the server checks the press against. */
	const { COPY } = await import('./ScheduledTasks.search');
	mocks.post.mockResolvedValue({ job_ids: ['j-1'], starts_at: null, named: 'Thumbnails' });
	await draw(
		list(
			task({
				id: 'generate',
				when_key: 'tasks.generate.when',
				parts: [
					{ key: 'thumbnails', label: 'Thumbnails', path: '' },
					{ key: 'previews', label: 'Hover previews', path: '' }
				],
				dry: true
			})
		),
		{ task: 'generate' }
	);

	await openMenu();
	expect(item('Run the ticked ones now').getAttribute('data-disabled')).not.toBeNull();
	item('Thumbnails').click();
	svelte.flushSync();
	expect(item('Thumbnails').getAttribute('aria-checked')).toBe('true');
	expect(() => item('Holidays')).toThrow();
	item('Run the ticked ones now').click();
	await vi.waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
	expect(mocks.post).toHaveBeenLastCalledWith('/tasks/generate/run', {
		body: { at: 'now', parts: ['thumbnails'] }
	});
	await vi.waitFor(() => expect(mocks.show).toHaveBeenCalled());
	expect(wordsOf(mocks.show.mock.calls[0][0])).toBe(
		'Started for Thumbnails. Follow it in Activity.'
	);
});

it('runs Scan for the ticked folders, and asks a dry run only of a task that has one', async () => {
	await draw(list(task({ id: 'scan', when_key: 'tasks.scan.when', locations: true })), {
		task: 'scan'
	});

	await openMenu();
	expect(() => item('Dry run')).toThrow();
	item('Holidays').click();
	svelte.flushSync();
	item('Run the ticked ones now').click();
	await vi.waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
	expect(mocks.post).toHaveBeenLastCalledWith('/tasks/scan/run', {
		body: { at: 'now', locations: ['root-a'] }
	});
});

it('lays a report out as rows: its sentence, each count, the first names, what cannot run', async () => {
	await draw(
		list(
			task({
				dry: true,
				dry_run: {
					ended_at: NOW - 120,
					outcome: 'done',
					seconds: 1,
					said: 'Identify would work on 1,250 files. Nothing was changed.',
					report: {
						headline: 'Identify would work on 1,250 files.',
						parts: [
							{ label: 'Faces', count: 1240 },
							{ label: 'Watermarks', count: 1200 }
						],
						named: 'First files',
						names: ['beach.mp4', 'dunes.jpg'],
						more: 1248,
						cannot: ['Smart Search needs its model.']
					}
				}
			})
		)
	);

	(host.querySelector('.facts .dry') as HTMLElement).click();
	const panel = await vi.waitFor(() => {
		const found = document.querySelector('[role="dialog"] div.report');
		expect(found).not.toBeNull();
		return found as HTMLElement;
	});
	expect(panel.querySelector('.headline')?.textContent).toBe('Identify would work on 1,250 files.');
	const rows = [...panel.querySelectorAll('.counts > div')].map((one) => [
		one.querySelector('dt')?.textContent,
		one.querySelector('dd')?.textContent
	]);
	expect(rows).toEqual([
		['Faces', '1,240'],
		['Watermarks', '1,200']
	]);
	expect(panel.querySelector('.named')?.textContent).toBe('First files');
	expect([...panel.querySelectorAll('.names li')].map((one) => one.textContent?.trim())).toEqual([
		'beach.mp4',
		'dunes.jpg',
		'and 1,248 more'
	]);
	expect(panel.querySelector('.cannot')?.textContent).toBe('Smart Search needs its model.');
	expect(panel.lastElementChild?.textContent).toBe('Nothing was changed.');
	// A long name is one line cut at its end, whole on the app's own tooltip (never the browser's);
	// broken anywhere, it would split ".jpg".
	const first = panel.querySelector('.names li .cut') as HTMLElement;
	expect(first.textContent).toBe('beach.mp4');
	expect(first.closest('.target'), 'the name stands on a Tooltip target').not.toBeNull();
	expect(panel.querySelector('.names li[title]')).toBeNull();
	const { applyStyles, removeStyles } = await import('$lib/design/testing-styles');
	const { default: source } = await import('./TaskWhen.svelte?raw');
	applyStyles(source, first);
	expect(getComputedStyle(first).whiteSpace).toBe('nowrap');
	expect(getComputedStyle(first).textOverflow).toBe('ellipsis');
	removeStyles();
});

it('asks for a dry run through the same route, and says its report on the row', async () => {
	const { COPY } = await import('./ScheduledTasks.search');
	mocks.post.mockResolvedValue({ job_ids: ['d-1'], starts_at: null, dry: true });
	const report = 'Recognize faces would work on 3 files. Nothing was changed.';
	await draw(
		list(
			task({
				dry: true,
				dry_run: { ended_at: NOW - 120, outcome: 'done', seconds: 1, said: report, report: null }
			})
		)
	);

	const phrase = host.querySelector('.facts .dry') as HTMLElement;
	expect(phrase.textContent).toContain('Dry run 2 minutes ago');
	// A report is a panel's worth of words: it opens from the phrase, never as its tooltip.
	expect(phrase.tagName).toBe('BUTTON');
	phrase.click();
	await vi.waitFor(() =>
		expect(document.querySelector('[role="dialog"] .report')?.textContent).toBe(report)
	);
	phrase.click();
	await choose('Dry run');
	await vi.waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
	expect(mocks.post).toHaveBeenLastCalledWith('/tasks/faces/run', {
		body: { at: 'now', dry: true }
	});
	await vi.waitFor(() => expect(mocks.show).toHaveBeenCalled());
	expect(mocks.show.mock.calls[0][0]).toBe(COPY.when.dryStarted);
});

it('says a run going now on the In progress chip, never a phrase of its own', async () => {
	/* Not a grey "Running now" among the facts: Activity's rows and Downloads draw the same
	   state as the blue In progress chip. */
	await draw(list(task({ running: true })));
	const chip = host.querySelector('[data-fact="running"] .badge');
	expect(chip, 'the In progress chip').not.toBeNull();
	expect(chip?.textContent).toContain('In progress');
	expect(host.querySelector('.facts')?.textContent).not.toContain('Running');
});

it('says a dry run is under way until its report lands, and follows the jobs bell to it', async () => {
	/* A plan of a whole library can take half a minute. */
	await draw(list(task({ dry: true, dry_running: true })));
	const facts = () => host.querySelector('.facts')?.textContent ?? '';
	expect(facts()).toContain('Dry run in progress');
	// On the In progress chip, the one every screen draws for work under way.
	expect(host.querySelector('[data-fact="dry-running"] .badge')).not.toBeNull();
	expect(host.querySelector('.facts .dry')).toBeNull();

	mocks.get.mockResolvedValue(
		list(
			task({
				dry: true,
				dry_run: { ended_at: NOW - 60, outcome: 'done', seconds: 29, said: 'Done.', report: null }
			})
		)
	);
	const { jobChanges } = await import('$lib/library/changes.svelte');
	jobChanges.changed();
	await vi.waitFor(() =>
		expect(host.querySelector('.facts .dry')?.textContent).toContain('Dry run')
	);
	expect(facts()).not.toContain('under way');
});

it('heads the parts and the folders in its menu, so the two lists read apart', async () => {
	/* Both lists are ticked rows, so a line alone would leave the parts and the folders reading
	   as one list. */
	const { COPY } = await import('./ScheduledTasks.search');
	const { applyStyles, removeStyles } = await import('$lib/design/testing-styles');
	const { default: groupSource } =
		await import('$lib/components/common/ContextMenuGroup.svelte?raw');
	await draw(
		list(
			task({
				id: 'generate',
				when_key: 'tasks.generate.when',
				parts: [{ key: 'thumbnails', label: 'Thumbnails', path: '' }],
				locations: true
			})
		),
		{ task: 'generate' }
	);

	await openMenu();
	applyStyles(groupSource);
	const headings = [...document.querySelectorAll<HTMLElement>('[role="menu"] .menu-heading')];
	expect(headings.map((one) => one.textContent?.trim())).toEqual([
		COPY.when.whenHeading,
		COPY.when.parts,
		COPY.when.folders
	]);
	for (const heading of headings) {
		const group = heading.parentElement?.closest('[role="group"]') as HTMLElement;
		await vi.waitFor(() => expect(group.getAttribute('aria-labelledby')).toBe(heading.id));
		expect(group.getAttribute('aria-label')).toBeNull();
		expect(getComputedStyle(heading).color).toBe('var(--sift-ink-3)');
		expect(getComputedStyle(heading).font).toBe('var(--text-label)');
	}
	// A group that asks for no heading draws none: the acts under the When have no label over them.
	expect(document.querySelectorAll('[role="menu"] .menu-heading')).toHaveLength(3);
	removeStyles();
});

it('puts every way to run at the top, and filters the parts and folders with the box under them', async () => {
	/* Under a long list of folders, Run the ticked ones now would be a scroll away from the
	   ticks it runs; and a long list needs a box to find a folder in, as every Add to list has. */
	const { COPY } = await import('./ScheduledTasks.search');
	await draw(
		list(
			task({
				id: 'generate',
				title: 'Generate',
				when_key: 'tasks.generate.when',
				dry: true,
				parts: [
					{ key: 'thumbnails', label: 'Thumbnails', path: '' },
					{ key: 'previews', label: 'Previews', path: '' }
				]
			})
		),
		{ task: 'generate' }
	);
	await openMenu();
	const rows = () =>
		[
			...document.querySelectorAll<HTMLElement>(
				'[role="menu"] [role="menuitem"], [role="menu"] [role="menuitemcheckbox"]'
			)
		].map((one) =>
			one.textContent
				?.replace(/^\s*[a-z_]+/, '')
				.replace(/^\W+/, '')
				.trim()
		);
	// The When's three answers first, then every way to run it, then the parts.
	expect(rows().slice(0, 3)).toEqual([
		'As soon as there is work',
		'During quiet hours (11 PM to 7 AM)',
		'Only when I press it'
	]);
	expect(rows().slice(3, 6)).toEqual([COPY.when.atQuiet, COPY.when.runTicked, COPY.when.dryRun]);
	expect(rows()).toContain('Thumbnails');

	const box = document.querySelector<HTMLInputElement>('[role="menu"] input');
	expect(box?.getAttribute('aria-label')).toBe(COPY.when.narrow('Generate'));
	box!.value = 'prev';
	box!.dispatchEvent(new Event('input', { bubbles: true }));
	svelte.flushSync();
	expect(rows()).toContain('Previews');
	expect(rows()).not.toContain('Thumbnails');

	box!.value = 'nothing like it';
	box!.dispatchEvent(new Event('input', { bubbles: true }));
	svelte.flushSync();
	expect(document.querySelector('[role="menu"]')?.textContent).toContain(COPY.when.nothingMatches);
});
