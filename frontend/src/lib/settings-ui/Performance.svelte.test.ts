import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount } from 'svelte';
import type { SettingSection } from '$lib/settings-ui/settings';
import type { ServerHealth } from '$lib/shell/health';
import type { Recommendation, SelfTest } from '$lib/shell/selftest';
import Performance from './Performance.svelte';
import DrilldownPage from './DrilldownPage.svelte';
import { COPY } from './Performance.search';
import { drilldown } from './drilldown.svelte';
import { readFileSync } from 'node:fs';
import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import paneSource from './SettingsPane.svelte?raw';

/* The Performance screen: the device, the benchmark, whether Sift is keeping up, and the figures
 * for a bug report. Its own settings are Concurrency's page and the step back;
 * the other settings it reads are what the device readout says each feature runs on. */

const fetchSettings = vi.fn<() => Promise<SettingSection[]>>();
const saveSettings = vi.fn<(values: Record<string, unknown>) => Promise<void>>();
const fetchHealth = vi.fn<() => Promise<ServerHealth | null>>();
const fetchSelfTest = vi.fn<() => Promise<SelfTest>>();
const startSelfTest = vi.fn<() => Promise<SelfTest>>();

vi.mock('$lib/settings-ui/settings', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/settings-ui/settings')>()),
	fetchSettings: () => fetchSettings(),
	saveSettings: (values: Record<string, unknown>) => saveSettings(values),
	// A rating is drawn wherever this reaches, and the rating scale registers its watcher at
	// module scope. A partial mock without this fails the suite at import.
	onSettingsSaved: vi.fn()
}));

vi.mock('$lib/shell/health', () => ({
	fetchHealth: () => fetchHealth()
}));

/* The machine probe, which is its own admin-only route rather than part of the settings. Only
   `get` is replaced: everything else in the client, `ApiError` included, stays real. */
const machine = vi.fn<(url?: string) => Promise<unknown>>();
vi.mock('$lib/api/client', async (importOriginal) => {
	const actual = await importOriginal<typeof import('$lib/api/client')>();
	return { ...actual, api: { ...actual.api, get: (url: string) => machine(url) } };
});

/* The shell, which is the only thing that can say what computer this window is on. Answers null by
   default: the machine running the library, where there is nothing to add. */
const localHardware = vi.hoisted(() => vi.fn(async () => null as unknown));
/* Whether this page is inside the desktop application at all. `localHardware` answering null is
   true both on the machine running the library and in a browser, and those two want different
   words: a browser is not a machine. */
const isDesktop = vi.hoisted(() => vi.fn(() => true));

vi.mock('$lib/bridge', () => ({
	bridge: { localHardware, isDesktop }
}));

vi.mock('$lib/shell/selftest', () => ({
	fetchSelfTest: () => fetchSelfTest(),
	startSelfTest: () => startSelfTest(),
	POLL_MS: 1
}));

const PERFORMANCE: SettingSection = {
	name: 'Performance',
	settings: [
		{
			key: 'performance.worker_count',
			value: 0,
			label: 'How many jobs run at the same time',
			help: '0 lets Sift choose.',
			minimum: 0,
			maximum: 64,
			scope: 'app'
		},
		/* Recognition's budget: a CHOICE, and a number drawn only while the choice is `share`. */
		{
			key: 'faces.machine_budget',
			value: 'share',
			label: 'When recognition may work',
			help: 'How much of this machine recognition may use, and when.',
			choices: ['share', 'idle', 'nightly', 'threads'],
			choice_labels: ['Share', 'Idle', 'Overnight', 'Fixed threads'],
			scope: 'app'
		},
		{
			key: 'faces.core_share',
			value: 50,
			label: 'Share of the machine to use',
			help: 'Taking the whole machine makes everything else on it feel broken.',
			minimum: 10,
			maximum: 100,
			unit: '%',
			scope: 'app'
		},
		/* Sent by an older server. Never drawn here, in any mode: quiet hours are on Tasks, and
		   the pane links there. */
		{
			key: 'faces.nightly_from',
			value: '23:00',
			label: 'Start at',
			help: 'Twenty-four hour clock.',
			scope: 'app'
		}
	]
};

/* A machine with a card in it. The readout is the only thing that reads this, and what it says
   about the two expensive features comes from their SETTINGS, not from here. */
const MACHINE = {
	cpu_model: 'A processor',
	cpu_count: 8,
	total_ram_bytes: 17_179_869_184,
	cuda: true,
	rocm: false,
	gpu_name: 'A graphics card',
	gpu_cards: [{ name: 'A graphics card', vram_bytes: null, can_compute: true }],
	gpu_driver: null,
	transcode_encoders: [],
	warnings: []
};

/* THE TWO DEVICE SETTINGS LIVE IN OTHER SECTIONS, and that is the whole point of these fixtures.
   The readout is on the Performance screen; the settings behind it belong to Smart Search and to
   Identify. Reading them out of the Performance section is exactly the bug these catch. */
const SMART_SEARCH: SettingSection = {
	name: 'Smart Search',
	settings: [
		{ key: 'semantic.device', value: 'nvidia', label: 'Run descriptions on', scope: 'app' }
	]
};
const IDENTIFY: SettingSection = {
	name: 'Identify',
	settings: [{ key: 'faces.device', value: 'cpu', label: 'Run recognition on', scope: 'app' }]
};

let host: HTMLElement;

async function render(sections: SettingSection[] = [PERFORMANCE]) {
	fetchSettings.mockResolvedValue(sections);
	saveSettings.mockResolvedValue(undefined);
	host = document.createElement('div');
	document.body.append(host);
	mount(Performance, { target: host });
	/* The sub-page is mounted beside the pane, exactly as the settings shell mounts it: what Sift
	   builds ahead is a `PresetGroup`, and its rows are drawn by the SHELL rather than by the pane.
	   See `DrilldownPage`. */
	mount(DrilldownPage, { target: host, props: { behind: 'Performance' } });
	/* onMount kicks off an async load: let the settings read land and the DOM settle. A task, not
	   a microtask, so every answer already resolved has been handed on. */
	await vi.waitFor(() => expect(fetchSettings).toHaveBeenCalled());
	await new Promise((settled) => setTimeout(settled, 0));
	flushSync();
}

/* A machine that will not describe itself, which is the ordinary case for every test here but
   the two about the readout. The component treats a failed check as "say nothing about the
   machine", so this leaves the rest of the screen exactly as it was. */
beforeEach(() => {
	machine.mockRejectedValue(new Error('nothing is answering'));
});

afterEach(() => {
	/* The open sub-page is MODULE state, so it outlives the host it was drawn in and the next test
	   would start with the last one's page already up. */
	drilldown.close();
	machine.mockReset();
	fetchSettings.mockReset();
	saveSettings.mockReset();
	fetchHealth.mockReset();
	fetchSelfTest.mockReset();
	startSelfTest.mockReset();
	host?.remove();
});

describe('the performance screen', () => {
	it('draws how much Sift does at the same time as one row, its numbers on a page behind its Edit', async () => {
		/* Concurrency is beside the step back that lowers it, not beside the stages on Importing.
		   The pane itself draws no number: they are on the page its Edit opens. */
		await render();

		const row = host.querySelector('[id="performance.concurrency"]');
		expect(row?.textContent).toContain('Concurrency');
		expect(host.querySelector('[id="performance.worker_count"]')).toBeNull();
		expect(host.textContent).not.toContain('Offer to measure');

		row?.querySelector<HTMLButtonElement>('button[aria-label="Edit concurrency"]')?.click();
		flushSync();
		await vi.waitFor(() =>
			expect(host.querySelector('[id="performance.worker_count"]')).not.toBeNull()
		);
		expect(drilldown.title).toBe('Concurrency');
		// The budget's own row, and the number that belongs to its answer; never quiet hours' times.
		expect(host.querySelector('[id="faces.machine_budget"]')).not.toBeNull();
		expect(host.querySelector('[id="faces.core_share"]')).not.toBeNull();
		expect(host.querySelector('[id="faces.nightly_from"]')).toBeNull();
		expect(host.querySelector('[data-testid="quiet-hours-link"]')).toBeNull();
	});

	it('opens Concurrency for a deep link to any of its numbers', async () => {
		await render();
		for (const key of ['performance.worker_count', 'faces.machine_budget', 'faces.thread_count']) {
			drilldown.close();
			flushSync();
			expect(drilldown.reveal(key), key).toBe(true);
			flushSync();
			expect(drilldown.title, key).toBe('Concurrency');
		}
	});

	it('points the quiet-hours budget at the one range on Tasks', async () => {
		const nightly: SettingSection = {
			...PERFORMANCE,
			settings: PERFORMANCE.settings.map((one) =>
				one.key === 'faces.machine_budget' ? { ...one, value: 'nightly' } : one
			)
		};
		await render([nightly]);
		expect(drilldown.reveal('faces.machine_budget')).toBe(true);
		flushSync();
		await vi.waitFor(() =>
			expect(host.querySelector('[data-testid="quiet-hours-link"] a')).not.toBeNull()
		);
		expect(host.querySelector('[id="faces.core_share"]')).toBeNull();
		expect(host.querySelector('[data-testid="quiet-hours-link"] a')?.getAttribute('href')).toMatch(
			/#tasks\.quiet-hours$/
		);
	});

	it('draws the one setting it owns: stepping back while the computer is in use', async () => {
		/* Filed under Performance by the registry and drawn here, on by default. Turning it off
		   is written immediately, like every row on a settings pane. */
		const stepBack: SettingSection = {
			name: 'Performance',
			settings: [
				{
					key: 'performance.step_back_while_used',
					value: true,
					label: "Use less system resources while you're working",
					help: 'While you are typing or moving the mouse, Sift runs half as many tasks.',
					scope: 'app'
				}
			]
		};
		await render([stepBack]);

		expect(host.textContent).toContain("Use less system resources while you're working");
		const toggle = host.querySelector<HTMLElement>('[role="switch"]');
		expect(toggle?.getAttribute('aria-checked')).toBe('true');
		toggle!.click();
		await vi.waitFor(() =>
			expect(saveSettings).toHaveBeenCalledWith({ 'performance.step_back_while_used': false })
		);
	});

	it('draws the share of this device the step back keeps to, beside its switch', async () => {
		const stepBack: SettingSection = {
			name: 'Performance',
			settings: [
				{
					key: 'performance.step_back_while_used',
					value: true,
					label: "Use less system resources while you're working",
					help: 'While you are typing or moving the mouse, Sift uses a share of this device.',
					scope: 'app'
				},
				{
					key: 'performance.step_back_share',
					value: 25,
					label: 'System resource usage in eco mode',
					help: 'How much of this device background tasks use.',
					minimum: 10,
					maximum: 100,
					unit: '%',
					scope: 'app'
				}
			]
		};
		await render([stepBack]);

		expect(host.textContent).toContain('System resource usage in eco mode');
		expect(host.querySelector('[id="performance.step_back_share"]')).not.toBeNull();
	});

	it('reads what each feature runs on from ITS OWN SECTION, not from this one', async () => {
		/*
		 * THE FAULT THIS GUARDS, in one sentence: the table saying "CPU" on a machine set to use
		 * the card, because the two settings were looked up in the Performance section and they are
		 * not in it.
		 *
		 * `faces.device` belongs to Identify and `semantic.device` to Smart Search. Both lookups
		 * would miss, both fall to the "not nvidia" side of the test, and the readout report the
		 * processor with complete confidence, to whoever had just changed the setting, a change
		 * that had not saved. So the fixture puts them in other sections and gives them DIFFERENT
		 * values: one right answer cannot cover for the other.
		 */
		machine.mockResolvedValue(MACHINE);
		await render([PERFORMANCE, SMART_SEARCH, IDENTIFY]);

		const rows = new Map(
			[...host.querySelectorAll('.readings > div')].map((row) => [
				row.querySelector('dt')?.textContent?.trim(),
				row.querySelector('dd')?.textContent?.trim()
			])
		);
		expect(rows.get('Smart Search')).toBe('GPU');
		expect(rows.get('Recognizing faces')).toBe('CPU');
	});

	it('draws each fact as one row with one unbroken line, in the shape of every fact row', async () => {
		/* The line is the ROW's, so it runs under the name and the value alike; a line per cell
		   would leave a gap between the two halves of every fact. */
		machine.mockResolvedValue(MACHINE);
		await render([PERFORMANCE]);
		const rows = [...host.querySelectorAll<HTMLElement>('.readings > div')];
		expect(rows.length).toBeGreaterThan(1);
		applyStyles(readFileSync('src/lib/settings-ui/Performance.svelte', 'utf8'), rows[0]);
		try {
			// The unit environment keeps a logical border as written, so it is read that way.
			const line = (element: Element) =>
				/\bsolid\b/.test(getComputedStyle(element).getPropertyValue('border-block-start'));
			const second = getComputedStyle(rows[1]);
			expect(line(rows[1]), 'the row draws no line').toBe(true);
			expect(line(rows[0]), 'the first fact draws a line').toBe(false);
			const name = rows[1].querySelector('dt')!;
			const value = rows[1].querySelector('dd')!;
			expect(line(name), 'the name draws its own line').toBe(false);
			expect(line(value), 'the value draws its own line').toBe(false);
			expect(getComputedStyle(name).fontWeight).toBe('600');
			expect(getComputedStyle(value).textAlign).toBe('end');
			expect(second.display).toBe('grid');
		} finally {
			removeStyles();
		}
	});

	it('says nothing at all about a feature whose setting it cannot see', async () => {
		/* A missing key must not come back as "CPU" just because it is not the string `nvidia`.
		   A section this account may not see, a request that half-failed, a key renamed: every
		   one of them would be a confident, wrong reading of the machine. A row that is not
		   drawn is honest; a row that is wrong is not. */
		machine.mockResolvedValue(MACHINE);
		await render([PERFORMANCE]);

		const names = [...host.querySelectorAll('.readings > div dt')].map((one) =>
			one.textContent?.trim()
		);
		expect(names).not.toContain('Smart Search');
		expect(names).not.toContain('Recognizing faces');
		// The one that is read off the machine rather than off a setting is still there.
		expect(names).toContain('Converting video');
	});
});

/* IS SIFT KEEPING UP: one sentence, and the four instruments behind Details.
 *
 * What these prove: the sentence says the present before the past; it names the reading that was
 * slow in that reading's own words; a quiet machine reads as yes; a reading an older server does
 * not send is left out rather than drawn as zero; and the two diagnosis tables are behind the
 * fold with a Copy button, in the order the server ranked them.
 */

const QUIET: ServerHealth = {
	loop: { worstLagSeconds: 0.04, heldCount: 0 },
	threads: { worstWaitSeconds: 0.01, fullCount: 0, waitingSeconds: 0 },
	database: {
		worstWaitSeconds: 0.01,
		fullCount: 0,
		waitingSeconds: 0,
		readers: 16,
		pointReadsInline: true,
		pointReadMicroseconds: 2,
		sweeping: null
	},
	queue: { worstSeconds: 0.01, latestSeconds: 0, overCount: 0 },
	work: [],
	widest: []
};

async function show(health: ServerHealth | null) {
	fetchHealth.mockResolvedValue(health);
	await render();
	if (health) {
		await vi.waitFor(() => expect(host.querySelector('[data-testid="keeping-up"]')).not.toBeNull());
	}
	flushSync();
}

function block(): HTMLElement | null {
	return host.querySelector('[data-testid="keeping-up"]');
}

function verdict(): string {
	return host.querySelector('[data-testid="keeping-up-verdict"]')?.textContent?.trim() ?? '';
}

function row(kind: string): HTMLElement | null {
	return host.querySelector(`[data-testid="keeping-up-readings"] tr[data-kind="${kind}"]`);
}

describe('is Sift keeping up', () => {
	it('is absent for anyone the server does not answer', async () => {
		// A guest gets no reading at all, so there is nothing to draw, not a zero, which would
		// read as "measured, and fine".
		await show(null);

		expect(block()).toBeNull();
	});

	it('says yes for a device that has never had to wait', async () => {
		await show(QUIET);

		expect(block()?.querySelector('h2')?.textContent).toContain('Is Sift keeping up?');
		expect(verdict()).toBe('Yes. Sift has answered straight away since it started.');
		expect(block()?.querySelectorAll('.verdict.bad')).toHaveLength(0);
	});

	it('names the worst reading since start, in the words of the thing that was slow', async () => {
		await show({
			...QUIET,
			loop: { worstLagSeconds: 1.78, heldCount: 4 },
			threads: { worstWaitSeconds: 0.6, fullCount: 9, waitingSeconds: 0 }
		});

		expect(verdict()).toBe(
			'Not always. Since it started, Sift stopped responding 4 times; the longest pause was 1.8 seconds.'
		);
		// History, not a fault happening now: the sentence is not marked.
		expect(block()?.querySelectorAll('.verdict.bad')).toHaveLength(0);
	});

	it("leaves the benchmark's own stalls out of the count and says how many it caused", async () => {
		fetchSelfTest.mockResolvedValue({ ...MEASURED, held_while_measuring: 21 });
		await show({ ...QUIET, loop: { worstLagSeconds: 3.6, heldCount: 52 } });
		await vi.waitFor(() => expect(verdict()).toContain('Not counted'));

		expect(verdict()).toBe(
			'Not always. Since it started, Sift stopped responding 31 times; the longest pause was 3.6 seconds. ' +
				'Not counted: 21 times the benchmark pushed Sift until it fell behind, which is how it measures.'
		);
	});

	it('picks the longest wait, not the most frequent', async () => {
		await show({
			...QUIET,
			loop: { worstLagSeconds: 0.3, heldCount: 40 },
			database: { ...QUIET.database!, worstWaitSeconds: 8.4, fullCount: 1 }
		});

		expect(verdict()).toContain('screens waited for the database 1 time');
		expect(verdict()).toContain('8.4 seconds');
	});

	it('puts what is happening NOW ahead of anything in the past', async () => {
		await show({
			...QUIET,
			loop: { worstLagSeconds: 9, heldCount: 3 },
			threads: { worstWaitSeconds: 2.0, fullCount: 9, waitingSeconds: 1.9 }
		});

		expect(verdict()).toBe(
			'Not right now. Video and file work has been waiting 1.9 seconds for a free worker, so video may stutter.'
		);
		expect(block()?.querySelectorAll('.verdict.bad')).toHaveLength(1);
		expect(block()?.textContent).toContain('lower the numbers under Concurrency.');
	});

	it('says a backed-up queue is slow screens, right now', async () => {
		await show({ ...QUIET, queue: { worstSeconds: 9.7, latestSeconds: 2.4, overCount: 51 } });

		expect(verdict()).toBe(
			'Not right now. Sift is 2.4 seconds behind with its work, so screens are slow to load.'
		);
	});

	it('draws one row per reading behind Details, each with its longest, times and now', async () => {
		await show({
			...QUIET,
			threads: { worstWaitSeconds: 1.4, fullCount: 6, waitingSeconds: 0 }
		});

		const details = host.querySelector('[id="performance.details"]');
		expect(details?.tagName).toBe('DETAILS');
		// The one fold, not a disclosure drawn by hand.
		expect(details?.classList.contains('fold')).toBe(true);
		expect(details?.querySelector('summary')?.textContent).toBe('The readings behind this answer');
		const cells = [...(row('threads')?.querySelectorAll('th, td') ?? [])].map((one) =>
			one.textContent?.trim()
		);
		expect(cells).toEqual(['Video and file work', '1.4 seconds', '6', 'clear']);
		// Two marked, not three: nothing is waiting at this moment.
		expect(row('threads')?.querySelectorAll('td.bad')).toHaveLength(2);
		// The app's own heartbeat has no live reading: a held loop cannot report until it is free.
		expect(row('loop')?.querySelectorAll('td')[2]?.textContent?.trim()).toBe('');
	});

	it('leaves out a reading an older server does not send, rather than drawing it as zero', async () => {
		await show({ ...QUIET, threads: null, database: null, queue: null });

		expect(row('loop')).not.toBeNull();
		expect(row('threads')).toBeNull();
		expect(row('database')).toBeNull();
		expect(row('queue')).toBeNull();
		expect(verdict()).toContain('Yes.');
	});

	it('says the database is on a slow disk when that is what the start-up reading found', async () => {
		await show({
			...QUIET,
			database: { ...QUIET.database!, pointReadsInline: false, pointReadMicroseconds: 401.5 }
		});

		const said = host.querySelector('[data-testid="point-reads-verdict"]')?.textContent ?? '';
		expect(said).toContain('402');
		expect(said).toContain('local disk');
	});

	it('names the pass reading the whole library, and how many connections screens share', async () => {
		await show({
			...QUIET,
			database: { ...QUIET.database!, sweeping: 'duplicate fingerprints' }
		});

		expect(host.querySelector('[data-testid="sweeping"]')?.textContent).toContain(
			'duplicate fingerprints'
		);
		expect(block()?.textContent).toContain('Screens share 16 database connections');
	});
});

/* Measuring the machine. The panel is the only place in Sift that produces a number rather than
 * taking one, so what matters here is that it never quietly becomes a number-writer: a
 * recommendation is shown, and applying it is a thing somebody does. */

const MEASURED: SelfTest = {
	running: false,
	rounds: 5,
	step: null,
	seconds_left: null,
	left_timed: false,
	whole_seconds: 299,
	whole_timed: false,
	finished: true,
	measured: true,
	progress: null,
	share_reads_now: 0,
	whole_to_come: false,
	whole_due: false,
	held_while_measuring: 0,
	full_while_measuring: 0,
	notes: [],
	card: null,
	models: [],
	measurement: {
		cores: 8,
		levels: [{ at_once: 2, seconds: 4, finished: 2, per_second: 0.5, responsive: true }],
		failed: null,
		storages: [],
		decode: null
	},
	recommendations: [
		{
			key: 'performance.generation_limit',
			label: 'How many previews are built at the same time',
			current: 0,
			suggested: 2,
			reason: 'Measured directly.',
			changes_anything: true
		}
	]
};

describe('testing this machine', () => {
	type Said = Omit<SelfTest, 'measurement' | 'progress'> &
		Partial<Pick<SelfTest, 'measurement' | 'progress'>>;
	async function withTest(state: Said | null) {
		fetchHealth.mockResolvedValue(null);
		if (state === null) fetchSelfTest.mockRejectedValue(new Error('not an admin'));
		else fetchSelfTest.mockResolvedValue({ measurement: null, progress: null, ...state });
		await render();
		flushSync();
		await vi.waitFor(() => expect(fetchSelfTest).toHaveBeenCalled());
		flushSync();
	}

	/*
	 * The apply button, found by what it SAYS rather than by a class.
	 *
	 * The control is the shared `Button`, which brings its own look and deliberately does not take
	 * a caller's class for styling. Reading the label is the better test anyway: it is what a
	 * person looks for, so this fails if the button stops being findable rather than only if its
	 * markup changes.
	 */
	function applyButton(): HTMLButtonElement | null {
		const buttons = [...(panel()?.querySelectorAll('button') ?? [])] as HTMLButtonElement[];
		return buttons.find((one) => /Apply th|Applying/.test(one.textContent ?? '')) ?? null;
	}

	function panel(): HTMLElement | null {
		return host.querySelector('[data-testid="self-test"]');
	}

	it('is absent for anyone the server will not answer', async () => {
		await withTest(null);

		expect(panel()).toBeNull();
	});

	it('offers the test on a machine that has never run one', async () => {
		await withTest({
			running: false,
			rounds: 5,
			step: null,
			seconds_left: null,
			left_timed: false,
			whole_seconds: 299,
			whole_timed: false,
			finished: false,
			measured: false,
			share_reads_now: 0,
			whole_to_come: false,
			whole_due: false,
			held_while_measuring: 0,
			full_while_measuring: 0,
			notes: [],
			card: null,
			models: [],
			measurement: null,
			recommendations: []
		});

		expect(
			[...(panel()?.querySelectorAll('button') ?? [])].map((one) => one.textContent).join(' ')
		).toContain('Run the benchmark');
	});

	/*
	 * THE TEST NEVER RUNS UNASKED, so the pane has to say what is waiting on it, and has to stop
	 * saying it the moment there are rates on file.
	 *
	 * `measured` and not `finished`: rates are stored, so a machine measured in an earlier session
	 * comes back with no run in this process, and a sentence keyed on `finished` would tell it
	 * every time that the quicker read is still waiting.
	 */
	it('says what is waiting on the test while this machine has never been measured', async () => {
		await withTest({
			running: false,
			rounds: 5,
			step: null,
			seconds_left: null,
			left_timed: false,
			whole_seconds: 299,
			whole_timed: false,
			finished: false,
			measured: false,
			share_reads_now: 0,
			whole_to_come: false,
			whole_due: false,
			held_while_measuring: 0,
			full_while_measuring: 0,
			notes: [],
			card: null,
			models: [],
			measurement: null,
			recommendations: []
		});

		expect(panel()?.querySelector('[data-testid="self-test-unmeasured"]')?.textContent).toContain(
			'needs the benchmark first'
		);
	});

	it('and says nothing of the kind once this machine has rates on file', async () => {
		await withTest({
			running: false,
			rounds: 5,
			step: null,
			seconds_left: null,
			left_timed: false,
			whole_seconds: 299,
			whole_timed: false,
			finished: false,
			measured: true,
			share_reads_now: 0,
			whole_to_come: false,
			whole_due: false,
			held_while_measuring: 0,
			full_while_measuring: 0,
			notes: [],
			card: null,
			models: [],
			measurement: null,
			recommendations: []
		});

		expect(panel()?.querySelector('[data-testid="self-test-unmeasured"]')).toBeNull();
	});

	it('picks up a run that was already going before the screen was opened', async () => {
		// The run belongs to the server, not to this tab. Offering to start one while another is in
		// flight is how somebody ends up measuring two tests against each other.
		await withTest({
			running: true,
			rounds: 5,
			step: null,
			seconds_left: null,
			left_timed: false,
			whole_seconds: 299,
			whole_timed: false,
			finished: false,
			measured: false,
			share_reads_now: 0,
			whole_to_come: false,
			whole_due: false,
			held_while_measuring: 0,
			full_while_measuring: 0,
			notes: [],
			card: null,
			models: [],
			measurement: null,
			recommendations: []
		});

		expect(panel()?.querySelector('[data-testid="self-test-running"]')).not.toBeNull();
		expect(panel()?.querySelector('button.measure')).toBeNull();
	});

	/*
	 * A RUN IN FLIGHT HAS TO LOOK LIKE ONE, for all of its two to four minutes.
	 *
	 * One static sentence while the processor is pinned is indistinguishable from a test that
	 * started and died, so people press the button again, which the server correctly ignores,
	 * which looks like it is broken twice.
	 */
	it('and says how far through it is, from what the server has already measured', async () => {
		await withTest({
			running: true,
			rounds: 5,
			step: null,
			seconds_left: null,
			left_timed: false,
			whole_seconds: 299,
			whole_timed: false,
			finished: false,
			measured: false,
			share_reads_now: 0,
			whole_to_come: false,
			whole_due: false,
			held_while_measuring: 0,
			full_while_measuring: 0,
			notes: [],
			card: null,
			models: [],
			progress: {
				cores: 16,
				failed: null,
				levels: [
					{ at_once: 1, seconds: 20, finished: 1, per_second: 0.05, responsive: true },
					{ at_once: 2, seconds: 21, finished: 2, per_second: 0.1, responsive: true }
				],
				storages: [],
				decode: null
			},
			recommendations: []
		});

		const said = panel()?.querySelector('[data-testid="self-test-rounds"]')?.textContent ?? '';
		expect(said).toContain('Round 3 of up to 5');
		/* And a bar, because a sentence that changes every twenty seconds is easy to miss and a bar
		   that moves is not. */
		const bar = panel()?.querySelector(
			'[role="progressbar"][aria-label="Benchmarking this device"]'
		);
		expect(bar?.getAttribute('aria-valuenow')).toBe('2');
		expect(bar?.getAttribute('aria-valuemax')).toBe('5');
	});

	/* Before the first level lands there is nothing measured, and the count still has to be honest:
	   round one, of up to five. */
	it('and reads as the first round before anything has been measured', async () => {
		await withTest({
			running: true,
			rounds: 5,
			step: null,
			seconds_left: null,
			left_timed: false,
			whole_seconds: 299,
			whole_timed: false,
			finished: false,
			measured: false,
			share_reads_now: 0,
			whole_to_come: false,
			whole_due: false,
			held_while_measuring: 0,
			full_while_measuring: 0,
			notes: [],
			card: null,
			models: [],
			measurement: null,
			recommendations: []
		});

		expect(panel()?.querySelector('[data-testid="self-test-rounds"]')?.textContent).toContain(
			'Round 1 of up to 5'
		);
	});

	/*
	 * THE BOUNDARY: never a sixth round of up to five.
	 *
	 * The ladder has five reachable rungs on this machine and all five have reported, while the run
	 * carries on through the decoder and the shares. Naming the rung about to start would say six
	 * of five, when no rung is about to start.
	 */
	const rung = (at_once: number, responsive = true) => ({
		at_once,
		seconds: 20,
		finished: at_once,
		per_second: at_once / 20,
		responsive
	});

	it('never counts a round past the last rung of the ladder', async () => {
		await withTest({
			running: true,
			rounds: 5,
			step: null,
			seconds_left: null,
			left_timed: false,
			whole_seconds: 299,
			whole_timed: false,
			finished: false,
			measured: false,
			share_reads_now: 0,
			whole_to_come: false,
			whole_due: false,
			held_while_measuring: 0,
			full_while_measuring: 0,
			notes: [],
			card: null,
			models: [],
			progress: {
				cores: 24,
				failed: null,
				levels: [rung(1), rung(2), rung(4), rung(8), rung(16)],
				storages: [],
				decode: null
			},
			recommendations: []
		});

		const said = panel()?.querySelector('[data-testid="self-test-rounds"]')?.textContent ?? '';
		expect(said).not.toContain('Round 6');
		expect(said).toContain('Encoding rounds done: 5 of up to 5');
		/* The bar is full and stays full: the ladder is over, not the run. */
		const bar = panel()?.querySelector(
			'[role="progressbar"][aria-label="Benchmarking this device"]'
		);
		expect(bar?.getAttribute('aria-valuenow')).toBe('5');
	});

	/* And the other way the ladder ends: it broke out because the machine stopped keeping up, so
	   rungs are left unrun and none of them is about to start either. */
	it('does not name a round that the ladder broke out before reaching', async () => {
		await withTest({
			running: true,
			rounds: 5,
			step: null,
			seconds_left: null,
			left_timed: false,
			whole_seconds: 299,
			whole_timed: false,
			finished: false,
			measured: false,
			share_reads_now: 0,
			whole_to_come: false,
			whole_due: false,
			held_while_measuring: 0,
			full_while_measuring: 0,
			notes: [],
			card: null,
			models: [],
			progress: {
				cores: 24,
				failed: null,
				levels: [rung(1), rung(2), rung(4, false)],
				storages: [],
				decode: null
			},
			recommendations: []
		});

		const said = panel()?.querySelector('[data-testid="self-test-rounds"]')?.textContent ?? '';
		expect(said).not.toContain('Round 4');
		expect(said).toContain('Encoding rounds done: 3 of up to 5');
	});

	/* Past the rounds the run names the step it is on and how long is left, from the server's
	   figure: "about" where this device's last run timed it, "up to" where only the limit is known. */
	it('names the step past the rounds and says how long is left, revised as the run goes', async () => {
		const progress = {
			cores: 24,
			failed: null,
			levels: [rung(1), rung(2), rung(4), rung(8), rung(12), rung(16)],
			storages: [],
			decode: null
		};
		await withTest({
			...MEASURED,
			running: true,
			rounds: 6,
			progress,
			step: 'models',
			seconds_left: 100,
			left_timed: true
		});

		const said = () => panel()?.querySelector('[data-testid="self-test-rounds"]')?.textContent;
		const left = () => panel()?.querySelector('[data-testid="self-test-left"]')?.textContent;
		expect(said()).toContain(
			'Encoding rounds done: 6 of up to 6. Now timing each installed model.'
		);
		expect(left()?.trim()).toBe('About 2 min left.');

		await withTest({
			...MEASURED,
			running: true,
			rounds: 6,
			progress,
			step: 'together',
			seconds_left: 38,
			left_timed: true
		});
		expect(left()?.trim()).toBe('About under a minute left.');
		await withTest({
			...MEASURED,
			running: true,
			rounds: 6,
			step: 'encoding',
			seconds_left: 3,
			left_timed: true,
			progress: { ...progress, levels: [rung(1), rung(2), rung(4), rung(8), rung(16, false)] }
		});
		expect(said()).toContain(
			'Encoding rounds done: 5 of up to 6. Now one more round, between the two quickest.'
		);
		expect(left()?.trim()).toBe('Almost done.');
		await withTest({
			...MEASURED,
			running: true,
			rounds: 6,
			// The widest round cut at its share of the time: the rounds are over, short of six.
			progress: { ...progress, levels: [rung(1), rung(2), rung(4), rung(8), rung(16)] },
			step: 'storage',
			seconds_left: 241,
			left_timed: false
		});
		expect(said()).toContain('5 of up to 6. Now reading a few large files from each drive');
		expect(left()?.trim()).toBe('Up to 5 min left.');
	});

	it('says how long a whole run takes here, from the last one where it was timed', async () => {
		await withTest({ ...MEASURED, whole_seconds: 224, whole_timed: true });
		expect(panel()?.textContent).toContain('On this device it takes about 4 min');
		await withTest({ ...MEASURED, whole_seconds: 299, whole_timed: false });
		expect(panel()?.textContent).toContain('It takes up to 5 min and keeps this device busy');
	});

	/* Right after a press the run is queued or pausing work: no rung of it is measured yet, and the
	   last result stays on screen under the same press, never its rungs read as this run's. */
	it('starts a pressed run at round one and keeps the last result under it', async () => {
		await withTest({ ...MEASURED, running: true });

		const said = panel()?.querySelector('[data-testid="self-test-rounds"]')?.textContent ?? '';
		expect(said).toContain('Round 1 of up to 5');
		const bar = panel()?.querySelector(
			'[role="progressbar"][aria-label="Benchmarking this device"]'
		);
		expect(bar?.getAttribute('aria-valuenow')).toBe('0');
		expect(panel()?.textContent).toContain('How many previews are built at the same time');
		expect(panel()?.textContent).toContain(COPY.measure.again);
	});

	it('calls the press Run it again on a device measured before this start', async () => {
		await withTest({
			...MEASURED,
			running: true,
			finished: false,
			measurement: null,
			recommendations: []
		});

		expect(panel()?.textContent).toContain(COPY.measure.again);
		expect(panel()?.textContent).not.toContain(COPY.measure.run);
	});

	it('shows what it found, and changes nothing by itself', async () => {
		await withTest(MEASURED);

		expect(panel()?.textContent).toContain('How many previews are built at the same time');
		expect(panel()?.textContent).toContain('Nothing is changed until you press that');
		expect(saveSettings).not.toHaveBeenCalled();
	});

	it('groups the frames a second it measured, the way every other count on screen is grouped', () => {
		// Never a bare run of digits beside a count that is grouped.
		expect(COPY.measure.decode(1480, 27, false)).toContain(
			`about ${(1480).toLocaleString()} frames`
		);
	});

	it('says what the decoder means for a share only where a share was measured', () => {
		expect(COPY.measure.decode(1480, 27, false)).not.toContain('share');
		expect(COPY.measure.decode(1480, 27, true)).toContain('On a network share');
	});

	it("wraps each suggestion's reason at the pane's one reading measure", async () => {
		await withTest(MEASURED);
		host.classList.add('section-body');
		applyStyles(paneSource);
		try {
			const reason = panel()?.querySelector('.suggestion .reason') as HTMLElement;
			expect(reason.textContent).toBe('Measured directly.');
			expect(reason.localName).toBe('p');
			expect(getComputedStyle(reason).getPropertyValue('max-inline-size')).toBe(
				'var(--reading-measure)'
			);
		} finally {
			removeStyles();
		}
	});

	it('draws the change from one number to the next with the arrow character', async () => {
		await withTest(MEASURED);

		const numbers = panel()?.querySelector('.suggestion .numbers')?.textContent ?? '';
		// Never "automatic -> 2", typed with a hyphen and a greater-than sign.
		expect(numbers.replace(/\s+/g, ' ')).toContain('automatic \u2192 2');
		expect(numbers).not.toContain('->');
	});

	it('sends the suggested numbers only when they are applied', async () => {
		await withTest(MEASURED);

		applyButton()!.click();
		await vi.waitFor(() => expect(saveSettings).toHaveBeenCalled());

		expect(saveSettings).toHaveBeenCalledWith(
			expect.objectContaining({ 'performance.generation_limit': 2 })
		);
	});

	it('writes the suggested numbers and nothing else', async () => {
		// There is no offer to settle, so the save is exactly the numbers that were applied.
		await withTest(MEASURED);

		applyButton()!.click();
		await vi.waitFor(() => expect(saveSettings).toHaveBeenCalled());

		expect(saveSettings).toHaveBeenCalledWith({ 'performance.generation_limit': 2 });
	});

	it('takes the apply button away once the settings match', async () => {
		/* A button left after applying would still offer to make a change that had already been
		   made. The server compares against the CURRENT settings, so reading the run back is what
		   makes the screen go quiet. */
		fetchSelfTest.mockResolvedValueOnce(MEASURED).mockResolvedValue({
			...MEASURED,
			recommendations: [{ ...MEASURED.recommendations[0], current: 2, changes_anything: false }]
		});
		fetchHealth.mockResolvedValue(null);
		await render();
		await vi.waitFor(() => expect(applyButton()).not.toBeNull());

		applyButton()!.click();

		await vi.waitFor(() => expect(applyButton()).toBeNull());
		expect(panel()?.querySelector('[data-testid="self-test-agrees"]')).not.toBeNull();
	});

	/* Sift's own run: only what it set stands under the sentence that says it set it. */
	describe('after a run Sift started by itself', () => {
		const TASKS: Recommendation = {
			key: 'performance.worker_count',
			label: 'How many tasks run at the same time',
			current: 7,
			suggested: 7,
			reason: 'Measured directly.',
			changes_anything: false
		};
		const PREVIEWS: Recommendation = { ...MEASURED.recommendations[0], current: 8, suggested: 6 };
		const TASKS_SET = [{ key: TASKS.key, after: 7 }];

		async function after(
			state: string,
			changes: { key: string; after: number }[],
			recommendations: Recommendation[]
		) {
			machine.mockImplementation(async (url) => {
				if (url !== '/performance/benchmark') throw new Error('nothing is answering');
				const set = changes.map((one) => ({ ...one, label: one.key, before: 1 }));
				return { state, job_id: 'run-1', said: 'Done.', changes: set, measured: true };
			});
			await withTest({ ...MEASURED, recommendations });
			await vi.waitFor(() => expect(machine).toHaveBeenCalledWith('/performance/benchmark'));
			await new Promise((settled) => setTimeout(settled, 0));
			flushSync();
		}
		const said = () => panel()?.querySelector('[data-testid="self-test-set-by-sift"]');
		const notSet = () => panel()?.querySelector('[data-testid="self-test-not-set"]');
		const labels = (testid: string) =>
			[...(panel()?.querySelectorAll(`[data-testid="${testid}"] dt`) ?? [])].map(
				(one) => one.textContent
			);

		it('claims the row it set, and offers the one it only suggested with Apply', async () => {
			await after('set', TASKS_SET, [TASKS, PREVIEWS]);

			expect(said()?.textContent).toContain('set this number from it');
			expect(labels('self-test-set')).toEqual([TASKS.label]);
			expect(notSet()?.textContent?.trim()).toBe("Sift didn't set this number.");
			expect(labels('self-test-suggested')).toEqual([PREVIEWS.label]);
			expect(applyButton()?.textContent).toContain('Apply this number');
		});

		it('claims every row when it set them all', async () => {
			const both = { ...PREVIEWS, current: 6, changes_anything: false };
			await after('set', [...TASKS_SET, { key: PREVIEWS.key, after: 6 }], [TASKS, both]);

			expect(said()?.textContent).toContain('set these numbers from it');
			expect(labels('self-test-set')).toEqual([TASKS.label, PREVIEWS.label]);
			expect(notSet()).toBeNull();
			expect(applyButton()).toBeNull();
		});

		it('claims nothing when every row is a suggestion', async () => {
			await after('set', TASKS_SET, [PREVIEWS]);

			expect(said()).toBeNull();
			expect(applyButton()?.textContent).toContain('Apply this number');
		});

		it('claims nothing somebody has moved since', async () => {
			const moved = { ...TASKS, current: 9, changes_anything: true };
			await after('set', TASKS_SET, [moved, PREVIEWS]);

			expect(said()).toBeNull();
			expect(notSet()).toBeNull();
			expect(labels('self-test-suggested')).toEqual([TASKS.label, PREVIEWS.label]);
			expect(applyButton()?.textContent).toContain('Apply these 2 numbers');
		});

		it('claims nothing when there was nothing to change', async () => {
			await after('agreed', [], [TASKS]);

			expect(said()).toBeNull();
			expect(panel()?.querySelector('[data-testid="self-test-agrees"]')).not.toBeNull();
		});
	});

	it('says a test that could not run is not a slow machine', async () => {
		// The two call for opposite things, and a result of zero would be read as the second.
		await withTest({
			running: false,
			rounds: 5,
			step: null,
			seconds_left: null,
			left_timed: false,
			whole_seconds: 299,
			whole_timed: false,
			finished: true,
			measured: true,
			share_reads_now: 0,
			whole_to_come: false,
			whole_due: false,
			held_while_measuring: 0,
			full_while_measuring: 0,
			notes: [],
			card: null,
			models: [],
			measurement: {
				cores: 8,
				levels: [],
				failed: 'the video encoder could not be run',
				storages: [],
				decode: null
			},
			recommendations: []
		});

		expect(panel()?.querySelector('[data-testid="self-test-failed"]')?.textContent).toContain(
			'Your settings are unchanged'
		);
	});

	/* A share, and the three facts somebody on one needs beside its curve: what was measured,
	 * what Sift is reading at, and where the share stopped delivering more.
	 *
	 * `share_reads_now` is the SERVER'S number: the setting holds 0 for automatic, and the rule
	 * that turns that into the real figure belongs to `kernel/lanes`.
	 */
	const ON_A_SHARE = {
		...MEASURED,
		share_reads_now: 2,
		whole_to_come: false,
		whole_due: false,
		measurement: {
			...MEASURED.measurement,
			cores: 8,
			levels: MEASURED.measurement?.levels ?? [],
			failed: null,
			decode: null,
			storages: [
				{
					storage: '\\\\nas\\media\\',
					label: '\\\\nas\\media',
					folders: 'Clips, Trips',
					remote: true,
					failed: null,
					best_at_once: 1,
					levels: [
						{ at_once: 1, seconds: 4, megabytes: 88, megabytes_per_second: 22 },
						{ at_once: 8, seconds: 4, megabytes: 104, megabytes_per_second: 26 }
					]
				}
			]
		}
	} as unknown as SelfTest;

	it('says what a share was measured at and what Sift is reading it at', async () => {
		await withTest(ON_A_SHARE);

		// Whitespace collapsed: the markup wraps the sentence across lines and a person reads one
		// sentence, not the indentation it was written at.
		const said = (
			panel()?.querySelector('[data-testid="self-test-share-reads"]')?.textContent ?? ''
		).replace(/\s+/g, ' ');
		expect(said).toContain('Sift opens 2 files at the same time from every share');
		expect(said).toContain('reading is the slow part');
		const curve = (
			panel()?.querySelector('[data-testid="self-test-storages"]')?.textContent ?? ''
		).replace(/\s+/g, ' ');
		expect(curve).toContain('Quickest at 1 at a time.');
		expect(curve).toContain(
			'Quickest at 1 at a time. Files read per share is set to 2, so 2 are read at a time here.'
		);
		expect(curve).toContain('\\\\nas\\media Library folders on it: Clips, Trips.');
	});

	it('says the share setting under a share and never under a local drive beside it', async () => {
		const share = ON_A_SHARE.measurement?.storages?.[0];
		await withTest({
			...ON_A_SHARE,
			measurement: {
				...ON_A_SHARE.measurement,
				storages: [share, { ...share, storage: 'C:\\', label: 'drive C:', remote: false }]
			}
		} as unknown as SelfTest);

		const [onShare, onDrive] = [
			...(panel()?.querySelectorAll('[data-testid="self-test-storages"] .storage') ?? [])
		].map((one) => (one.textContent ?? '').replace(/\s+/g, ' '));
		expect(onShare).toContain('Files read per share is set to 2');
		expect(onDrive).toContain('Quickest at 1 at a time.');
		expect(onDrive).not.toContain('Files read per share');
	});

	it('says nothing of the share setting where only a local drive was measured', async () => {
		const share = ON_A_SHARE.measurement?.storages?.[0];
		await withTest({
			...ON_A_SHARE,
			measurement: { ...ON_A_SHARE.measurement, storages: [{ ...share, remote: false }] }
		} as unknown as SelfTest);

		expect(panel()?.querySelector('[data-testid="self-test-share-reads"]')).toBeNull();
	});

	it("says what the run could not measure and what it paused, in the server's words", async () => {
		const notes = [
			'No library folder yet, so no network share was measured.',
			'Sift paused 2 tasks while it measured, waited 3 s for them to stop, and started them again after.'
		];
		await withTest({ ...MEASURED, notes });

		const said = [...(panel()?.querySelectorAll('[data-testid="self-test-note"]') ?? [])].map(
			(one) => one.textContent
		);
		expect(said).toEqual(notes);
	});

	it.each([
		[true, "runs by itself once Sift has nothing else to do and nobody's using this device"],
		[false, "The full benchmark hasn't run yet."]
	])(
		'says a first measure is all there is until the full run (by itself: %s)',
		async (due, said) => {
			const whole = () => panel()?.querySelector('[data-testid="self-test-whole"]')?.textContent;
			await withTest({ ...MEASURED, whole_to_come: true, whole_due: due });
			expect(whole()).toContain('This is a first measure');
			expect(whole()).toContain(said);

			await withTest({ ...MEASURED, running: true, whole_to_come: true });
			expect(whole()).toBeUndefined();
		}
	);

	it('lists the previews built on the GPU and each measured model, not the ones it did not', async () => {
		await withTest({
			...MEASURED,
			card: {
				encoder: 'h264_nvenc',
				decodes_on_card: true,
				failed: null,
				best_at_once: 4,
				levels: [
					{
						at_once: 4,
						seconds: 3.2,
						finished: 4,
						per_second: 1.257,
						responsive: true,
						busy: false
					},
					{ at_once: 8, seconds: 6.2, finished: 8, per_second: 1.293, responsive: true, busy: true }
				]
			},
			models: [
				{
					name: 'Faces',
					device: 'nvidia',
					failed: null,
					best_at_once: 4,
					seconds_per_file: 11.46,
					megabytes: 1257,
					card_megabytes: 596,
					levels: [{ at_once: 4, files_per_second: 0.349, failed: 0, busy: true }]
				},
				{ name: 'Watermarks', device: 'cpu', failed: 'not installed', levels: [] }
			]
		} as unknown as SelfTest);

		const card = (
			panel()?.querySelector('[data-testid="self-test-card"]')?.textContent ?? ''
		).replace(/\s+/g, ' ');
		expect(card).toContain('Previews on your GPU');
		expect(card).toContain('4 at the same time: 1.26 previews a second');
		expect(panel()?.querySelector('[data-testid="self-test-card"] li.best')?.textContent).toContain(
			'4 at'
		);
		const models = (
			panel()?.querySelector('[data-testid="self-test-models"]')?.textContent ?? ''
		).replace(/\s+/g, ' ');
		expect(models).toContain('Faces on the GPU');
		expect(models).toContain('4 at the same time: 0.35 files a second');
		expect(models).toContain(
			'About 11.5 seconds of one task for each file. Loading it took 1,257 MB of memory and 596 MB on the GPU.'
		);
		expect(models).not.toContain('Watermarks');
	});

	it('says nothing about a number the server did not send', async () => {
		// Zero is not a reading, and "Sift reads 0 files at the same time" says the opposite of what happens.
		await withTest(MEASURED);

		expect(panel()?.querySelector('[data-testid="self-test-share-reads"]')).toBeNull();
	});

	it('is quiet when the settings already match what the machine can do', async () => {
		// Three rows all saying "leave this exactly as it is" is worse than one sentence saying so.
		await withTest({
			...MEASURED,
			recommendations: [{ ...MEASURED.recommendations[0], current: 2, changes_anything: false }]
		});

		expect(panel()?.querySelector('[data-testid="self-test-agrees"]')).not.toBeNull();
		expect(panel()?.querySelector('button.apply')).toBeNull();
	});
});

/* The two diagnosis tables, behind the same fold under "For a bug report": their only use is
 * diagnosis, so they carry a Copy button and are not read to decide anything. */
describe('the figures for a bug report', () => {
	function report(): HTMLElement | null {
		return host.querySelector('[data-testid="bug-report"]');
	}

	const WORK = [
		{ stage: 'db.read', runs: 2169, totalMs: 8400, worstMs: 9.2 },
		{ stage: 'preview.encode', runs: 12, totalMs: 900, worstMs: 210 }
	];

	it('is absent while nothing has been measured yet', async () => {
		// The ordinary answer moments after a start. An empty table would read as a broken screen.
		await show(QUIET);

		expect(report()).toBeNull();
	});

	it('sits inside the fold, not beside it', async () => {
		await show({ ...QUIET, work: WORK });

		expect(report()?.closest('details')).not.toBeNull();
	});

	it('names each kind of work, how often it ran, and keeps the order the server ranked', async () => {
		// Ranked by the TOTAL: the fault worth catching is a cheap thing done far too often, and
		// ordering by the worst single run is exactly what hides it.
		await show({ ...QUIET, work: WORK });

		const text = report()?.textContent ?? '';
		expect(text).toContain('2,169');
		const rows = [
			...(report()?.querySelectorAll('[data-testid="work-health"] tbody th[scope="row"]') ?? [])
		];
		expect(rows.map((one) => one.textContent)).toEqual(['db.read', 'preview.encode']);
	});

	it('marks a read wider than the server escalates', async () => {
		await show({
			...QUIET,
			widest: [{ read: 'SELECT * FROM assets', widestRows: 9000, runs: 3, totalRows: 12000 }]
		});

		expect(report()?.querySelectorAll('[data-testid="widest-reads"] td.bad')).toHaveLength(1);
	});

	it('offers the figures to copy', async () => {
		await show({ ...QUIET, work: WORK });

		expect(
			report()?.querySelector('button[aria-label="Copy the figures for a bug report"]')
		).not.toBeNull();
	});
});

/* The memory row, which reads one of two numbers.
 *
 * What the operating system reports as physical memory is what it can ADDRESS: the installed
 * total less whatever the firmware and the hardware reserve, a few gigabytes. Shown, it would
 * stand beside a Windows dialog naming the installed figure and read as Sift being unable to
 * count. Both are kept: the addressable figure is what any sizing is done against, and the
 * installed figure is what a person recognises as their computer.
 */
describe('the memory row', () => {
	async function memoryRow(over: Record<string, unknown>): Promise<string | undefined> {
		machine.mockResolvedValue({ ...MACHINE, ...over });
		await render([PERFORMANCE]);
		const row = [...host.querySelectorAll('.readings > div')].find(
			(one) => one.querySelector('dt')?.textContent?.trim() === 'Memory'
		);
		return row?.querySelector('dd')?.textContent?.trim();
	}

	it('says what the machine has, and only that', async () => {
		/* The addressable figure is deliberately NOT shown beside it. It is what every sizing
		   decision is made against, but a description of somebody's computer should say what is in
		   it: two numbers in one row would invite the question rather than answer it. */
		const said = await memoryRow({
			installed_ram_bytes: 34_359_738_368,
			total_ram_bytes: 33_285_996_544
		});

		expect(said).toBe('32 GB');
	});

	/* Linux has no equivalent that is not a guess, so nothing is answered there and the row falls
	   back to the addressable figure rather than to "Unknown". */
	it('falls back to the addressable figure where nothing will say what is installed', async () => {
		const said = await memoryRow({ installed_ram_bytes: null });

		expect(said).toBe('16 GB');
	});
});

/* The two computers.
 *
 * Everything Sift does with a machine happens where the library is, so the hardware block
 * describes the SERVER, and in client mode that is a computer somewhere else, which a heading
 * saying "This machine" would name wrongly.
 */
describe('the machine blocks', () => {
	async function drawn(local: unknown, desktop = true) {
		isDesktop.mockReturnValue(desktop);
		localHardware.mockResolvedValue(local);
		machine.mockResolvedValue(MACHINE);
		await render([PERFORMANCE]);
		return host;
	}

	it('says whose machine it is when the library is on another one', async () => {
		const shown = await drawn({
			cpu_model: 'A laptop processor',
			thread_count: 8,
			installed_ram_bytes: 17_179_869_184,
			gpu_cards: [{ name: 'AMD Radeon(TM) Graphics', vram_bytes: 2_147_483_648 }]
		});

		expect(shown.textContent).toContain('Device running Sift');
		expect(shown.textContent).toContain('This device');
		const block = shown.querySelector('[data-testid="local-hardware"]')?.textContent ?? '';
		expect(block).toContain('A laptop processor');
		// Its OWN card, which is not the one doing the work and is named anyway.
		expect(block).toContain('AMD Radeon(TM) Graphics');
		expect(block).toContain('2 GB');
	});

	/* On the machine running the library there is nothing to add, and a second block saying the
	   same thing twice would invite somebody to look for a difference that cannot exist. */
	it('draws one block, under the plain heading, on the machine running the library', async () => {
		const shown = await drawn(null);

		expect(shown.textContent).toContain('This device');
		expect(shown.querySelector('[data-testid="local-hardware"]')).toBeNull();
	});

	/* A BROWSER IS NOT A MACHINE. There is no second computer to describe, so the block that would
	   have said "This machine" is about a server reached over a network, and saying "this" about it
	   names the wrong thing twice over. */
	it('does not call the server "this machine" when read in a browser', async () => {
		const shown = await drawn(null, false);

		expect(shown.textContent).toContain('Device running Sift');
		expect(shown.textContent).not.toContain('This device');
		expect(shown.querySelector('[data-testid="local-hardware"]')).toBeNull();
	});

	/* THREADS, not cores. An eight-core chip with two threads each reports 16, and a row labelled
	   "Cores" over that number is simply wrong. */
	it('calls the count what it is', async () => {
		const shown = await drawn(null);

		const names = [...shown.querySelectorAll('.readings > div dt')].map((one) =>
			one.textContent?.trim()
		);
		expect(names).toContain('Threads');
		expect(names).not.toContain('Cores');
	});
});

/* Every card, not just the first.
 *
 * A machine can have more than one, and a description of somebody's computer that names half of it
 * is quietly wrong. The first is still the one that does the work (it is the device CUDA uses
 * unless it is told otherwise), so it is the one that says so.
 */
describe('the graphics cards', () => {
	async function cardRows(cards: unknown) {
		machine.mockResolvedValue({ ...MACHINE, gpu_cards: cards });
		await render([PERFORMANCE]);
		return [...host.querySelectorAll('.readings > div')]
			.map((row) => [
				row.querySelector('dt')?.textContent?.trim(),
				row.querySelector('dd')?.textContent?.replace(/\s+/g, ' ').trim()
			])
			.filter(([name]) => name === 'GPU' || name === 'Also installed');
	}

	it('names each one, with its memory', async () => {
		const rows = await cardRows([
			{ name: 'RTX 4080', vram_bytes: 17_179_869_184 },
			{ name: 'RTX 3060', vram_bytes: 12_884_901_888 }
		]);

		expect(rows).toHaveLength(2);
		expect(rows[0][1]).toContain('RTX 4080');
		expect(rows[0][1]).toContain('16 GB');
		expect(rows[1][1]).toContain('RTX 3060');
	});

	/* Which one does the work is the question a two-card machine actually has. */
	it('says which one Sift uses, but only when there is a choice', async () => {
		const two = await cardRows([
			{ name: 'A', can_compute: true },
			{ name: 'B', can_compute: false }
		]);
		expect(two[0][1]).toContain('Sift uses this one');

		const one = await cardRows([{ name: 'A', vram_bytes: null, can_compute: true }]);
		expect(one[0][1]).not.toContain('Sift uses this one');
	});

	/* WHICH ONE, NOT WHICH POSITION. Windows enumerates adapters in its own order, so a machine
	   that lists its built-in chip first would have had the mark on the wrong row. */
	it('marks the card that can do the work, wherever it is in the list', async () => {
		const rows = await cardRows([
			{ name: 'AMD Radeon(TM) Graphics', can_compute: false },
			{ name: 'RTX 4080', can_compute: true }
		]);

		expect(rows[0][1]).not.toContain('Sift uses this one');
		expect(rows[1][1]).toContain('Sift uses this one');
	});

	/* A driver that reports a device but will not name it. The row is still drawn, because "there
	   is a card here" is worth saying and is not the same as "there is none". */
	it('still draws a row for a card that will not say what it is', async () => {
		const rows = await cardRows([{ name: null, vram_bytes: null }]);

		expect(rows[0][1]).toContain("doesn't say which");
	});

	it('says there is none to reach when the list is empty', async () => {
		machine.mockResolvedValue({ ...MACHINE, gpu_cards: [], cuda: false, rocm: false });
		await render([PERFORMANCE]);

		expect(host.textContent).toContain('None Sift can reach');
	});
});
