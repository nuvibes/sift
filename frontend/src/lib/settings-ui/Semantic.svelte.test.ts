/* The Search-by-meaning pane.
 *
 * Everything here is a claim the screen makes about what this install can do, and each one is a
 * sentence somebody would act on. A machine that cannot load the add-on must be told so rather than
 * handed a switch; switched on is not the same as able to run, so a fresh install must read as
 * "fetch the models" and not as broken; and the control that throws the index away must not be
 * offered when there is no index to throw.
 *
 * All four are decided in the markup, from three fields of one answer, and nothing in the server
 * tests can see any of them.
 */

import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { words as wordsOn } from '$lib/design/testing.svelte';
import { flushSync, mount } from 'svelte';
import type { SettingSection } from '$lib/settings-ui/settings';
import type { SemanticStatus } from '$lib/search/semantic.svelte';
import Semantic from './Semantic.svelte';
import DrilldownPage from './DrilldownPage.svelte';
import { drilldown } from './drilldown.svelte';
import { settingChanges } from '$lib/library/changes.svelte';

const fetchSettings = vi.fn<() => Promise<SettingSection[]>>();
const saveSettings = vi.fn<(values: Record<string, unknown>) => Promise<void>>();
const semanticStatus = vi.fn<() => Promise<SemanticStatus>>();
const fetchSemanticModels = vi.fn(async (_again?: boolean) => ({ job_id: 'job-1' }));
const removeSemanticIndex = vi.fn(async () => ({ removed_frames: 12 }));
const jobProgress =
	vi.fn<
		(
			type: string,
			jobId: string
		) => Promise<{ state: string; progress: number; note: string | null } | null>
	>();

/* The task's When row reads the tasks store. No row answers here, so the row is drawn by its
   address with no controls in it: what is tested is that the pane draws the task's row at all,
   and draws no describe button of its own beside it. The row itself is TaskWhen's to test. */
/* The two deletions are jobs followed by `$lib/jobs/watch-removal`, tested beside it. */
vi.mock('$lib/jobs/watch-removal.svelte', () => {
	const watch = {
		running: false,
		resume: async () => {},
		follow: () => {},
		couldNotStart: () => {}
	};
	return { faceRemoval: watch, indexRemoval: watch };
});

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

vi.mock('$lib/settings-ui/settings', () => ({
	fetchSettings: () => fetchSettings(),
	saveSettings: (values: Record<string, unknown>) => saveSettings(values),
	// A rating is drawn wherever this reaches, and the rating scale registers its watcher at
	// module scope. A partial mock without this fails the suite at import.
	onSettingsSaved: vi.fn()
}));

vi.mock('$lib/search/semantic.svelte', () => ({
	SEMANTIC_ENABLED_KEY: 'semantic.enabled',
	SEMANTIC_DEVICE_KEY: 'semantic.device',
	FETCHING_MODELS: 'semantic_fetch_models',
	semanticStatus: () => semanticStatus(),
	fetchSemanticModels: (again?: boolean) => fetchSemanticModels(again),
	removeSemanticIndex: () => removeSemanticIndex(),
	jobProgress: (type: string, jobId: string) => jobProgress(type, jobId)
}));

/*
 * The two runs, stood in for.
 *
 * They live outside this component on purpose: a watcher held here would die with the pane. What
 * is left to test HERE is the markup: which of the three
 * states each section draws, and what it says in each. So the stores are replaced by plain objects
 * a test can set, and what they do internally is their own business.
 *
 * The stores' own behaviour (the estimate, the resume, and the "switched off mid-run" stop) is
 * tested in `semantic-runs.svelte.test.ts`.
 *
 * A stand-in must answer EVERY field the component reads. A field it leaves out comes back
 * undefined, and the branch behind it (which model file is arriving, the interrupted-run warning)
 * is never drawn by any test while it looks covered. The last test in this file reads the
 * component and refuses a field the stand-in has no answer for.
 */
const runs = vi.hoisted(() => {
	const fetching = {
		jobId: null as string | null,
		fraction: 0,
		note: null as string | null,
		/* Where the download has got to, IN WORDS. The watcher composes the sentence (every
		   pane building its own from the fraction and the note would say "0% - downloading" for
		   a job no worker has picked up), so what this pane owns is drawing it. What the
		   sentence SAYS is proved in `$lib/jobs/watch-download.svelte.test.ts`. */
		status: 'Waiting to start',
		outcome: null as string | null
	};
	const describingState = {
		status: null as unknown,
		outcome: null as string | null,
		done: 0,
		left: 0,
		fraction: null as number | null,
		remaining: null as number | null,
		running: false,
		stopped: false
	};
	return { fetching, describingState };
});

vi.mock('$lib/jobs/semantic-runs.svelte', () => ({
	availability: { load: async () => {}, refresh: async () => {}, available: false },
	modelFetch: {
		get running() {
			return runs.fetching.jobId !== null;
		},
		get fraction() {
			return runs.fetching.fraction;
		},
		get note() {
			return runs.fetching.note;
		},
		get status() {
			return runs.fetching.status;
		},
		get outcome() {
			return runs.fetching.outcome;
		},
		resume: async () => {},
		follow: (jobId: string) => {
			runs.fetching.jobId = jobId;
			runs.fetching.fraction = 0;
			runs.fetching.note = null;
			runs.fetching.status = 'Waiting to start';
			runs.fetching.outcome = null;
		},
		couldNotStart: (why: string) => {
			runs.fetching.jobId = null;
			runs.fetching.outcome = why;
		}
	},
	describing: {
		get status() {
			return runs.describingState.status;
		},
		get running() {
			return runs.describingState.running;
		},
		get stopped() {
			return runs.describingState.stopped;
		},
		get done() {
			return runs.describingState.done;
		},
		get left() {
			return runs.describingState.left;
		},
		get fraction() {
			return runs.describingState.fraction;
		},
		get remaining() {
			return runs.describingState.remaining;
		},
		get outcome() {
			return runs.describingState.outcome;
		},
		attach: async () => {},
		detach: () => {},
		follow: () => {
			runs.describingState.running = true;
			runs.describingState.fraction = 0;
			runs.describingState.outcome = null;
		},
		couldNotStart: (why: string) => {
			runs.describingState.running = false;
			runs.describingState.outcome = why;
		}
	}
}));

const SETTINGS: SettingSection[] = [
	{
		name: 'Smart Search',
		settings: [
			{
				key: 'semantic.enabled',
				value: false,
				label: 'Search by what a picture looks like',
				help: 'Off until you turn it on.',
				scope: 'app'
			},
			{
				key: 'semantic.model',
				value: 'base',
				label: 'Which models',
				choices: ['base', 'large'],
				scope: 'app'
			},
			{
				key: 'semantic.device',
				value: 'cpu',
				label: 'What to run them on',
				choices: ['cpu'],
				scope: 'app'
			}
		]
	}
];

function status(over: Partial<SemanticStatus> = {}): SemanticStatus {
	return {
		supported: true,
		enabled: false,
		ready: false,
		family: 'base',
		device: 'cpu',
		indexed_frames: 0,
		described_files: 0,
		waiting_files: 0,
		unread_files: 0,
		running_jobs: 0,
		described_by_another_model: 0,
		problem: null,
		installed: [],
		...over
	};
}

let host: HTMLElement;

/* The watchers wait on real timers, so the clock is this test's to move. Without it every
   assertion about a progress bar would have to sit through two seconds of nothing per tick. */
beforeEach(() => {
	vi.useFakeTimers();
	Object.assign(runs.fetching, {
		jobId: null,
		fraction: 0,
		note: null,
		status: 'Waiting to start',
		outcome: null
	});
	Object.assign(runs.describingState, {
		status: null,
		outcome: null,
		done: 0,
		left: 0,
		fraction: null,
		remaining: null,
		running: false,
		stopped: false
	});
});

afterEach(() => {
	vi.useRealTimers();
	host?.remove();
	vi.clearAllMocks();
	/* The open sub-page is MODULE state, so it outlives the host it was drawn in. */
	drilldown.close();
});

/** Let the watcher's next tick happen, and everything it awaits after it. */
async function tick() {
	await vi.advanceTimersByTimeAsync(2000);
	for (let turn = 0; turn < 6; turn += 1) await Promise.resolve();
	flushSync();
}

async function render(state: SemanticStatus, enabled = false) {
	const sections: SettingSection[] = JSON.parse(JSON.stringify(SETTINGS));
	sections[0].settings![0].value = enabled;
	fetchSettings.mockResolvedValue(sections);
	saveSettings.mockResolvedValue(undefined);
	semanticStatus.mockResolvedValue(state);
	host = document.createElement('div');
	document.body.append(host);
	mount(Semantic, { target: host });
	// The page behind More settings is drawn by the settings frame, so it is mounted beside the
	// pane exactly as the frame mounts it. See `DrilldownPage`.
	mount(DrilldownPage, { target: host, props: { behind: 'Smart Search' } });
	await vi.waitFor(() => expect(semanticStatus).toHaveBeenCalled(), { interval: 1 });
	await Promise.resolve();
	await Promise.resolve();
	flushSync();
	return host;
}

function toggle(): HTMLElement | null {
	return host.querySelector('[role="switch"]');
}

/* The row saying where the switch is: Smart Search is turned on and off under Importing. */
function pointer(): HTMLElement | null {
	return host.querySelector<HTMLElement>('[id="semantic.enabled"]');
}

function stateOf(row: HTMLElement | null): string {
	return row?.querySelector('.state')?.textContent?.trim() ?? '';
}

function button(label: string): HTMLButtonElement | undefined {
	return [...host.querySelectorAll('button')].find((each) => wordsOn(each) === label);
}

/** Open the More settings page, where the models and the device are. */
function openMore(): void {
	host.querySelector<HTMLButtonElement>('[id="semantic.more"] button')?.click();
	flushSync();
}

/** The task's own When row, by the address it carries. */
function whenRow(): Element | null {
	return host.querySelector('[id="tasks.smart-search.when"]');
}

it('offers no switch at all on a machine that cannot hold the index', async () => {
	await render(
		status({
			supported: false,
			problem: 'Search by meaning needs an add-on this copy cannot load.'
		})
	);

	expect(toggle()).toBeNull();
	expect(host.textContent).toContain('cannot load');
});

it('draws nothing but where the switch is until somebody turns it on', async () => {
	await render(status());

	expect(stateOf(pointer())).toBe('Off');
	expect(button('Download the models')).toBeUndefined();
	expect(whenRow()).toBeNull();
	// Off is a status, not a blank page.
	expect(host.textContent).toContain('Turned off.');
});

it('says the models are not here yet rather than reading as broken', async () => {
	await render(
		status({
			enabled: true,
			ready: false,
			problem: 'The models have not been obtained yet.'
		}),
		true
	);

	expect(host.textContent).toContain('The models have not been obtained yet.');
	expect(button('Download the models')).toBeDefined();
});

it('says how much of the library has been described', async () => {
	await render(status({ enabled: true, ready: true, described_files: 7, waiting_files: 3 }), true);

	expect(host.textContent).toContain(
		'Ready. Running on the CPU. 7 files described, 3 not described yet.'
	);
});

it('says how many files the previous model described and that search leaves them out', async () => {
	await render(
		status({
			enabled: true,
			ready: true,
			described_files: 7,
			waiting_files: 3,
			described_by_another_model: 2
		}),
		true
	);

	// The sentence wraps across template lines, so the text is read with its whitespace folded.
	expect(host.textContent?.replace(/\s+/g, ' ')).toContain(
		'2 of those were described by the previous model and are left out of Smart Search'
	);
});

it('does not offer to remove an index that does not exist', async () => {
	await render(status({ enabled: true, ready: true }), true);

	expect(button('Delete index')).toBeUndefined();
});

it('answers a link to Delete index before anything is described by saying why it is not there', async () => {
	await render(status({ enabled: true, ready: true }), true);
	host.className = 'section-body';
	const { toasts } = await import('$lib/shell/toasts.svelte');
	const { revealSetting, NOTHING_TO_DELETE } = await import('./settings-anchor.svelte');
	const said = vi.spyOn(toasts, 'show');
	const scrolled = Element.prototype.scrollIntoView;
	Element.prototype.scrollIntoView = vi.fn();
	try {
		await expect(revealSetting('semantic.forget')).resolves.toBe(true);
		expect(said).toHaveBeenCalledWith(NOTHING_TO_DELETE);
	} finally {
		Element.prototype.scrollIntoView = scrolled;
		said.mockRestore();
	}
});

it('offers to remove the index once something has been described', async () => {
	await render(status({ enabled: true, ready: true, indexed_frames: 40 }), true);

	expect(button('Delete index')).toBeDefined();
});

it('draws no switch of its own: it says whether it is on and links to the one under Import tasks', async () => {
	/* One setting, one door. The switch is under Import tasks with the other recognition switches;
	   a second one here would be two controls for one answer. */
	await render(status({ enabled: true, ready: true, indexed_frames: 40 }), true);

	expect(toggle()).toBeNull();
	expect(stateOf(pointer())).toBe('On');
	const link = pointer()?.querySelector('a');
	expect(link?.textContent?.trim()).toBe('Change in Identify settings');
	expect(link?.getAttribute('href')).toContain('tasks');
	expect(saveSettings).not.toHaveBeenCalled();
});

it('starts the download rather than fetching the models itself', async () => {
	await render(status({ enabled: true, ready: false, problem: 'Not yet.' }), true);

	button('Download the models')!.click();
	await vi.waitFor(() => expect(fetchSemanticModels).toHaveBeenCalledTimes(1));
});

it("describing the library is the task's own When row, not a button of the pane's", async () => {
	/* Describing the library is the Smart Search task's row, the same row Tasks draws, so there
	   is one Run now and it is the same everywhere, not a button here starting the Build for
	   one product. */
	await render(status({ enabled: true, ready: true }), true);

	expect(whenRow()).not.toBeNull();
	expect(button('Run now')).toBeUndefined();
	expect(button('Resume')).toBeUndefined();
});

/* --- progress, and saying which way it ended ---------------------------------------------------
 *
 * Both buttons here start a job that takes minutes. A toast pointing at a different screen, and a
 * button that goes back to looking exactly as it did before it was pressed, would leave no way,
 * from this screen, to find out whether a few hundred megabytes had arrived, which is the one
 * question somebody presses that button to answer.
 *
 * What is asserted here is the MARKUP: which of the three states each section draws, and what it
 * says in each. The following of a run happens in `$lib/jobs/semantic-runs` and is stood in for above,
 * so the stand-in is set BEFORE the pane is drawn rather than mutated underneath it. A plain
 * object is not reactive, and a test that mutated one and expected the screen to move would be
 * asserting against Svelte rather than against this component.
 *
 * The stores' own behaviour is tested in `semantic-runs.svelte.test.ts`.
 */

it('shows how far the download has got, where the button was', async () => {
	runs.fetching.jobId = 'job-1';
	runs.fetching.fraction = 0.42;
	runs.fetching.status = '42% \u2014 downloading';
	await render(status({ enabled: true, ready: false, problem: 'Not yet.' }), true);

	expect(host.querySelector('[role="progressbar"]')).not.toBeNull();
	expect(host.textContent).toContain('42%');
	// And the button is gone while it runs, so it cannot be pressed a second time.
	expect(button('Download the models')).toBeUndefined();
});

it('says a download is waiting when nothing has picked it up yet', async () => {
	/* A download queued behind other jobs must not draw "0% - downloading" for minutes, which
	   reads as a stuck download rather than a queue. The bar stays (the button
	   must not come back, or somebody starts a second one), and the words say which of the two
	   it is. */
	runs.fetching.jobId = 'job-1';
	await render(status({ enabled: true, ready: false, problem: 'Not yet.' }), true);

	expect(host.querySelector('[role="progressbar"]')).not.toBeNull();
	expect(host.textContent).toContain('Waiting to start');
	expect(host.textContent).not.toContain('downloading');
	expect(button('Download the models')).toBeUndefined();
});

it('says the models arrived, rather than leaving the bar where it stopped', async () => {
	runs.fetching.outcome = 'The models are on this machine. Sift can search by meaning now.';
	await render(status({ enabled: true, ready: true }), true);

	expect(host.textContent).toContain('The models are on this machine.');
	expect(host.querySelector('[role="progressbar"]')).toBeNull();
});

it('offers to fetch them AGAIN once they are already here, and says why you would', async () => {
	/* "Download the models" over models that are on the disk is an instruction to somebody who has
	   already followed it. */
	await render(status({ enabled: true, ready: true }), true);
	openMore();

	expect(button('Download the models')).toBeUndefined();
	expect(button('Download')?.getAttribute('aria-label')).toBe('Download the models again');
	expect(host.textContent).toContain('Download them again only if a file is damaged');
});

it('asks for a forced fetch only when the files are already here', async () => {
	/* The difference is the whole of the second press: without it every file is skipped, nothing is
	   downloaded, and the job reports success. */
	await render(status({ enabled: true, ready: true }), true);
	openMore();

	button('Download')!.click();
	await vi.waitFor(() => expect(fetchSemanticModels).toHaveBeenCalledWith(true), { interval: 1 });
});

it('counts files described rather than files queued, and says how long is left', async () => {
	/* The pass only QUEUES: it finishes in seconds while the reading runs for hours, so a bar
	   following the job would read "200 queued so far" and then report itself done. */
	Object.assign(runs.describingState, {
		running: true,
		done: 40,
		left: 60,
		fraction: 0.4,
		remaining: 600,
		status: {}
	});
	await render(status({ enabled: true, ready: true }), true);

	expect(host.querySelector('[role="progressbar"]')).not.toBeNull();
	expect(host.textContent).toContain('40 of 100');
	expect(host.textContent).toContain('about 10 minutes left');
	expect(host.textContent).not.toContain('queued');
});

it('does not report a failure to start as work in progress', async () => {
	runs.describingState.outcome = 'That could not be started.';
	await render(status({ enabled: true, ready: true }), true);

	expect(host.querySelector('[role="progressbar"]')).toBeNull();
	expect(host.textContent).toContain('That could not be started.');
});

it('names which of the set is arriving, rather than only a percentage', async () => {
	/* Three separate downloads of wildly different sizes, so the fraction alone runs to one and back
	   to zero three times, which reads as a download that keeps failing and starting again. */
	runs.fetching.jobId = 'job-1';
	runs.fetching.fraction = 0.5;
	runs.fetching.note = '2 of 3 - word reader';
	runs.fetching.status = '50% \u2014 2 of 3 - word reader';
	await render(status({ enabled: true, ready: false, problem: 'Not yet.' }), true);

	expect(host.textContent).toContain('2 of 3 - word reader');
});

it('falls back to a plain word when the job has nothing to say yet', async () => {
	runs.fetching.jobId = 'job-1';
	runs.fetching.fraction = 0.2;
	runs.fetching.status = '20% \u2014 downloading';
	await render(status({ enabled: true, ready: false, problem: 'Not yet.' }), true);

	expect(host.textContent).toContain('downloading');
});

it('says a run stopped rather than drawing a bar that will never move again', async () => {
	/* Switching the feature off does not cancel what it has already queued: every one of those jobs
	   runs, finds the switch off, and finishes having done nothing. Work left with nothing doing it
	   is the state that leaves behind, and it is identical to a run in progress from the counts. */
	Object.assign(runs.describingState, {
		running: false,
		stopped: true,
		done: 40,
		left: 60,
		status: {}
	});
	await render(status({ enabled: true, ready: true }), true);

	expect(host.querySelector('[role="progressbar"]')).toBeNull();
	expect(host.textContent).toContain('60 files were not described');
	// And the way out is named: the task's Run now, on Tasks, where every press lives.
	expect(host.textContent?.replace(/\s+/g, ' ')).toContain(
		'To continue where it stopped, press Run now on its row in Tasks.'
	);
	expect(host.querySelector('a[href="/settings/tasks#tasks.smart-search.when"]')).not.toBeNull();
	expect(whenRow()).not.toBeNull();
});

it('says nothing about a stopped run when none was interrupted', async () => {
	Object.assign(runs.describingState, { running: false, stopped: false, status: {} });
	await render(status({ enabled: true, ready: true }), true);

	expect(host.textContent).not.toContain('were not described');
});

/*
 * The stand-in above answers every field this component reads.
 *
 * A plain object standing in for a store is invisible to the type checker (a `vi.mock` factory is
 * never compared against the module it replaces), so a field the component starts reading comes
 * back `undefined` and the branch behind it is simply never drawn. Every test still passes, and the
 * pane looks covered.
 *
 * Derived from the component rather than listed, so a field added tomorrow is covered by having
 * been written rather than by somebody remembering this.
 */
it('the stand-in answers every field the pane reads off the two runs', async () => {
	const source = readFileSync('src/lib/settings-ui/Semantic.svelte', 'utf8');
	// The stand-in itself, as the component receives it, not the plain object behind it, which a
	// reset can quietly put a field back onto.
	const stood = await import('$lib/jobs/semantic-runs.svelte');
	const answered: Record<string, Set<string>> = {
		modelFetch: new Set(Object.keys(stood.modelFetch)),
		describing: new Set(Object.keys(stood.describing))
	};

	const missing: string[] = [];
	for (const [, store, field] of source.matchAll(/\b(modelFetch|describing)\.([A-Za-z_]+)/g)) {
		if (!answered[store].has(field)) missing.push(`${store}.${field}`);
	}

	expect(missing, 'the pane reads fields the stand-in has no answer for').toEqual([]);
});

/* The pane follows the bus.
 *
 * The same account in a second window, or another admin turning an installation-wide switch: the
 * server rings the bell, and a pane that only read on mount would go on showing the value from
 * before. On the desktop app there is no page load to put it right, so it would stay wrong until
 * a restart.
 */
it('re-reads when a setting moves somewhere else', async () => {
	await render(status({ supported: true }), false);
	expect(stateOf(pointer())).toBe('Off');

	const moved: SettingSection[] = JSON.parse(JSON.stringify(SETTINGS));
	moved[0].settings![0].value = true;
	fetchSettings.mockResolvedValue(moved);

	settingChanges.changed();
	flushSync();
	for (let turn = 0; turn < 8; turn += 1) await Promise.resolve();
	flushSync();

	expect(stateOf(pointer())).toBe('On');
});
