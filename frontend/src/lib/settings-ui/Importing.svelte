<script lang="ts">
	/* Import tasks, on the Tasks tab of Tasks and Activity: Scan, Generate and Identify.
	 *
	 * A file lands, and Sift SCANS it to find out what it is, GENERATES the rest (the pictures, the
	 * fingerprints) and IDENTIFIES what is in it (the faces, the meaning, the watermarks). Each stage
	 * is its task's row (`TaskWhen`): the stage's Edit before Run now, and after the task's own facts
	 * what the stage still has to do, priced in wall time, with the files a product gave up on.
	 * Identify is three tasks; its Edit page holds their three switches. Folder-specific import
	 * settings follow the stages. How much Sift does at the same time is Concurrency, on Performance.
	 */
	import { onMount, type Snippet } from 'svelte';
	import { counted } from '$lib/entity/entity-counts';
	import {
		fetchSettings,
		refusalOf,
		saveSettings,
		type SettingEntry
	} from '$lib/settings-ui/settings';
	import { settingChanges, whenChanged } from '$lib/library/changes.svelte';
	import RecognitionSection from './RecognitionSection.svelte';
	import { RECOGNITION_SWITCHES } from './recognition-switches';
	import SettingGroup from './SettingGroup.svelte';
	import SettingRow from './SettingRow.svelte';
	import ImportingFolders from './ImportingFolders.svelte';
	import { drilldown } from './drilldown.svelte';
	import {
		GENERATE_KEYS,
		GENERATE_PRODUCTS,
		IDENTIFY_PRODUCTS,
		SCAN_KEYS,
		fetchBuildSheet,
		retryBuild,
		type BuildRow,
		type BuildSheet as Sheet
	} from '$lib/library/importing';
	import { Button, Note, SettingLink } from '$lib/components/common';
	import { NOT_ENOUGH_TO_SAY, sayWindow } from '$lib/shell/when';
	import { STAGES, taskList, type TaskView } from '$lib/jobs/tasks.svelte';
	import { COPY as TASKS } from './ScheduledTasks.search';
	import TaskWhen from './TaskWhen.svelte';
	import TasksLeftOut from './TasksLeftOut.svelte';
	import { leftOut } from '$lib/jobs/left-out.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { COPY } from './Importing.search';

	/* What is in a hover clip. Owned by the server's performance module, a question about the
	   way in. */
	const PREVIEW_SHAPE_KEY = 'performance.preview_shape';

	/* Whether a proposed shoot becomes a Photo Set by itself. Filed with Importing and drawn on
	   Scan's page, beside the other things a scan files on its own. */
	const SHOOTS_KEY = 'shoots.auto_file';

	interface Props {
		/** The settings a task's When leaves meaningful, drawn under its row by the Tasks pane. */
		rows?: Snippet<[TaskView]>;
		/** What the Tasks pane draws under the stages, before the folders: Other tasks. */
		after?: Snippet;
	}

	let { rows, after }: Props = $props();

	/* The stages in the order work happens to a file, as the server lists them. */
	const stages = $derived(
		STAGES.map((id) => taskList.row(id)).filter((one): one is TaskView => one !== undefined)
	);

	let entries = $state<Map<string, SettingEntry>>(new Map());
	/* The counts under each stage. Re-read after every switch, because what is missing changes with
	   what is switched on: turning previews on makes every file without one part of Generate. */
	let sheet = $state<Sheet | null>(null);
	let retrying = $state<string | null>(null);
	let generation = 0;

	onMount(() => {
		void load();
		void taskList.ensure();
	});

	/* And again when a setting moves somewhere else: this account in a browser, a second window,
	 * or another admin changing one the installation shares. Every control on this pane writes on
	 * the press and holds nothing unsaved, so a re-read can only put the same value back; see
	 * `scripts/check_settings_followed.js`, which holds every pane to this. */
	whenChanged(settingChanges, () => void load());

	async function load() {
		const mine = generation;
		try {
			const sections = await fetchSettings();
			const all = sections.flatMap((section) => section.settings ?? []);
			const read = await fetchBuildSheet();
			if (mine !== generation) return;
			entries = new Map(all.map((entry) => [entry.key, entry]));
			sheet = read;
		} catch {
			// Left as it was. A pane that blanked itself over one dropped request would hide every
			// switch on it, and the switches are the point.
		}
	}

	async function save(key: string, value: unknown) {
		generation += 1;
		const entry = entries.get(key);
		const previous = entry?.value;
		if (entry) entries.set(key, { ...entry, value });
		entries = new Map(entries);
		try {
			await saveSettings({ [key]: value });
			sheet = await fetchBuildSheet();
		} catch (error) {
			if (entry) entries.set(key, { ...entry, value: previous });
			entries = new Map(entries);
			toasts.show(refusalOf(error), { tone: 'error' });
		}
	}

	function rowsFor(keys: readonly string[]) {
		return keys.map((key) => entries.get(key)).filter((entry) => entry !== undefined);
	}

	/** The sheet's rows for one stage, in the server's order. Empty until the sheet has been read. */
	function rowsOf(products: readonly string[]): BuildRow[] {
		return (sheet?.rows ?? []).filter((row) => products.includes(row.key));
	}

	/*
	 * One product's name, agreeing with the number in front of it.
	 *
	 * The server declares each product's label once, in the plural, because that is how it reads
	 * everywhere else it is drawn, and this is the one place a count is put in front of it, so
	 * this is where the agreement belongs. Left alone it would say "1 thumbnails".
	 *
	 * A trailing "s" taken off, and nothing cleverer. It is right for every label the sheet can
	 * carry (thumbnails, hover previews, sprites, fingerprints, faces, watermarks, music
	 * fingerprints), and right by doing nothing for "meaning", which has no "s" to take. A label
	 * that is plural without one, or a mass noun that ends in one, would need the server to send
	 * both forms; there is a test over the real labels so the day that is added is the day it is
	 * noticed.
	 */
	function naming(label: string, files: number): string {
		const lower = label.toLocaleLowerCase();
		return files === 1 && lower.endsWith('s') ? lower.slice(0, -1) : lower;
	}

	/*
	 * What a stage's row says beside its button: how many of EACH thing is missing, by name, and
	 * the window this machine's recent runs say that would take.
	 *
	 * Per product rather than one total, because the products cost nothing like each other:
	 * pictures for a library are minutes, faces are a day, and "16,000 thumbnails, 40,000 hover
	 * previews" is what lets somebody decide to switch the slow one off before pressing. A product
	 * with nothing missing is left out rather than listed as zero.
	 *
	 * ## THE TIME IS WALL TIME, AND IT SAYS WHAT IT ASSUMES
	 *
	 * The sum of `files x seconds_per_file` is WORKER time, not wall time. Several jobs run at
	 * once, so the two differ by however many: enough to read a day's run as four, and four days
	 * is a number somebody decides against pressing.
	 *
	 * The server sends wall seconds at the cheapest and the dearest of its recent runs, shared out
	 * between the products of each run, so the sum across a stage is still a stage's wall time
	 * rather than several copies of it. What it assumes is that the next run gets the machine the
	 * last one had, which is why the number of jobs at the same time is said out loud rather than
	 * buried: halve it and the wait doubles.
	 */
	function countNote(products: readonly string[]): string | undefined {
		if (sheet === null) return undefined;
		const missing = rowsOf(products).filter((row) => row.files > 0);
		// Nothing missing is not the word for files that were tried and could not be made: those
		// are the row's foot line, and a note beside it would contradict it.
		if (missing.length === 0)
			return cannotIn(products).length > 0 ? undefined : COPY.nothingMissing;
		const parts = missing.map(
			(row) => `${row.files.toLocaleString()} ${naming(row.label, row.files)}`
		);
		const timed = missing.every(
			(row) => typeof row.quick_seconds === 'number' && typeof row.slow_seconds === 'number'
		);
		const quick = missing.reduce((sum, row) => sum + (row.quick_seconds ?? 0), 0);
		const slow = missing.reduce((sum, row) => sum + (row.slow_seconds ?? 0), 0);
		/* Below the sample (no run of this stage priced yet) the window is said as not known yet,
		   through the one reader every estimate goes through, rather than dropped: a count with no
		   time beside it read as a count with nothing to add. */
		const about = sayWindow(timed ? quick : null, timed ? slow : null);
		if (about === NOT_ENOUGH_TO_SAY) return COPY.untimed(parts.join(', '), about);
		/* The rows of one stage are priced from one run, so they agree about this; the first that
		   has it is the run's own number. Left off where the run predates it being recorded:
		   "with undefined tasks at the same time" is worse than a sentence that stops early. */
		const at = missing.find((row) => (row.jobs_at_once ?? 0) > 0)?.jobs_at_once;
		const assuming = at ? COPY.withTasks(at) : '';
		return COPY.about(parts.join(', '), about, assuming);
	}

	/** The products in a stage a run has given up on, with how many files each. */
	function cannotIn(products: readonly string[]): BuildRow[] {
		return rowsOf(products).filter((row) => row.cannot > 0);
	}

	/** Forget what a product gave up on, so its files are offered again. Queues nothing. */
	async function retry(key: string) {
		if (retrying) return;
		retrying = key;
		try {
			await retryBuild([key]);
			sheet = await fetchBuildSheet();
			void leftOut.read();
		} catch (error) {
			toasts.show(refusalOf(error), { tone: 'error' });
		} finally {
			retrying = null;
		}
	}

	/* What the Scan stage says under its row. During a scan the count of files waiting to be read
	   is the scan's own backlog, so it is said as one rather than as a fault that is growing. */
	const scanNote = $derived.by(() => {
		if (sheet === null) return undefined;
		if (sheet.unread === 0) return COPY.scan.nothingWaiting;
		return COPY.scan.waiting(counted(sheet.unread));
	});

	/* Each stage's Edit page. Opened by its row's Edit, and by a deep link to any row on it: the
	   settings the page holds are claimed, so a link names the setting and the page opens first. */
	function openScan(): void {
		drilldown.open(COPY.scan.pageTitle, scanPage, COPY.edit);
	}
	function openGenerate(): void {
		drilldown.open(COPY.generate.pageTitle, generatePage, COPY.edit);
	}
	function openIdentify(): void {
		drilldown.open(COPY.identify.pageTitle, identifyPage, COPY.edit);
	}
	$effect(() => drilldown.own([...SCAN_KEYS, SHOOTS_KEY], openScan));
	$effect(() => drilldown.own([...GENERATE_KEYS, PREVIEW_SHAPE_KEY], openGenerate));
	/* The three recognition switches are on Identify's page, which each feature's own pane links
	   to, and so is the group they sit in, which the search finds by name. */
	const RECOGNITION_GROUP = 'importing.recognition';
	$effect(() => drilldown.own([RECOGNITION_GROUP, ...RECOGNITION_SWITCHES], openIdentify));
</script>

{#snippet scanPage()}
	{#each rowsFor([...SCAN_KEYS, SHOOTS_KEY]) as entry (entry.key)}
		<SettingRow {entry} value={entry.value} onchange={(value) => save(entry.key, value)} />
	{/each}
{/snippet}

{#snippet generatePage()}
	{#each rowsFor(GENERATE_KEYS) as entry (entry.key)}
		<SettingRow {entry} value={entry.value} onchange={(value) => save(entry.key, value)} />
	{/each}
	<!-- What is IN a hover clip, once there is one. A choice rather than a switch, and it sits
	     under the switches that decide whether a clip exists at all. -->
	{#each rowsFor([PREVIEW_SHAPE_KEY]) as entry (entry.key)}
		<SettingRow {entry} value={entry.value} onchange={(value) => save(entry.key, value)} />
	{/each}
{/snippet}

{#snippet identifyPage()}
	<!-- recognition switches -->
	<RecognitionSection />
{/snippet}

<!-- Each stage's Edit, before its Run now. -->
{#snippet scanEdit()}
	<Button
		id="importing.scan-settings"
		icon="edit"
		onclick={openScan}
		aria-label={COPY.editStage(COPY.scan.heading)}>{COPY.edit}</Button
	>
{/snippet}
{#snippet generateEdit()}
	<Button
		id="importing.generate-settings"
		icon="edit"
		onclick={openGenerate}
		aria-label={COPY.editStage(COPY.generate.heading)}>{COPY.edit}</Button
	>
{/snippet}
{#snippet identifyEdit()}
	<Button
		id="importing.identify-settings"
		icon="edit"
		onclick={openIdentify}
		aria-label={COPY.editStage(COPY.identify.heading)}>{COPY.edit}</Button
	>
{/snippet}

<!-- What each stage still has to do, on its row's foot line after the task's own facts. -->
{#snippet scanFacts()}
	{#if scanNote}<span class="count sentence" data-count="scan">{scanNote}</span>{/if}
{/snippet}
{#snippet generateFacts()}
	{#if countNote(GENERATE_PRODUCTS)}
		<span class="count sentence" data-count="generate">{countNote(GENERATE_PRODUCTS)}</span>
	{/if}
	{@render gaveUp(GENERATE_PRODUCTS, COPY.generate.cannot)}
{/snippet}
{#snippet identifyFacts()}
	{#if countNote(IDENTIFY_PRODUCTS)}
		<span class="count sentence" data-count="identify">{countNote(IDENTIFY_PRODUCTS)}</span>
	{/if}
	{@render gaveUp(IDENTIFY_PRODUCTS, COPY.identify.cannot)}
{/snippet}

<!-- The files a product gave up on (one that will not decode, one with no frame to cut) are
     their own line, never folded into the count: folded in, they would be offered by every run for
     ever. Trying again forgets the verdicts and re-reads the counts; the person decides whether to
     run.

     The count opens those files, each with why (`TasksLeftOut`), counted as the `left_out:` wall
     counts them for this viewer, so the number and the list agree (`BuildRow.cannot`). -->
{#snippet gaveUp(products: readonly string[], said: typeof COPY.generate.cannot)}
	{#each cannotIn(products) as row (row.key)}
		<!-- A sentence, so it wraps like one: only a fact (a count, a time) keeps to one line.
		     DRESSED BY: .sentence (the foot line holds every fact but this one to one line) -->
		{@const words = said(row.cannot === 1, row.label.toLocaleLowerCase())}
		{@const lastBreak = words.lastIndexOf(' ')}
		<!-- The sentence's last word rides with the press, so a narrow line breaks inside the
		     sentence and never leaves the press alone under it. -->
		<span class="sentence">
			<span data-left-out={row.key}
				><TasksLeftOut products={[row]} title={row.label}
					>{COPY.leftOut(row.cannot, row.cannot === 1)}</TasksLeftOut
				></span
			>
			{words.slice(0, lastBreak + 1)}<span class="with-press"
				>{words.slice(lastBreak + 1)}
				<Button
					tone="link"
					size="small"
					disabled={retrying !== null}
					onclick={() => void retry(row.key)}
				>
					{retrying === row.key ? COPY.tryingAgain : COPY.tryAgain}
				</Button></span
			>
		</span>
	{/each}
{/snippet}

{#if sheet?.measure_first}
	<!-- The note stands a group apart from the heading under it, as every block on a pane does. -->
	<div class="note-block">
		<Note tone="info"
			>{COPY.notMeasured.said}
			<SettingLink section={COPY.notMeasured.section} setting={COPY.notMeasured.setting}
				>{COPY.notMeasured.link}</SettingLink
			>.</Note
		>
	</div>
{/if}

<SettingGroup id="tasks.stages" heading={TASKS.stages.heading} help={TASKS.stages.help}>
	{#each stages as task (task.id)}
		{#if task.id === 'scan'}
			<TaskWhen id="tasks.scan.when" task="scan" beside={scanEdit} more={scanFacts} />
		{:else if task.id === 'generate'}
			<TaskWhen
				id="tasks.generate.when"
				task="generate"
				beside={generateEdit}
				more={generateFacts}
			/>
		{:else if task.id === 'identify'}
			<TaskWhen
				id="tasks.identify.when"
				task="identify"
				beside={identifyEdit}
				more={identifyFacts}
			/>
		{/if}
		{@render rows?.(task)}
	{/each}
</SettingGroup>

{@render after?.()}

<ImportingFolders />

<style>
	/* A count is a figure, in tabular figures so it does not jitter as it moves. */
	.count {
		font-variant-numeric: tabular-nums;
	}

	/* The last word of a sentence and the press after it, kept on one line. */
	.with-press {
		white-space: nowrap;
	}

	/* The note over the stages stands a group apart from the heading under it. */
	.note-block {
		margin-block-end: var(--space-6);
	}
</style>
