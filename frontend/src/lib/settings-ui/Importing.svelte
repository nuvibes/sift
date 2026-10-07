<script lang="ts">
	/* What Sift does with a file as it arrives: Scan, Generate and Identify.
	 *
	 * Three stages over one moment. A file lands, and Sift SCANS it to find out what it is and
	 * give it a picture, then GENERATES the rest (the pictures, the fingerprints) and IDENTIFIES
	 * what is in it (the faces, the meaning, the watermarks). This pane decides WHAT each stage
	 * does; WHEN it does it is each stage's task.
	 *
	 * ## Three identical stage rows, and nothing here starts work or times it
	 *
	 * Each stage is one row: the stage's Edit, and a foot line of facts (when it runs, as a link to
	 * the row on Tasks where that is chosen, and what is still missing). Every When is chosen on
	 * Tasks and nowhere else, beside the press, so when work runs and starting it live in one
	 * place. A stage's on/off is that When ("off" is "only when I press it"), and the server keeps
	 * the stage switch answering through it for every folder's own answer and every caller.
	 *
	 * Identify is three tasks (faces, Smart Search, watermarks). Its When is a reading of theirs,
	 * the shared answer or Mixed. Its Edit page holds the three switches.
	 *
	 * ## What stays here
	 *
	 * What each stage makes (the Edit pages) and what is missing: the per-product counts, priced
	 * in wall time, and the files a product gave up on with Try again. Those are about the stage,
	 * not about when it runs. How much Sift does at the same time is Concurrency, on Performance.
	 */
	import { onMount } from 'svelte';
	import { counted } from '$lib/entity/entity-counts';
	import {
		fetchSettings,
		refusalOf,
		saveSettings,
		type SettingEntry
	} from '$lib/settings-ui/settings';
	import { settingChanges, whenChanged } from '$lib/library/changes.svelte';
	import LabelledRow from '$lib/components/common/LabelledRow.svelte';
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
	import { taskList } from '$lib/jobs/tasks.svelte';
	import { labelFor } from './sections';
	import { COPY as TASKS } from './ScheduledTasks.search';
	import { leftOutWall } from '$lib/library/left-out';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { COPY } from './Importing.search';

	/* What is in a hover clip. Owned by the server's performance module, a question about the
	   way in. */
	const PREVIEW_SHAPE_KEY = 'performance.preview_shape';

	/* Whether a proposed shoot becomes a Photo Set by itself. Filed with Importing and drawn on
	   Scan's page, beside the other things a scan files on its own. */
	const SHOOTS_KEY = 'shoots.auto_file';

	/* The three stages, by the id of the task that is each one. */
	type Stage = 'scan' | 'generate' | 'identify';

	/** When a stage runs, in the words its row on Tasks ticks: "During quiet hours (11 PM to 7 AM)",
	 * or Mixed while Identify's three tasks disagree. Null until the list has come back. */
	function whenOf(stage: Stage): string | null {
		const row = taskList.row(stage);
		if (!row) return null;
		const chosen = row.whens.find((one) => one.value === row.when);
		return chosen ? taskList.whenLabel(chosen.value, chosen.label) : TASKS.when.mixed;
	}

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

{#snippet scanEdit()}
	<Button icon="edit" onclick={openScan} aria-label={COPY.editStage(COPY.scan.heading)}
		>{COPY.edit}</Button
	>
{/snippet}
{#snippet generateEdit()}
	<Button icon="edit" onclick={openGenerate} aria-label={COPY.editStage(COPY.generate.heading)}
		>{COPY.edit}</Button
	>
{/snippet}
{#snippet identifyEdit()}
	<Button icon="edit" onclick={openIdentify} aria-label={COPY.editStage(COPY.identify.heading)}
		>{COPY.edit}</Button
	>
{/snippet}

<!-- When a stage runs, said as the answer chosen on Tasks and a link to the row it is chosen on. -->
{#snippet whenFact(stage: Stage)}
	{@const said = whenOf(stage)}
	{#if said}
		<span class="sentence" data-fact="when"
			>{COPY.chosenOn(said)}
			<SettingLink section="tasks" setting="tasks.{stage}.when">{labelFor('tasks')}</SettingLink
			></span
		>
	{/if}
{/snippet}

<!-- What each stage still has to do, as facts on its row's foot line, after when it runs. -->
{#snippet scanFacts()}
	<p class="facts">
		{@render whenFact('scan')}
		{#if scanNote}<span class="count sentence" data-count="scan">{scanNote}</span>{/if}
	</p>
{/snippet}
{#snippet generateFacts()}
	<p class="facts">
		{@render whenFact('generate')}
		{#if countNote(GENERATE_PRODUCTS)}
			<span class="count sentence" data-count="generate">{countNote(GENERATE_PRODUCTS)}</span>
		{/if}
		{@render gaveUp(GENERATE_PRODUCTS, COPY.generate.cannot)}
	</p>
{/snippet}
{#snippet identifyFacts()}
	<p class="facts">
		{@render whenFact('identify')}
		{#if countNote(IDENTIFY_PRODUCTS)}
			<span class="count sentence" data-count="identify">{countNote(IDENTIFY_PRODUCTS)}</span>
		{/if}
		{@render gaveUp(IDENTIFY_PRODUCTS, COPY.identify.cannot)}
	</p>
{/snippet}

<!-- The files a product gave up on (one that will not decode, one with no frame to cut) are
     their own line, never folded into the count: folded in, they would be offered by every run for
     ever. Trying again forgets the verdicts and re-reads the counts; the person decides whether to
     run.

     The count is a link to those files: the Files wall filtered `left_out:<product>`, where each
     one says under its tile why it was left out. The server counts them as that wall does, for
     this viewer, so the number and the wall it opens agree (`BuildRow.cannot`). -->
{#snippet gaveUp(products: readonly string[], said: typeof COPY.generate.cannot)}
	{#each cannotIn(products) as row (row.key)}
		<!-- A sentence, so it wraps like one: only a fact (a count, a time) keeps to one line.
		     DRESSED BY: .sentence (the foot line holds every fact but this one to one line) -->
		{@const words = said(row.cannot === 1, row.label.toLocaleLowerCase())}
		{@const lastBreak = words.lastIndexOf(' ')}
		<!-- The sentence's last word rides with the press, so a narrow line breaks inside the
		     sentence and never leaves the press alone under it. -->
		<span class="sentence">
			<a href={leftOutWall(row.key)} data-left-out={row.key}
				>{COPY.leftOut(row.cannot, row.cannot === 1)}</a
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

<!-- Each stage's sentence is its task's own line, the one Tasks draws under the same task, so the
     two doors say one thing. -->
<SettingGroup
	id="importing.scan-stage"
	heading={COPY.scan.heading}
	help={taskList.row('scan')?.explain}
>
	<LabelledRow id="importing.scan-settings" label={COPY.scan.pageTitle} wide foot={scanFacts}>
		{@render scanEdit()}
	</LabelledRow>
</SettingGroup>

<SettingGroup
	id="importing.generate-stage"
	heading={COPY.generate.heading}
	help={taskList.row('generate')?.explain}
>
	<LabelledRow
		id="importing.generate-settings"
		label={COPY.generate.pageTitle}
		wide
		foot={generateFacts}
	>
		{@render generateEdit()}
	</LabelledRow>
</SettingGroup>

<SettingGroup
	id="importing.identify-stage"
	heading={COPY.identify.heading}
	help={taskList.row('identify')?.explain}
>
	<LabelledRow
		id="importing.identify-settings"
		label={COPY.identify.pageTitle}
		wide
		foot={identifyFacts}
	>
		{@render identifyEdit()}
	</LabelledRow>
</SettingGroup>

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

	/* The facts, read left to right under the label: each one a phrase, the line wrapping between
	   them and never inside one, except a fact that is a sentence (a count with its wait is one). */
	.facts {
		display: flex;
		flex-wrap: wrap;
		column-gap: var(--space-4);
		row-gap: var(--space-1);
		margin: 0;
	}

	.facts > :global(*:not(.sentence)) {
		white-space: nowrap;
	}
</style>
