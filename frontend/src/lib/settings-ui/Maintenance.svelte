<script lang="ts">
	/* Maintenance: the dials, the one-off jobs, and the ways in to the piles they govern.
	 *
	 * The piles of work (near duplicates, exact copies, and the two halves of what the gate on
	 * the way in refused) are cards on the Organize board, where a decision is written into the
	 * record and can be taken back, which a settings screen cannot offer.
	 *
	 * What stays is what a settings screen is for and has nowhere else to be: the tidyings, the
	 * two housekeeping jobs and how long quarantined files are kept. What left this device is
	 * Activity's History filtered to "Saved to a device", pointed at from Privacy's Save to device,
	 * beside the switch that decides who may save.
	 *
	 * **A dial and the pile it governs are read together**, so the two duplicate dials sit at the
	 * top of the duplicate queue itself, beside the rule that marks a keeper: the closeness
	 * dial belongs on the only list that shows what moving it does. Counts here would be a second
	 * copy of counts the board already draws, with buttons that only opened the screen those
	 * counts came from.
	 *
	 * Nothing on this screen deletes anything without a confirm naming what goes and what it
	 * frees.
	 */
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

	/* How long quarantined files are kept: the one registered setting on this pane. The sweep that
	   deletes them is upkeep nobody times, so the number is all a person sets about it. */
	const KEEP_DAYS_KEY = 'quarantine.keep_days';
	const panel = new SettingsPanel();
	onMount(() => void panel.load());

	/* How alike two files must be and which copy stays are Organize's Near duplicates controls,
	   drawn beside the groups they decide. A search or a link naming one is sent on there. */
	$effect(() =>
		explainAbsentRows((key) => {
			const label = key.startsWith('dedup.') ? panel.entry(key)?.label : undefined;
			if (!label || panel.loading) return null;
			return drawnOn(label, { href: '/organize/duplicates', label: COPY.duplicatesScreen });
		})
	);

	/* The log: what it records and how much disk it may use. Together, because they are two
	   halves of one question: detail fills a file many times faster, so somebody turning it
	   on is the person who most needs to see the size beside it. */
	let tidying = $state<Leftovers | null>(null);
	let tidyOpen = $state(false);

	function askToTidy(one: Leftovers) {
		tidying = one;
		tidyOpen = true;
	}

	async function doTidy() {
		if (tidying) await tidy.run(tidying);
	}

	/* The sentence in front of an irreversible button. It names the count, what they are, and what
	   is freed where there is anything to free, never "are you sure". */
	const tidyConsequence = $derived.by(() => {
		if (!tidying) return '';
		const frees = tidying.frees_bytes ? COPY.tidy.frees(formatBytes(tidying.frees_bytes)) : '';
		return `${COPY.tidy.consequence(tidying.count ?? 0, tidying.noun, tidying.nouns)} ${tidying.detail}${frees}`;
	});

	let problem = $state<string | undefined>(undefined);

	/*
	 * Loaded on arrival, and again whenever the library's shape changes underneath.
	 *
	 * Both lists here are ABOUT which files exist: one holds assets stored in more than one place,
	 * the other pairs that look alike. Deleting one of a pair anywhere else in the application
	 * settles that pair, and a screen that loaded once, on mount, would go on offering a review
	 * of a file that was already gone, with a Delete button pointed at it. Reading the signal is
	 * what makes it re-ask; the server says what is left, and nothing here tries to work it out.
	 */
	$effect(() => {
		void libraryChanges.generation;
		/* No duplicate read here: loading BOTH duplicate lists for two counts beside two buttons
		   means, on a large library, clustering every pending pair every time somebody opens
		   Maintenance to change something else entirely. The counts live on the Organize board,
		   where the work is. */
		void tidy.load();
	});

	/* And the tidy list again whenever the queue moves: the counts that read the disk are taken
	   by a job, and the moment it finishes is the moment the numbers change. */
	whenChanged(jobChanges, () => void tidy.load());

	async function doSurvey() {
		await tidy.survey();
	}

	/* Under the survey row's help: when the counts that read the disk were last taken, or that they
	   are being taken, or that they never have been. Never a bare nothing. It is the row's foot
	   rather than a figure beside the button, because beside it the pair outgrows the control
	   column and stacks. */
	const surveyStanding = $derived.by(() => {
		if (tidy.surveying) return COPY.survey.counting;
		if (tidy.lastSurveyed === null) return COPY.survey.never;
		return COPY.survey.counted(describeLastChecked(tidy.lastSurveyed));
	});

	/* What a row's figure says. A count that has never been taken says so, rather than reading as
	   nothing to remove. */
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

	/* Beside the button: how many files it is about, or what the last press did. Never a bare
	   nothing: the number is the whole reason the count is asked for before the run. */
	const rebuildStanding = $derived.by(() => {
		if (tidy.rebuilding !== null) return COPY.rebuild.queued(tidy.rebuilding);
		if (tidy.rebuildable === null) return COPY.cannotCount;
		return COPY.files(tidy.rebuildable);
	});

	/* Beside the preview button. "None" is the ordinary answer on a library that is up to date, and
	   it is worth saying rather than leaving blank: a bare nothing beside a disabled button reads
	   as a screen that failed to load. */
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
			<!--
			Every kind is listed, including the ones with nothing to remove.

			Drawing only what has built up would make a tidying at zero indistinguishable from one
			that does not exist: somebody looking for the control that clears a particular thing
			would find an empty space and no way to tell whether they were looking in the wrong
			place. So the list is what Sift can tidy, and the count says how much of it there is.

			The ones with something to do come first, so it still reads as a work list.
		-->
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

<!-- The log is Activity's Log tab. See `Logs.svelte`. A log is looked for by name on the day
     something is wrong, and this is the pane about duplicates and orphaned files. -->

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
