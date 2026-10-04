/* The Faces pane, on the one Recognition layout.
 *
 * What is tested is what the pane says about itself, because each line is something somebody acts
 * on: where recognition stands, in one line whose every number is the server's; that the last scan
 * is when it ENDED and says so when it was canceled; that when it runs is the task's own row; that
 * the rows for tuning it are one page in and can be reached; and that the People it knows, the
 * packs and the folder of people are lists on the pane itself, drawn as rows.
 */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';
import { words as wordsOn } from '$lib/design/testing.svelte';
import type { SettingEntry } from '$lib/settings-ui/settings';
import type { PackImported } from '$lib/people/fingerprint-packs';
import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import faces from './Faces.svelte?raw';
import { noServerAt } from '../../test-setup';

/* Left unanswered on purpose: the card art a person's picture in the share list is read beside. */
noServerAt('/api/creator-art');
/* And the folder import's task, looked for as the pane opens: its row has its own file. */
noServerAt('/api/jobs');

const mocks = vi.hoisted(() => ({
	fetchSettings: vi.fn(),
	saveSettings: vi.fn(),
	faceSettings: vi.fn(),
	startersOffer: vi.fn(),
	useStarters: vi.fn(),
	fetchModels: vi.fn(async (_again?: boolean) => 'job'),
	knownPeople: vi.fn(),
	waitingFingerprints: vi.fn(),
	removeWaitingFingerprints: vi.fn(),
	makePersonFromFingerprints: vi.fn(),
	libraries: vi.fn(),
	exportWay: vi.fn(() => 'except' as 'except' | 'only'),
	rememberExportWay: vi.fn()
}));

/* How the export's sheet opens, remembered for the account; the module is tested beside it. */
vi.mock('$lib/shell/interface-state.svelte', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/shell/interface-state.svelte')>()),
	recallInterfaceState: async () => undefined,
	exportWay: mocks.exportWay,
	rememberExportWay: mocks.rememberExportWay
}));

/* The people a file or a folder holds for whom no face matches yet, and the two presses on them. */
vi.mock('$lib/people/fingerprint-offers', () => ({
	waitingFingerprints: mocks.waitingFingerprints,
	removeWaitingFingerprints: mocks.removeWaitingFingerprints,
	makePersonFromFingerprints: mocks.makePersonFromFingerprints
}));

/* The library's name, which the exported file is named after; every other read goes through. */
vi.mock('$lib/api/client', async (importOriginal) => {
	const actual = await importOriginal<typeof import('$lib/api/client')>();
	return {
		...actual,
		api: {
			...actual.api,
			get: (path: string, options?: unknown) =>
				path === '/libraries' ? mocks.libraries() : actual.api.get(path as never, options as never)
		}
	};
});

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

vi.mock('$lib/settings-ui/settings', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/settings-ui/settings')>()),
	fetchSettings: mocks.fetchSettings,
	saveSettings: mocks.saveSettings
}));

vi.mock('$lib/people/faces.svelte', () => ({
	FACES_ENABLED_KEY: 'faces.enabled',
	FACES_DEVICE_KEY: 'faces.device',
	faceSettings: mocks.faceSettings,
	fetchModels: mocks.fetchModels,
	forgetFaces: vi.fn(),
	regroupFaces: vi.fn(),
	knownPeople: mocks.knownPeople,
	startersOffer: mocks.startersOffer,
	useStarters: mocks.useStarters,
	scanLibrary: vi.fn(),
	stopScanning: vi.fn(),
	describeWait: () => '',
	sweep: {
		jobId: null,
		total: 0,
		done: 0,
		scanned: 0,
		left: 0,
		failed: 0,
		problem: null,
		remaining: null,
		announceWith: () => {},
		resume: async () => {},
		follow: () => {}
	}
}));

/* A file of facial fingerprints, in and out: what the presses send is read off these. */
vi.mock('$lib/people/fingerprint-packs', () => ({ importPack: vi.fn(), exportPack: vi.fn() }));

vi.mock('$lib/people/faces-runs.svelte', () => ({
	modelFetch: {
		running: false,
		fraction: 0,
		status: '',
		outcome: null,
		settled: null,
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

import Faces from './Faces.svelte';
import DrilldownPage from './DrilldownPage.svelte';
import { drilldown } from './drilldown.svelte';

function entry(key: string, over: Partial<SettingEntry> = {}): SettingEntry {
	return { key, value: false, default: false, label: key, ...over };
}

function settings(enabled: boolean, extra: SettingEntry[] = []) {
	return [
		{
			name: 'Identify',
			settings: [
				entry('faces.enabled', { label: 'Recognize faces in your library', value: enabled }),
				entry('faces.device', {
					label: 'Run recognition on',
					value: 'nvidia',
					choices: ['cpu', 'nvidia'],
					choice_labels: ['CPU', 'GPU']
				}),
				entry('faces.effort', { label: 'How thorough', value: 'balanced' }),
				...extra
			]
		}
	];
}

function feature(over: Record<string, unknown> = {}) {
	return {
		enabled: true,
		ready: true,
		family: 'accurate',
		device: 'nvidia',
		depth: 'fast',
		device_problem: null,
		last_run_at: null,
		measured_by_another_model: 0,
		references_without_pictures: 0,
		never_scanned: 0,
		scanned_under_older_rules: 0,
		installed: [],
		...over
	};
}

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

beforeEach(() => {
	vi.clearAllMocks();
	mocks.saveSettings.mockResolvedValue(undefined);
	mocks.startersOffer.mockResolvedValue({ people: 0 });
	mocks.knownPeople.mockResolvedValue({ items: [], total: 0 });
	mocks.waitingFingerprints.mockResolvedValue({ items: [] });
	mocks.libraries.mockResolvedValue({ libraries: [{ name: 'Home Movies', current: true }] });
	mocks.exportWay.mockReturnValue('except');
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host.remove();
	drilldown.close();
});

async function draw(
	enabled: boolean,
	state: Record<string, unknown>,
	extra: SettingEntry[] = []
): Promise<void> {
	mocks.fetchSettings.mockResolvedValue(settings(enabled, extra));
	mocks.faceSettings.mockResolvedValue(feature(state));
	drawn = mount(Faces, { target: host }) as Record<string, unknown>;
	mount(DrilldownPage, { target: host, props: { behind: 'Faces' } });
	await vi.waitFor(() => expect(mocks.faceSettings).toHaveBeenCalled(), { interval: 1 });
	for (let turn = 0; turn < 6; turn += 1) await tick();
	flushSync();
}

function line(): string {
	return host.querySelector('.status')?.textContent?.trim() ?? '';
}

it('says where recognition stands in one line, every number from the server', async () => {
	await draw(true, { never_scanned: 80000, scanned_under_older_rules: 12 });

	expect(line()).toBe(
		`Ready. Running on the GPU. ${(80000).toLocaleString()} files not scanned for faces yet. 12 files were scanned with older settings.`
	);
});

it('says the last scan was canceled rather than reading it as the last look at the library', async () => {
	await draw(true, { last_run_at: 1_700_000_000_000, last_run_canceled: true });

	expect(line()).toContain('Last scan was canceled');
});

it('reads a finished last scan as when it ended', async () => {
	await draw(true, { last_run_at: 1_700_000_000_000, last_run_canceled: false });

	expect(line()).toContain('Last scan ended');
});

it('says the day in lower case in the middle of the sentence', async () => {
	// A second ago is today however close to midnight the run is.
	await draw(true, { last_run_at: Date.now() - 1000, last_run_canceled: false });

	expect(line()).toMatch(/Last scan ended today \d/);
	expect(line()).not.toContain('Today');
});

it('says it is off as its status, and offers no When row while it is', async () => {
	await draw(false, { enabled: false });

	expect(line()).toBe('Turned off. The faces Sift already found are kept.');
	expect(host.querySelector('[id="tasks.faces.when"]')).toBeNull();
	expect(host.querySelector('[id="faces.more"]')).toBeNull();
	// Who Sift knows is reachable with recognition off: pausing it is no reason to lose the list.
	expect(host.querySelector('[id="faces.people"]')).not.toBeNull();
});

it("draws the Identify task's own When row, and How thorough on the page itself", async () => {
	await draw(true, {});

	expect(host.querySelector('[id="tasks.faces.when"]')).not.toBeNull();
	expect(host.textContent).toContain('How thorough');
	// The device is for somebody tuning it: one page in, not on the page.
	expect(host.textContent).not.toContain('Run recognition on');
});

it('opens More settings on the rows for tuning it, and a deep link to one opens it too', async () => {
	await draw(true, {});

	expect(drilldown.reveal('faces.device')).toBe(true);
	flushSync();
	await tick();
	flushSync();

	expect(drilldown.title).toBe('More settings');
	expect(host.textContent).toContain('Run recognition on');
});

it('opens More settings for a deep link to a row drawn by hand on it', async () => {
	await draw(true, {});

	for (const key of ['faces.regroup', 'faces.rescan', 'faces.models']) {
		drilldown.close();
		flushSync();
		expect(drilldown.reveal(key)).toBe(true);
		flushSync();
		await tick();
		flushSync();
		expect(drilldown.title).toBe('More settings');
	}
});

it('calls the press that looks at every file again Identify, the word of the stage it reruns', async () => {
	await draw(true, {});

	expect(drilldown.reveal('faces.rescan')).toBe(true);
	flushSync();
	await tick();
	flushSync();

	const row = host.querySelector('[id="faces.rescan"]');
	expect(row?.textContent).toContain('Identify all files again');
	const presses = [...(row?.querySelectorAll('button') ?? [])].map((one) => wordsOn(one));
	expect(presses).toContain('Identify');
	expect(presses).not.toContain('Scan');
	expect(host.textContent).not.toContain('Scan all files again');
});

it('draws no match-again row, even from a server that still sends one', async () => {
	/* No switch decides whether a match runs again: every change to the People Sift knows
	   re-matches, once. The pane lists the rows it draws, so an entry a stale server still sends
	   is not one of them. */
	const sent = settings(true);
	sent[0].settings.push(
		entry('faces.rematch_on_change', { label: 'Match again when People change', value: true })
	);
	mocks.fetchSettings.mockResolvedValue(sent);
	mocks.faceSettings.mockResolvedValue(feature({}));
	drawn = mount(Faces, { target: host }) as Record<string, unknown>;
	mount(DrilldownPage, { target: host, props: { behind: 'Faces' } });
	await vi.waitFor(() => expect(mocks.faceSettings).toHaveBeenCalled(), { interval: 1 });
	for (let turn = 0; turn < 6; turn += 1) await tick();
	flushSync();

	expect(drilldown.reveal('faces.device')).toBe(true);
	flushSync();
	await tick();
	flushSync();

	expect(host.textContent).toContain('Run recognition on');
	expect(host.textContent).not.toContain('Match again when People change');
});

it('draws no Naming faces rows, even from a server that still sends them, and keeps Group faces again', async () => {
	/* How sure Sift must be and how many samples describe a person are fixed in the code: nobody
	   could tune them without knowing how the matching is measured. A stale server's entries are
	   not rows the pane draws. */
	const sent = settings(true);
	sent[0].settings.push(
		entry('faces.match_confidence', { label: 'Ask about a match above', value: 45 }),
		entry('faces.attach_confidence', { label: 'Add the name without asking above', value: 60 }),
		entry('faces.reference_groups', { label: 'Face samples per person', value: 1 })
	);
	mocks.fetchSettings.mockResolvedValue(sent);
	mocks.faceSettings.mockResolvedValue(feature({}));
	drawn = mount(Faces, { target: host }) as Record<string, unknown>;
	mount(DrilldownPage, { target: host, props: { behind: 'Faces' } });
	await vi.waitFor(() => expect(mocks.faceSettings).toHaveBeenCalled(), { interval: 1 });
	for (let turn = 0; turn < 6; turn += 1) await tick();
	flushSync();

	expect(drilldown.reveal('faces.device')).toBe(true);
	flushSync();
	await tick();
	flushSync();

	expect(host.textContent).toContain('Run recognition on');
	expect(host.textContent).not.toContain('Naming faces');
	expect(host.textContent).not.toContain('Ask about a match above');
	expect(host.textContent).not.toContain('Add the name without asking above');
	expect(host.textContent).not.toContain('Face samples per person');
	expect(host.querySelector('[id="faces.regroup"]')?.textContent).toContain('Group faces again');
});

it('offers stash-box starters with the count first, and the one press queues them', async () => {
	/* The one explicit press that shows the count first: the number is in the row's own label
	   before anything is pressed, because the press reaches out to the stash-boxes for every one
	   of those People. The count is read again afterwards, so the row says what is left. */
	mocks.startersOffer.mockResolvedValue({ people: 500 });
	mocks.useStarters.mockResolvedValue({ job_id: 'job', people: 500 });
	await draw(true, {});

	const row = host.querySelector('[id="faces.starters"]');
	expect(row?.textContent).toContain('Use stash-box pictures as starters for 500 people');
	expect(mocks.useStarters).not.toHaveBeenCalled();
	row?.querySelector('button')?.click();
	await vi.waitFor(() => expect(mocks.useStarters).toHaveBeenCalledTimes(1), { interval: 1 });
	await vi.waitFor(() => expect(mocks.startersOffer).toHaveBeenCalledTimes(2), { interval: 1 });
});

it('names the People the starters count, each opening that person, folded under the row', async () => {
	/* A count alone says which people nowhere: the count is checked as names before a press
	   that reaches out to a stash-box for every one of them. */
	mocks.startersOffer.mockResolvedValue({
		people: 2,
		who: [
			{ id: 'p1', name: 'Ada Byron' },
			{ id: 'p2', name: 'Bryn Calloway' }
		]
	});
	await draw(true, {});

	const fold = await vi.waitFor(() => {
		const found = host.querySelector('[id="faces.starters.who"]');
		expect(found).not.toBeNull();
		return found!;
	});
	expect(fold.textContent).toContain('Show all 2 people');
	const links = [...fold.querySelectorAll('a')].map((one) => [
		one.textContent,
		one.getAttribute('href')
	]);
	expect(links).toEqual([
		['Ada Byron', '/people/p1'],
		['Bryn Calloway', '/people/p2']
	]);
});

it("keeps the starters' help to two sentences, the rest under More about this", async () => {
	mocks.startersOffer.mockResolvedValue({ people: 500 });
	await draw(true, {});

	const row = await vi.waitFor(() => {
		const found = host.querySelector('[id="faces.starters"]');
		expect(found).not.toBeNull();
		return found!;
	});
	const help = row.querySelector('.help')?.textContent ?? '';
	expect(help.split('. ')).toHaveLength(2);
	expect(help).not.toContain('\u2014');
	expect(row.querySelector('details')?.textContent).toContain(
		'never names a face from a starter without you'
	);
});

it('offers nothing to press when nobody linked is waiting for starters', async () => {
	await draw(true, {});

	const button = host.querySelector('[id="faces.starters"] button') as HTMLButtonElement | null;
	expect(button?.disabled).toBe(true);
});

it('puts where it stands in the shaded box under the switch, and the download in a row', async () => {
	await draw(true, { ready: false });

	expect(host.querySelector('.recognition-note .status')?.textContent).toBe(
		"On, but the models aren't downloaded yet."
	);
	// A row with its button in the control column, never a button under a paragraph.
	const press = host.querySelector('[id="faces.download"] .control button');
	expect(wordsOn(press)).toBe('Download the models');
});

it('draws the People lists on the pane as rows, and the way out last under its own heading', async () => {
	await draw(true, {});

	// No page one level in for them: the lists a section owns are part of its page.
	expect(host.textContent).not.toContain('Import People');
	for (const id of ['faces.people', 'faces.packs', 'faces.folder']) {
		expect(host.querySelector(`[id="${id}"]`), id).not.toBeNull();
	}
	// Every press on the lists is in a row's control column.
	for (const id of ['faces.pack-import', 'faces.pack-export', 'faces.folder-import']) {
		expect(host.querySelector(`[id="${id}"] .control button`), id).not.toBeNull();
	}
	const groups = [...host.querySelectorAll('section.group')];
	const last = groups[groups.length - 1];
	expect(last?.textContent).toContain('Start over');
	expect(wordsOn(last?.querySelector('[id="faces.forget"] .control button'))).toBe(
		'Delete face data'
	);
});

it('offers to download the models AGAIN once they are here, and asks for a forced fetch', async () => {
	/* "Download the models" over models on the disk is an instruction to somebody who has already
	   followed it; the press says what it does, and without `again` it would fetch nothing. */
	await draw(true, { ready: true });

	expect(drilldown.reveal('faces.device')).toBe(true);
	flushSync();
	await tick();
	flushSync();

	const row = host.querySelector('[id="faces.models"]');
	expect(row?.textContent).toContain('Download them again only if a file is damaged');
	const press = row?.querySelector<HTMLButtonElement>('.control button');
	/* The row names what is downloaded, so the press is the verb alone; its accessible name keeps both. */
	expect(wordsOn(press)).toBe('Download');
	expect(press?.getAttribute('aria-label')).toBe('Download the models again');
	press?.click();
	await vi.waitFor(() => expect(mocks.fetchModels).toHaveBeenCalledWith(true), { interval: 1 });
});

it("says why the people it knows are not listed in the server's words while it declines", async () => {
	mocks.knownPeople.mockResolvedValue({
		items: [],
		total: 0,
		declined: 'Face recognition is turned off.'
	});
	await draw(true, {});

	const said = host.textContent ?? '';
	expect(said).toContain('Face recognition is turned off.');
	expect(said).not.toContain("Couldn't load this list.");
});

it('stands the people it knows on the pane edges, with room inside the scroller for the overhang', async () => {
	mocks.knownPeople.mockResolvedValue({
		items: [{ id: 'p1', name: 'Ada Byron', faces: 3, starters: 0 }],
		total: 1
	});
	await draw(true, {});

	const list = host.querySelector('.known-box ul.rows')!;
	expect(list.classList.contains('edges'), 'the list is not on the pane edges').toBe(true);
	/* The option pulls the list out by a row's padding; the scroller's root clips, so the same
	   amounts are given as padding on the box the list stands in. */
	const room = list.parentElement!;
	expect(room.classList.contains('known-room')).toBe(true);
	applyStyles(faces, host.querySelector('.known-box'));
	try {
		expect(getComputedStyle(room).getPropertyValue('padding-inline').trim()).toBe(
			'var(--space-2) var(--space-3)'
		);
	} finally {
		removeStyles();
	}
});

it('draws the people it knows as a table: each count in its own column, a zero as a quiet dash', async () => {
	mocks.knownPeople.mockResolvedValue({
		items: [
			{ id: 'p1', name: 'Ada Byron', faces: 1234, starters: 0 },
			{ id: 'p2', name: 'Bryn Calloway', faces: 0, starters: 3 }
		],
		total: 2
	});
	await draw(true, {});

	const list = host.querySelector('.known-box ul.rows')!;
	expect(list).not.toBeNull();
	const heads = [...list.querySelectorAll('.heads .head')].map((one) => one.textContent?.trim());
	expect(heads).toEqual(['', 'Confirmed faces', 'Starters']);
	expect([...list.querySelectorAll('.heads .head.end')]).toHaveLength(2);

	const rows = [...list.querySelectorAll('.row.columned')];
	expect(rows).toHaveLength(2);
	const cells = (row: Element) => [...row.querySelectorAll(':scope > .cell')];
	// Every row has one cell per column, so a count can never float into the name's column.
	for (const row of rows) expect(cells(row)).toHaveLength(3);

	const [ada, bryn] = rows.map(cells);
	expect(ada[1].textContent?.trim()).toBe((1234).toLocaleString());
	expect(ada[2].querySelector('.none')?.textContent).toBe('\u2014');
	expect(ada[2].querySelector('.none')?.getAttribute('aria-hidden')).toBe('true');
	expect(ada[2].querySelector('.unseen')?.textContent).toBe('none');
	expect(bryn[1].querySelector('.none')).not.toBeNull();
	expect(bryn[2].textContent?.trim()).toBe('3');
	// No bare zero anywhere: a figure is only drawn where there is something to count.
	expect(list.textContent).not.toMatch(/(^|\s)0(\s|$)/);
});

it('calls them facial fingerprints, never a pack, and says how many it can recognize', async () => {
	mocks.knownPeople.mockResolvedValue({
		items: [
			{ id: 'p1', name: 'Ada Byron', faces: 12, starters: 0 },
			{ id: 'p2', name: 'Bryn Calloway', faces: 4, starters: 0 }
		],
		total: 2
	});
	await draw(true, {});

	const group = host.querySelector('[id="faces.packs"]')!.closest('section')!;
	expect(group.textContent).toContain('Facial fingerprints');
	expect(group.textContent).toContain('Sift can recognize 2 people.');
	expect(group.textContent).toContain("Export your library's facial fingerprints");
	expect(group.textContent).toContain(
		'Add people Sift can recognize from a facial fingerprints file to your library'
	);
	expect(host.textContent).not.toMatch(/\bpacks?\b/i);
});

it('says nobody can be recognized yet, with no count, when the list is empty', async () => {
	mocks.knownPeople.mockResolvedValue({ items: [], total: 0 });
	await draw(true, {});

	const group = host.querySelector('[id="faces.packs"]')!.closest('section')!;
	expect(group.textContent).toContain(
		"Sift can recognize nobody yet, so there's nothing to export."
	);
	expect(group.textContent).not.toContain('recognize 0');
});

it('exports everyone with one press, or everyone but the people left out', async () => {
	mocks.exportWay.mockReturnValue('except');
	const { exportPack } = await import('$lib/people/fingerprint-packs');
	vi.mocked(exportPack).mockResolvedValue(new Blob(['zip']));
	URL.createObjectURL = vi.fn(() => 'blob:faces');
	URL.revokeObjectURL = vi.fn();
	const saved: string[] = [];
	const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
		this: HTMLAnchorElement
	) {
		saved.push(this.download);
	});
	mocks.knownPeople.mockResolvedValue({
		items: [
			{ id: 'p1', name: 'Ada Byron', faces: 12, starters: 0, cover_upload_id: 'u1' },
			{ id: 'p2', name: 'Bryn Calloway', faces: 0, starters: 3 },
			{ id: 'p3', name: 'Cass Ivory', faces: 5, starters: 0 }
		],
		total: 3
	});
	await draw(true, {});

	const row = host.querySelector('[id="faces.pack-export"]')!;
	const everyone = [...row.querySelectorAll('button')].find((one) =>
		one.textContent?.includes('Export')
	)!;
	everyone.click();
	await vi.waitFor(() => expect(exportPack).toHaveBeenCalledTimes(1), { interval: 1 });
	expect(vi.mocked(exportPack).mock.calls[0][1]).toEqual({ personIds: [], includePictures: false });
	// Named for the library and the day, never one name for every export.
	await vi.waitFor(() => expect(saved).toHaveLength(1), { interval: 1 });
	expect(saved[0]).toMatch(/^Home Movies facial fingerprints \d{4}-\d{2}-\d{2}\.zip$/);

	row.querySelector<HTMLButtonElement>('button[aria-label="Choose people"]')!.click();
	await vi.waitFor(
		() => expect(document.body.textContent).toContain('Choose who goes in the export'),
		{ interval: 1 }
	);
	expect(document.body.textContent).toContain('All 2 people are in the file.');
	// Only somebody with a page and confirmed faces has anything of this library's to export.
	await vi.waitFor(() => expect(document.body.textContent).toContain('Ada Byron'), { interval: 1 });
	const sheet = document.body.querySelector('[role="dialog"]')!;
	expect(sheet.textContent).not.toContain('Bryn Calloway');
	// Each drawn by their picture, as every people picker draws them, not as a bare name.
	expect(
		[...sheet.querySelectorAll('img')].some((one) => one.getAttribute('src')?.includes('p1')),
		'Ada is drawn without her picture'
	).toBe(true);
	// Everyone starts ticked: a press leaves somebody out.
	const ada = [...sheet.querySelectorAll('button, [role="option"], [role="checkbox"]')].find(
		(one) => one.textContent?.includes('Ada Byron')
	) as HTMLElement;
	ada.click();
	flushSync();
	const confirm = [...sheet.querySelectorAll('button')].find(
		(one) => one.textContent?.trim() === 'Export 1 person'
	)!;
	confirm.click();
	await vi.waitFor(() => expect(exportPack).toHaveBeenCalledTimes(2), { interval: 1 });
	expect(vi.mocked(exportPack).mock.calls[1][1]).toEqual({
		personIds: ['p3'],
		includePictures: false
	});
	click.mockRestore();
});

it('exports only the people picked, the other way round, and remembers the way', async () => {
	const { exportPack } = await import('$lib/people/fingerprint-packs');
	vi.mocked(exportPack).mockResolvedValue(new Blob(['zip']));
	URL.createObjectURL = vi.fn(() => 'blob:faces');
	URL.revokeObjectURL = vi.fn();
	const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
	mocks.exportWay.mockReturnValue('except');
	mocks.knownPeople.mockResolvedValue({
		items: [
			{ id: 'p1', name: 'Ada Byron', faces: 12, starters: 0 },
			{ id: 'p3', name: 'Cass Ivory', faces: 5, starters: 0 }
		],
		total: 2
	});
	await draw(true, {});

	host
		.querySelector<HTMLButtonElement>(
			'[id="faces.pack-export"] button[aria-label="Choose people"]'
		)!
		.click();
	await vi.waitFor(
		() => expect(document.body.querySelector('[role="dialog"] .rows')).not.toBeNull(),
		{ interval: 1 }
	);
	const sheet = document.body.querySelector('[role="dialog"]')!;
	const only = [...sheet.querySelectorAll<HTMLElement>('[role="radio"]')].find((one) =>
		one.textContent?.includes('Include only the people you pick')
	)!;
	only.click();
	await vi.waitFor(
		() => expect(sheet.textContent).toContain('Nobody is in the file yet. Tick everyone'),
		{ interval: 1 }
	);
	expect(mocks.rememberExportWay).toHaveBeenCalledWith('only');
	// Nobody starts ticked now: a press puts somebody in.
	expect(sheet.querySelectorAll('.rows button[aria-pressed="true"]')).toHaveLength(0);
	const cass = [...sheet.querySelectorAll<HTMLElement>('.rows button')].find((one) =>
		one.textContent?.includes('Cass Ivory')
	)!;
	cass.click();
	flushSync();
	[...sheet.querySelectorAll<HTMLButtonElement>('button')]
		.find((one) => one.textContent?.trim() === 'Export 1 person')!
		.click();
	await vi.waitFor(() => expect(exportPack).toHaveBeenCalledTimes(1), { interval: 1 });
	expect(vi.mocked(exportPack).mock.calls[0][1]).toEqual({
		personIds: ['p3'],
		includePictures: false
	});
	click.mockRestore();
});

it('opens the sheet the way it was left, with nobody ticked', async () => {
	mocks.exportWay.mockReturnValue('only');
	mocks.knownPeople.mockResolvedValue({
		items: [{ id: 'p1', name: 'Ada Byron', faces: 12, starters: 0 }],
		total: 1
	});
	await draw(true, {});

	host
		.querySelector<HTMLButtonElement>(
			'[id="faces.pack-export"] button[aria-label="Choose people"]'
		)!
		.click();
	await vi.waitFor(
		() => expect(document.body.querySelector('[role="dialog"] .rows')).not.toBeNull(),
		{ interval: 1 }
	);
	const sheet = document.body.querySelector('[role="dialog"]')!;
	expect(sheet.textContent).toContain('Nobody is in the file yet.');
	expect(sheet.querySelectorAll('.rows button[aria-pressed="true"]')).toHaveLength(0);
});

it("marks who has under twenty confirmed faces on the export's sheet, with their page's band", async () => {
	mocks.knownPeople.mockResolvedValue({
		items: [
			{ id: 'p1', name: 'Ada Byron', faces: 40, starters: 0, verdict: 'strong' },
			{ id: 'p3', name: 'Cass Ivory', faces: 3, starters: 0, verdict: 'weak' }
		],
		total: 2
	});
	await draw(true, {});

	const row = host.querySelector('[id="faces.pack-export"]')!;
	row.querySelector<HTMLButtonElement>('button[aria-label="Choose people"]')!.click();
	await vi.waitFor(
		() => expect(document.body.querySelector('[role="dialog"] .thin')).not.toBeNull(),
		{
			interval: 1
		}
	);
	const sheet = document.body.querySelector('[role="dialog"]')!;
	const thin = [...sheet.querySelectorAll('.thin')];
	expect(thin.map((one) => [one.getAttribute('data-band'), one.textContent?.trim()])).toEqual([
		['weak', 'Only 3 confirmed faces: Sift recognizes them less surely']
	]);
});

it('sends the face pictures only when the switch for them is turned on', async () => {
	const { exportPack } = await import('$lib/people/fingerprint-packs');
	vi.mocked(exportPack).mockResolvedValue(new Blob(['zip']));
	URL.createObjectURL = vi.fn(() => 'blob:faces');
	URL.revokeObjectURL = vi.fn();
	const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
	mocks.knownPeople.mockResolvedValue({
		items: [{ id: 'p1', name: 'Ada Byron', faces: 12, starters: 0 }],
		total: 1
	});
	await draw(true, {});

	const pictures = host.querySelector('[id="faces.pack-pictures"]')!;
	expect(pictures.textContent).toContain("can't compare them");
	const toggle = pictures.querySelector<HTMLButtonElement>('[role="switch"]')!;
	expect(toggle.getAttribute('aria-checked')).toBe('false');
	toggle.click();
	flushSync();
	[...host.querySelectorAll<HTMLButtonElement>('[id="faces.pack-export"] button')]
		.find((one) => one.textContent?.includes('Export'))!
		.click();
	await vi.waitFor(() => expect(exportPack).toHaveBeenCalledTimes(1), { interval: 1 });
	expect(vi.mocked(exportPack).mock.calls[0][1]).toEqual({ personIds: [], includePictures: true });
	click.mockRestore();
});

it('lists who is waiting for a matching face, and creates or removes one with a press', async () => {
	mocks.waitingFingerprints.mockResolvedValue({
		items: [{ entry_id: 'e1', name: 'Neve Arbor', faces: 3, source: 'Studio Faces', added_at: 0 }]
	});
	mocks.makePersonFromFingerprints.mockResolvedValue('p9');
	mocks.removeWaitingFingerprints.mockResolvedValue(undefined);
	await draw(true, {});

	const group = host.querySelector('[id="faces.waiting"]')!.closest('section')!;
	expect(group.textContent).toContain('Waiting for a matching face');
	await vi.waitFor(() => expect(group.textContent).toContain('Neve Arbor'), { interval: 1 });
	expect(group.textContent?.replace(/\s+/g, ' ')).toContain('3 faces');
	expect(group.textContent).toContain('From Studio Faces');

	const button = (words: string) =>
		[...group.querySelectorAll<HTMLButtonElement>('button')].find(
			(one) => one.textContent?.trim() === words
		)!;
	button('Create a person').click();
	await vi.waitFor(() => expect(mocks.makePersonFromFingerprints).toHaveBeenCalledWith('e1'), {
		interval: 1
	});

	await vi.waitFor(() => expect(button('Remove').disabled).toBe(false), { interval: 1 });
	button('Remove').click();
	await vi.waitFor(() => expect(document.body.textContent).toContain('Remove Neve Arbor?'), {
		interval: 1
	});
	const dialog = document.body.querySelector('[role="alertdialog"], [role="dialog"]')!;
	[...dialog.querySelectorAll<HTMLButtonElement>('button')]
		.find((one) => one.textContent?.trim() === 'Remove')!
		.click();
	await vi.waitFor(() => expect(mocks.removeWaitingFingerprints).toHaveBeenCalledWith('e1'), {
		interval: 1
	});
});

it('says how many confirmed faces a waiting entry had where its file was made', async () => {
	mocks.waitingFingerprints.mockResolvedValue({
		items: [
			{
				entry_id: 'e1',
				name: 'Neve Arbor',
				faces: 64,
				confirmed: 210,
				source: 'Studio Faces',
				added_at: 0
			},
			{ entry_id: 'e2', name: 'Orla Tennant', faces: 4, source: 'Gallery', added_at: 0 }
		]
	});
	await draw(true, {});

	const group = host.querySelector('[id="faces.waiting"]')!.closest('section')!;
	await vi.waitFor(() => expect(group.textContent).toContain('Orla Tennant'), { interval: 1 });
	const text = group.textContent?.replace(/\s+/g, ' ') ?? '';
	expect(text).toContain('64 faces of 210 confirmed');
	expect(text).toContain('4 faces');
});

it('marks a person facial fingerprints created with the file they came from', async () => {
	mocks.knownPeople.mockResolvedValue({
		items: [
			{ id: 'p1', name: 'Ada Byron', faces: 2, starters: 0, from_fingerprints: 'Studio Faces' },
			{ id: 'p2', name: 'Cass Ivory', faces: 2, starters: 0, from_fingerprints: null }
		],
		total: 2
	});
	await draw(true, {});

	const marks = () =>
		[
			...(host
				.querySelector('[id="faces.people"]')
				?.closest('section')
				?.querySelectorAll('.came-from') ?? [])
		].map((one) => one.textContent?.trim());
	await vi.waitFor(
		() => expect(marks()).toEqual(['Created from facial fingerprints in Studio Faces']),
		{ interval: 1 }
	);
});

it('says the time left in the one formatter, and its own words below the sample', async () => {
	const { COPY } = await import('./Faces.search');
	expect(COPY.timeLeft(null)).toBe('Not enough to say yet');
	expect(COPY.timeLeft(20 * 60)).toMatch(/^[A-Z].* left$/);
	expect(faces).not.toContain('estimating the time left');
});

it('takes a fingerprints file in with recognition off and says nothing is recognized until it is on', async () => {
	const { importPack } = await import('$lib/people/fingerprint-packs');
	vi.mocked(importPack).mockResolvedValue({
		added: 4,
		people: 1,
		recognizing: false
	} satisfies PackImported);
	await draw(false, { enabled: false, ready: false });
	const input = host.querySelector<HTMLInputElement>('input[accept=".zip,application/zip"]');
	expect(input).not.toBeNull();
	const file = new File(['x'], 'facial-fingerprints.zip', { type: 'application/zip' });
	Object.defineProperty(input, 'files', { value: [file], configurable: true });
	input?.dispatchEvent(new Event('change', { bubbles: true }));
	await tick();
	host.querySelector<HTMLButtonElement>('[id="faces.pack-import"] .control button')?.click();
	await vi.waitFor(
		() => expect(host.textContent?.replace(/\s+/g, ' ')).toContain('Took in 4 faces of 1 person.'),
		{ interval: 1 }
	);
	expect(host.textContent).not.toContain('started recognizing');

	const said = [...host.querySelectorAll('[id="faces.pack-import"] p.small')].map((one) =>
		one.textContent?.replace(/\s+/g, ' ').trim()
	);
	expect(said).toContain(
		"Sift won't recognize anyone until Recognize faces in your library is on."
	);
	const link = host.querySelector<HTMLAnchorElement>('[id="faces.pack-import"] p.small a');
	expect(link?.textContent?.trim()).toBe('Recognize faces in your library');
	expect(link?.getAttribute('href')).toContain('faces.enabled');
});

/* A file only holds facial fingerprints: the press sends the file and nothing else, whatever the
 * switch under it says, and the answer names nobody and asks nothing. Who each one is, is the pass's
 * to decide by face. */
it.each([true, false])(
	'sends the fingerprints file alone with the switch %s, and says what it took in and that recognizing started',
	async (on) => {
		const { importPack } = await import('$lib/people/fingerprint-packs');
		vi.mocked(importPack).mockResolvedValue({
			added: 37,
			people: 3,
			recognizing: true
		} satisfies PackImported);
		await draw(true, {}, [
			entry('faces.people_from_files', {
				label: 'Create people from these fingerprints as their faces are recognized',
				value: on
			})
		]);
		const input = host.querySelector<HTMLInputElement>('input[accept=".zip,application/zip"]');
		const file = new File(['x'], 'facial-fingerprints.zip', { type: 'application/zip' });
		Object.defineProperty(input, 'files', { value: [file], configurable: true });
		input?.dispatchEvent(new Event('change', { bubbles: true }));
		await tick();
		host.querySelector<HTMLButtonElement>('[id="faces.pack-import"] .control button')?.click();
		await vi.waitFor(() => expect(importPack).toHaveBeenCalled(), { interval: 1 });

		expect(vi.mocked(importPack).mock.calls.at(-1)).toEqual([file]);
		await vi.waitFor(
			() => expect(host.querySelector('[id="faces.pack-import"] p.status')).not.toBeNull(),
			{
				interval: 1
			}
		);
		const said = host
			.querySelector('[id="faces.pack-import"] p.status')
			?.textContent?.replace(/\s+/g, ' ')
			.trim();
		expect(said).toBe(
			'Took in 37 faces of 3 people. Sift has started recognizing who they are in your library.'
		);
		expect(host.textContent).not.toContain('added to People');
	}
);

it('says a file whose faces were all here already brought nothing, rather than a zero', async () => {
	const { importPack } = await import('$lib/people/fingerprint-packs');
	vi.mocked(importPack).mockResolvedValue({
		added: 0,
		people: 3,
		recognizing: true
	} satisfies PackImported);
	await draw(true, {});
	const input = host.querySelector<HTMLInputElement>('input[accept=".zip,application/zip"]');
	const file = new File(['x'], 'facial-fingerprints.zip', { type: 'application/zip' });
	Object.defineProperty(input, 'files', { value: [file], configurable: true });
	input?.dispatchEvent(new Event('change', { bubbles: true }));
	await tick();
	host.querySelector<HTMLButtonElement>('[id="faces.pack-import"] .control button')?.click();
	await vi.waitFor(
		() =>
			expect(host.textContent).toContain('Nothing new in this file. Its faces were already here.'),
		{ interval: 1 }
	);
});

/* A folder is held by its faces, never by its name: no switch makes a person of a folder's
 * name, and the one row is the press, followed by `FolderImportRow`. */
it('draws the folder import as one press with no switch for making people', async () => {
	await draw(true, {});
	expect(host.querySelector('[id="faces.folder-create"]')).toBeNull();
	expect(host.textContent).not.toContain("Create a person for each name this library doesn't have");
	expect(host.querySelector('[id="faces.folder-import"] .control button')).not.toBeNull();
});

it('says nothing about the switch after a file taken in with recognition on', async () => {
	const { importPack } = await import('$lib/people/fingerprint-packs');
	vi.mocked(importPack).mockResolvedValue({
		added: 4,
		people: 2,
		recognizing: true
	} satisfies PackImported);
	await draw(true, {});
	const input = host.querySelector<HTMLInputElement>('input[accept=".zip,application/zip"]');
	const file = new File(['x'], 'facial-fingerprints.zip', { type: 'application/zip' });
	Object.defineProperty(input, 'files', { value: [file], configurable: true });
	input?.dispatchEvent(new Event('change', { bubbles: true }));
	await tick();
	host.querySelector<HTMLButtonElement>('[id="faces.pack-import"] .control button')?.click();
	await vi.waitFor(
		() => expect(host.textContent?.replace(/\s+/g, ' ')).toContain('Took in 4 faces of 2 people.'),
		{ interval: 1 }
	);

	expect(host.textContent).not.toContain("won't recognize anyone");
});
