<script lang="ts">
	/* Maintenance: the dials, the one-off jobs, and the ways in to the piles they govern. */
	import { onMount } from 'svelte';
	import { jobChanges, libraryChanges, whenChanged } from '$lib/library/changes.svelte';
	import { describeLastChecked } from '$lib/shell/updates.svelte';
	import { ConfirmDialog, Empty, MoreAbout, Problem, Skeleton } from '$lib/components/common';
	import { splitHelp } from './help-split';
	import { formatBytes } from './maintenance-state.svelte';
	import { Tidy, type Leftovers } from './tidy.svelte';
	import ActionRow from './ActionRow.svelte';
	import SettingGroup from './SettingGroup.svelte';
	import SettingRow from './SettingRow.svelte';
	import { SettingsPanel } from './panel.svelte';
	import { COPY } from './Maintenance.search';
	import { drawnOn, explainAbsentRows } from '$lib/settings-ui/settings-anchor.svelte';

	const tidy = new Tidy();

	/* How long quarantined files are kept: the one registered setting on this pane. */
	const KEEP_DAYS_KEY = 'quarantine.keep_days';
	const panel = new SettingsPanel();
	onMount(() => void panel.load());

	/* How alike two files must be and which copy stays are Organize's Near duplicates controls,
	   drawn beside the groups they decide. */
	$effect(() =>
		explainAbsentRows((key) => {
			const label = key.startsWith('dedup.') ? panel.entry(key)?.label : undefined;
			if (!label || panel.loading) return null;
			return drawnOn(label, { href: '/organize/duplicates', label: COPY.duplicatesScreen });
		})
	);

	/* The log: what it records and how much disk it may use. */
	let tidying = $state<Leftovers | null>(null);
	let tidyOpen = $state(false);

	function askToTidy(one: Leftovers) {
		tidying = one;
		tidyOpen = true;
	}

	async function doTidy() {
		if (tidying) await tidy.run(tidying);
	}

	/* The sentence in front of an irreversible button. */
	const tidyConsequence = $derived.by(() => {
		if (!tidying) return '';
		const frees = tidying.frees_bytes ? COPY.tidy.frees(formatBytes(tidying.frees_bytes)) : '';
		return `${COPY.tidy.consequence(tidying.count ?? 0, tidying.noun, tidying.nouns)} ${tidying.detail}${frees}`;
	});

	let problem = $state<string | undefined>(undefined);

	/* Loaded on arrival, and again whenever the library's shape changes underneath. */
	$effect(() => {
		void libraryChanges.generation;
		/* No duplicate read here: loading BOTH duplicate lists for two counts beside two buttons
		   means, on a large library, clustering every pending pair every time somebody opens
		   Maintenance to change something else entirely. */
		void tidy.load();
	});

	/* And the tidy list again whenever the queue moves: the counts that read the disk are taken
	   by a job, and the moment it finishes is the moment the numbers change. */
	whenChanged(jobChanges, () => void tidy.load());

	async function doSurvey() {
		await tidy.survey();
	}

	/* Under the survey row's help: when the counts that read the disk were last taken, or that
	   they are being taken, or that they never have been. */
	const surveyStanding = $derived.by(() => {
		if (tidy.surveying) return COPY.survey.counting;
		if (tidy.lastSurveyed === null) return COPY.survey.never;
		return COPY.survey.counted(describeLastChecked(tidy.lastSurveyed));
	});

	/* What a row's figure says. A count that has never been taken says so, rather than reading
	   as nothing to remove. */
	function standing(one: Leftovers): string {
		if (one.count === null) return COPY.survey.never;
		if (one.count === 0) return COPY.none;
		const said = COPY.howMany(one.count, one.noun, one.nouns);
		return one.frees_bytes ? COPY.toFree(said, formatBytes(one.frees_bytes)) : said;
	}

	let rebuildOpen = $state(false);
	let restyleOpen = $state(false);

	async function doOptimize() {
		problem = await tidy.optimize();
	}

	async function doRebuild() {
		problem = await tidy.rebuildThumbnails();
	}

	async function doRestyle() {
		problem = await tidy.rebuildPreviews();
	}

	/* Beside the button: how many files it is about, or what the last press did. */
	const rebuildStanding = $derived.by(() => {
		if (tidy.rebuilding !== null) return COPY.rebuild.queued(tidy.rebuilding);
		if (tidy.rebuildable === null) return COPY.cannotCount;
		return COPY.files(tidy.rebuildable);
	});

	/* Beside the preview button. */
	const restyleStanding = $derived.by(() => {
		if (tidy.restyling) return COPY.restyle.generating;
		if (tidy.restyleable === null) return COPY.cannotCount;
		if (tidy.restyleable === 0) return COPY.restyle.upToDate;
		return COPY.files(tidy.restyleable);
	});
</script>

<section>
	<!-- One line, and assertive: these are failures, and `status` is the polite register that a
	     screen reader may hold until it is finished with whatever else it was saying. -->
	<Problem message={problem} />

	<!-- ------------------------------------------------------------------ upkeep -->
	<!-- Three acts that remove no data, as the page's first group under its title and away from
	     the deletes below. Each is minutes of the machine on a large library, so each says how much
	     it is about before it is pressed. -->
	<SettingGroup>
		<ActionRow
			label={COPY.optimize.name}
			help={COPY.optimize.help}
			note={tidy.optimized
				? tidy.optimized.freed > 0
					? COPY.optimize.freed(formatBytes(tidy.optimized.freed), formatBytes(tidy.optimized.now))
					: COPY.optimize.nothingFreed(formatBytes(tidy.optimized.now))
				: undefined}
			action={COPY.optimize.action}
			disabled={tidy.busy}
			onclick={doOptimize}
		/>

		<ActionRow
			id="maintenance.rebuild"
			label={COPY.rebuild.name}
			help={COPY.rebuild.help}
			note={rebuildStanding}
			action={COPY.rebuild.action}
			disabled={tidy.busy || tidy.rebuildable === null || tidy.rebuildable === 0}
			onclick={() => (rebuildOpen = true)}
		/>

		<ActionRow
			label={COPY.restyle.name}
			help={COPY.restyle.help}
			note={restyleStanding}
			action={COPY.restyle.action}
			disabled={tidy.busy || !tidy.restyleable}
			onclick={() => (restyleOpen = true)}
		/>
	</SettingGroup>

	<!-- ------------------------------------------------------------------ tidy up -->
	<!-- One group: the count row first, then the list of tidyings the counts are about, with the
	     line between rows. -->
	<SettingGroup id="maintenance.tidy" heading={COPY.cleanup.name} help={COPY.cleanup.help}>
		<!-- The counts that read the disk are taken on request, as a job, and kept: reading every
		     picture Sift has made takes seconds on a fast disk and much longer on a share. -->
		<ActionRow
			id="maintenance.survey"
			label={COPY.survey.name}
			help={COPY.survey.help}
			action={COPY.survey.action}
			icon="search"
			disabled={tidy.surveying}
			onclick={doSurvey}
		>
			{surveyStanding}
		</ActionRow>

		<Problem message={tidy.problem} />

		{#if tidy.loading && !tidy.loaded}
			<Skeleton lines={3} />
		{:else if !tidy.loaded}
			<Problem message={COPY.cannotCheck} />
		{:else}
			{#if !tidy.anythingToDo}
				<Empty scope="block">
					{COPY.nothing}
					{#if tidy.lastRun}
						{COPY.deleted(tidy.lastRun.removed, tidy.lastRun.noun, tidy.lastRun.nouns)}
					{/if}
				</Empty>
			{:else if tidy.lastRun}
				<p class="total">
					<strong class="data">{tidy.lastRun.removed}</strong>
					{COPY.deletedFrom(tidy.lastRun.removed, tidy.lastRun.noun, tidy.lastRun.nouns)}
					{tidy.lastRun.title.toLowerCase()}.
				</p>
			{/if}
			<!-- Every kind is listed, including the ones with nothing to remove. -->
			{#each tidy.worthDoing as one (one.name)}
				<!-- The count is the row's value and Delete its verb, disabled at none: the row says
				     what the control is FOR even when there is nothing for it to do. The server's
				     description is two sentences of help and the rest under More about this. -->
				{@const said = splitHelp(one.detail)}
				<ActionRow
					label={one.title}
					help={said.help}
					note={standing(one)}
					action={COPY.delete}
					actionLabel={COPY.deleteWhat(one.title)}
					destructive
					disabled={tidy.busy || !one.count}
					onclick={() => askToTidy(one)}
				>
					{#if said.more}<MoreAbout text={said.more} />{/if}
				</ActionRow>
			{/each}
		{/if}
	</SettingGroup>

	<!-- ------------------------------------------------------------------- quarantine -->
	<!-- How long they are kept. The sweep runs by itself once a day in quiet hours and shows in
	     Activity; nobody decides when, so no When is drawn for it. -->
	<SettingGroup
		id="maintenance.quarantine"
		heading={COPY.quarantine.name}
		help={COPY.quarantine.help}
	>
		{#each panel.pick(KEEP_DAYS_KEY) as entry (entry.key)}
			<SettingRow
				{entry}
				value={panel.value(entry.key)}
				onchange={(next: unknown) => panel.save(entry.key, next)}
			/>
		{/each}
	</SettingGroup>
</section>

<ConfirmDialog
	bind:open={rebuildOpen}
	title={COPY.rebuild.title}
	consequence={COPY.rebuild.consequence(tidy.rebuildable ?? 0)}
	confirmLabel={COPY.rebuild.action}
	onconfirm={doRebuild}
/>

<ConfirmDialog
	bind:open={restyleOpen}
	title={COPY.restyle.title}
	consequence={COPY.restyle.consequence(tidy.restyleable ?? 0)}
	confirmLabel={COPY.restyle.action}
	onconfirm={doRestyle}
/>

<ConfirmDialog
	bind:open={tidyOpen}
	title={tidying ? COPY.tidy.title(tidying.title.toLowerCase()) : ''}
	consequence={tidyConsequence}
	confirmLabel={COPY.tidy.confirm}
	destructive
	onconfirm={doTidy}
/>

<!-- The log is Activity's Log tab. See `Logs.svelte`. -->

<style>
	.total {
		margin: 0 0 var(--space-4);
		color: var(--sift-ink-2);
		font: var(--text-body);
	}

	/* Machine facts line up in a column, so a stack of sizes can be read down rather than across. */
	.data {
		font: var(--text-data);
		font-variant-numeric: tabular-nums;
	}
</style>
