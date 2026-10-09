<!--
	Reading the Site's own mark off the picture. The pane says three things and nothing else: whether
	it can run, what it has read, and the two controls.
-->
<script lang="ts">
	import { onMount } from 'svelte';
	import { ConfirmDialog, Problem, ProgressBar } from '$lib/components/common';
	import {
		fetchSettings,
		refusalOf,
		saveSettings,
		type SettingEntry
	} from '$lib/settings-ui/settings';
	import { settingChanges, whenChanged } from '$lib/library/changes.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import {
		WATERMARKS_ENABLED_KEY as ENABLED_KEY,
		WATERMARKS_DEVICE_KEY as DEVICE_KEY,
		fetchWatermarkModels,
		forgetWatermarkReads,
		watermarkStatus,
		type WatermarkStatus
	} from '$lib/library/watermarks.svelte';
	import { modelFetch } from '$lib/jobs/watermarks-runs.svelte';
	import SettingRow from './SettingRow.svelte';
	import SwitchPointer, { namesOf } from './SwitchPointer.svelte';
	import { explainAbsentRows, NOTHING_TO_DELETE } from '$lib/settings-ui/settings-anchor.svelte';
	import SettingGroup from './SettingGroup.svelte';
	import ActionRow from './ActionRow.svelte';
	import RecognitionPane, {
		DANGER_HEADING,
		deviceWords,
		openPage,
		type SubPage
	} from './RecognitionPane.svelte';
	import { filedUnder } from './drilldown.svelte';
	import { COPY, SEARCHABLE, WATERMARK_RESULTS, watermarksStatusLine } from './Watermarks.search';

	/* Which settings belong on the More settings page, and the order they read in. */
	const FIELDS = [DEVICE_KEY];

	let entries = $state<Map<string, SettingEntry>>(new Map());
	let enabled = $state(false);
	let status = $state<WatermarkStatus | null>(null);
	let loadFailed = $state(false);
	let forgetting = $state(false);

	/* A read already in flight must not undo a press made while it was in flight. */
	let generation = 0;

	const consentEntry = $derived(entries.get(ENABLED_KEY));

	onMount(() => {
		void load();
		// A download already going is picked up rather than ignored, so opening this screen halfway
		// through one shows the run rather than the button that starts it.
		void modelFetch.resume();
	});

	whenChanged(settingChanges, () => void load());

	async function load() {
		const mine = generation;
		try {
			const sections = await fetchSettings();
			const all = sections.flatMap((section) => section.settings ?? []);
			const state = await watermarkStatus().catch(() => null);
			if (mine !== generation) return;
			entries = new Map(all.map((entry) => [entry.key, entry]));
			enabled = Boolean(entries.get(ENABLED_KEY)?.value);
			status = state;
		} catch {
			if (mine === generation) loadFailed = true;
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
			status = await watermarkStatus().catch(() => status);
		} catch (error) {
			if (entry) entries.set(key, { ...entry, value: previous });
			entries = new Map(entries);
			// The server's own words when it sent any. Choosing a graphics card this installation
			// cannot drive is refused with a sentence saying so, and a generic message would hide it.
			toasts.show(refusalOf(error), { tone: 'error' });
		}
	}

	async function getModels(again = false) {
		try {
			const started = await fetchWatermarkModels(again);
			modelFetch.follow(started.job_id);
		} catch {
			modelFetch.couldNotStart(COPY.models.couldNotStart);
		}
	}

	/* Reading the library is not a button of this pane's. */

	async function forget() {
		try {
			const gone = await forgetWatermarkReads();
			toasts.show(`${gone.removed} watermark results deleted. Sites on your files didn't change.`);
			status = await watermarkStatus().catch(() => status);
		} catch (error) {
			toasts.show(refusalOf(error), { tone: 'error' });
		}
	}

	/* Where reading stands, in one line under the switch. */
	const line = $derived(
		watermarksStatusLine(
			status,
			enabled,
			status ? deviceWords(entries.get(DEVICE_KEY), status.device) : ''
		)
	);
	const needsModels = $derived(enabled && status !== null && !status.ready);

	const morePage: SubPage = $derived({
		title: COPY.more.label,
		/* The hand rows drawn on the page are the ones its search entries are filed under. */
		keys: [...FIELDS, ...filedUnder(SEARCHABLE, COPY.more.label)],
		body: moreSettings,
		door: COPY.more.action
	});
	const pages = $derived(enabled ? [morePage] : []);
	/* The way out is drawn only once there is something to delete. */
	$effect(() =>
		explainAbsentRows((key) =>
			key === 'watermarks.forget' && status !== null && status.read_files === 0
				? { because: NOTHING_TO_DELETE, near: 'watermarks.status' }
				: null
		)
	);

	/* What is drawn only while it is on, for a link landing on it while it is off. */
	const offRows = $derived(namesOf([...morePage.keys, 'watermarks.more'], entries, SEARCHABLE));
</script>

<!-- More settings: what the scan runs on, and downloading the models again. -->
{#snippet moreSettings()}
	<SettingGroup>
		{#each FIELDS as key (key)}
			{@const entry = entries.get(key)}
			{#if entry}
				<SettingRow
					{entry}
					value={entry.value}
					onchange={(next: unknown) => void save(key, next)}
				/>
			{/if}
		{/each}
	</SettingGroup>
	{#if status?.ready && !modelFetch.running}
		<!-- Named for what pressing it would DO, which is not the same sentence once the files are
		     already here. -->
		<SettingGroup heading={COPY.models.heading} help={COPY.models.help}>
			<ActionRow
				id="watermarks.models"
				label={COPY.models.again}
				help={COPY.models.againHelp}
				action={COPY.models.againPress}
				actionLabel={COPY.models.again}
				onclick={() => void getModels(true)}
			/>
		</SettingGroup>
	{/if}
{/snippet}

{#if loadFailed}
	<Problem message="These settings couldn't be loaded. Reload the page to try again." />
{:else}
	<RecognitionPane
		id="watermarks.status"
		on={enabled}
		status={line}
		statusReady={enabled && status?.ready === true}
		statusCaution={needsModels}
		task="watermarks"
		{pages}
	>
		{#snippet consent()}
			<!-- The switch itself is under Importing; this says where. -->
			{#if consentEntry}<SwitchPointer entry={consentEntry} on={enabled} hides={offRows} />{/if}
		{/snippet}
		{#snippet work()}
			{#if enabled && status}
				{#if modelFetch.outcome}
					<p>{modelFetch.outcome}</p>
				{/if}
				{#if status.ready}
					<!-- "Scanned" and "with a watermark" are different numbers on purpose: about half
					     of a typical library carries no mark at all, so a scan that read forty thousand
					     files and found twenty thousand marks has not failed at anything. -->
					<p>{COPY.about}</p>
				{/if}
			{/if}
		{/snippet}
		{#snippet setup()}
			{#if needsModels || (enabled && modelFetch.running)}
				<!-- Three states, and they must be three. -->
				<ActionRow
					id="watermarks.download"
					label={COPY.models.heading}
					help={COPY.models.help}
					action={modelFetch.running ? COPY.models.downloading : COPY.models.download}
					busy={modelFetch.running}
					onclick={() => void getModels(false)}
				>
					{#if modelFetch.running}
						<ProgressBar value={modelFetch.fraction * 100} label={COPY.models.bar} />
						<p class="status">{modelFetch.status}</p>
					{/if}
				</ActionRow>
			{/if}
		{/snippet}
		{#snippet rows()}
			{#if enabled}
				<ActionRow
					id="watermarks.more"
					label={COPY.more.label}
					help={COPY.more.help}
					action={COPY.more.action}
					onclick={() => openPage(morePage)}
				/>
			{/if}
		{/snippet}
		{#snippet danger()}
			{#if status && status.read_files > 0}
				<SettingGroup heading={DANGER_HEADING}>
					<ActionRow
						id="watermarks.forget"
						label={WATERMARK_RESULTS.name}
						help={COPY.forget.help}
						action={COPY.forget.action}
						destructive
						onclick={() => (forgetting = true)}
					/>
				</SettingGroup>
			{/if}
		{/snippet}
	</RecognitionPane>
{/if}

<ConfirmDialog
	bind:open={forgetting}
	title="Delete the watermark results?"
	consequence={'Every watermark result is deleted, and the next scan checks every file again. ' +
		'Sites already added to files stay.'}
	confirmLabel="Delete results"
	onconfirm={() => void forget()}
/>

<style>
	/* The percentage beside the bar. Tabular figures so the number does not jitter sideways as
	   it climbs, which is the whole reason a progress reading is hard to read. */
	.status {
		color: var(--sift-ink-2);
		font: var(--text-data);
		font-variant-numeric: tabular-nums;
	}
</style>
