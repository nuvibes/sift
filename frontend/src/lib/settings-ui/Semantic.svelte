<script lang="ts">
	/* Search by meaning: the switch, what it runs on, and the two jobs it can be asked to do.
	 *
	 * Three things this screen must not overstate, because each is a sentence somebody would rely
	 * on:
	 *
	 * **Some machines cannot do this at all.** The index needs an add-on that SQLite has to be able
	 * to load, and a build without that will never run this however the switch is set. So
	 * `supported` is drawn first and, when it is false, the switch is not offered: a control that
	 * cannot work is worse than an absent one, because pressing it teaches nothing.
	 *
	 * **Sift ships no models.** Switched on is not the same as able to run: the files are fetched by
	 * whoever runs it, from their publisher, on their terms. That is why `ready` is its own line
	 * rather than folded into the switch: a fresh install that read as broken would send people
	 * looking for a fault that is not there.
	 *
	 * **Turning it off keeps the index.** Deliberate: somebody switching it off for a week has not
	 * asked to spend hours rebuilding it afterwards. Which is exactly why the control that removes
	 * it is here, separate, and says what it costs to press.
	 *
	 * The controls themselves are drawn from what the feature declared rather than written out
	 * again. Every one has a label and an explanation registered beside it in the code that owns it,
	 * and a second copy of those words here would be a second thing to keep true.
	 */
	import { onMount, onDestroy } from 'svelte';
	import { ConfirmDialog, Problem, ProgressBar, SettingLink } from '$lib/components/common';
	import {
		fetchSettings,
		refusalOf,
		saveSettings,
		type SettingEntry
	} from '$lib/settings-ui/settings';
	import { settingChanges, whenChanged } from '$lib/library/changes.svelte';
	import SettingRow from './SettingRow.svelte';
	import SwitchPointer, { namesOf } from './SwitchPointer.svelte';
	import { explainAbsentRows, NOTHING_TO_DELETE } from '$lib/settings-ui/settings-anchor.svelte';
	import {
		SEMANTIC_ENABLED_KEY as ENABLED_KEY,
		SEMANTIC_DEVICE_KEY as DEVICE_KEY,
		fetchSemanticModels,
		removeSemanticIndex,
		semanticStatus,
		type SemanticStatus
	} from '$lib/search/semantic.svelte';
	// The two runs are followed OUTSIDE this component. See the module. A watcher held here would
	// die with the pane, leaving somebody who pressed it no idea whether it worked.
	import { describing, modelFetch } from '$lib/jobs/semantic-runs.svelte';
	import { indexRemoval } from '$lib/jobs/watch-removal.svelte';
	import { describeWait } from '$lib/jobs/waiting';
	import { joinCounts } from '$lib/entity/entity-counts';
	import { toasts } from '$lib/shell/toasts.svelte';
	import SettingGroup from './SettingGroup.svelte';
	import ActionRow from './ActionRow.svelte';
	import RecognitionPane, {
		DANGER_HEADING,
		deviceWords,
		openPage,
		type SubPage
	} from './RecognitionPane.svelte';
	import { filedUnder } from './drilldown.svelte';
	import { COPY, DELETE_INDEX, SEARCHABLE, semanticStatusLine } from './Semantic.search';

	const MODEL_KEY = 'semantic.model';

	/* Which settings belong on the More settings page, and the order they read in.
	 *
	 * Written out rather than matched on the `semantic.` prefix: the prefix is a detail of the key
	 * and would drag in anything later named that way. The page itself holds only the consent and
	 * when it runs; which models and what to run them on are for somebody tuning it. Smart Search
	 * has no dial for how thorough it is, and none is invented here.
	 */
	const FIELDS = [MODEL_KEY, DEVICE_KEY];

	let entries = $state<Map<string, SettingEntry>>(new Map());
	let enabled = $state(false);
	let status = $state<SemanticStatus | null>(null);
	let loadFailed = $state(false);
	let removing = $state(false);
	/* Describing the library is no button of this pane's. It is the Smart Search task's Run now,
	   on Tasks where every press lives, and it runs the Build for the Meaning product, so there is
	   one way to start it and it is the same everywhere. */
	let generation = 0;

	onMount(() => {
		void load();
		// A run already going is picked up rather than ignored, so opening this screen halfway
		// through one shows the run rather than the button that starts it.
		void modelFetch.resume();
		void indexRemoval.resume();
		void describing.attach();
	});

	/* And again when a setting moves somewhere else: this account in a browser, a second window,
	 * or another admin changing one the installation shares. Every control on this pane writes on
	 * the press and holds nothing unsaved, so a re-read can only put the same value back; see
	 * `scripts/check_settings_followed.js`, which holds every pane to this. */
	whenChanged(settingChanges, () => void load());

	onDestroy(() => {
		// Only the polling stops. The fetch goes on being followed at module level, which is the
		// whole point of it living there.
		describing.detach();
	});

	async function load() {
		const mine = generation;
		try {
			const sections = await fetchSettings();
			const all = sections.flatMap((section) => section.settings ?? []);
			const state = await semanticStatus();
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
			// Every one of these changes what the feature can report about itself: turning it on
			// does not make it ready, and changing the models makes every described file stale. So
			// the status is re-read rather than guessed at.
			status = await semanticStatus();
		} catch (error) {
			if (entry) entries.set(key, { ...entry, value: previous });
			entries = new Map(entries);
			// The server's own words when it sent any. Choosing a graphics card this installation
			// cannot drive is refused with a sentence saying so, and a generic toast would hide it.
			toasts.show(refusalOf(error), { tone: 'error' });
		}
	}

	async function getModels(again = false) {
		try {
			const started = await fetchSemanticModels(again);
			modelFetch.follow(started.job_id);
		} catch {
			modelFetch.couldNotStart(COPY.models.couldNotStart);
		}
	}

	async function removeIndex() {
		removing = false;
		try {
			const started = await removeSemanticIndex();
			if (started.job_id) indexRemoval.follow(started.job_id);
		} catch {
			indexRemoval.couldNotStart();
		}
	}

	/* Where Smart Search stands, in one line under the switch. Off is a status and not a blank: a
	   page that says nothing with the switch off reads the same as one that failed to load. The words
	   are shared with the switch on Importing; a run followed here has the fresher counts. */
	const line = $derived(
		semanticStatusLine(
			status,
			enabled,
			status ? deviceWords(entries.get(DEVICE_KEY), status.device) : '',
			describing.status ? { done: describing.done, left: describing.left } : undefined
		)
	);
	const needsModels = $derived(enabled && status !== null && status.supported && !status.ready);

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
			key === 'semantic.forget' && status !== null && status.indexed_frames === 0
				? { because: NOTHING_TO_DELETE, near: 'semantic.status' }
				: null
		)
	);

	/* What is drawn only while it is on, for a link landing on it while it is off. */
	const offRows = $derived(namesOf([...morePage.keys, 'semantic.more'], entries, SEARCHABLE));
</script>

<!-- More settings: which models, what they run on, and downloading them again. -->
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
		     already here. "Download the models" over models that are on the disk is an instruction to
		     somebody who has already followed it. -->
		<SettingGroup heading={COPY.models.heading} help={COPY.models.help}>
			<ActionRow
				id="semantic.models"
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
	<!-- A device that cannot hold the index gets no switch: not a switch that is off, but a
	     sentence saying no setting will change it. A control that cannot work is worse than an
	     absent one, because pressing it teaches nothing. -->
	{@const consentEntry = status?.supported === false ? undefined : entries.get(ENABLED_KEY)}
	<RecognitionPane
		id="semantic.status"
		on={enabled && status?.supported !== false}
		status={line}
		statusReady={enabled && status?.ready === true}
		statusCaution={needsModels || (status !== null && !status.supported)}
		task="smart-search"
		{pages}
	>
		{#snippet consent()}
			<!-- The switch itself is under Importing; this says where. -->
			{#if consentEntry}<SwitchPointer entry={consentEntry} on={enabled} hides={offRows} />{/if}
		{/snippet}
		{#snippet setup()}
			{#if needsModels || (enabled && status?.supported && modelFetch.running)}
				<!-- Three states, and they must be three. A bar with no percentage says only that
				     something is happening, and a button that goes back to looking exactly as it did
				     before the press says nothing about whether the press worked. -->
				<ActionRow
					id="semantic.download"
					label={COPY.models.heading}
					help={COPY.models.help}
					action={modelFetch.running ? COPY.models.downloading : COPY.models.download}
					busy={modelFetch.running}
					onclick={() => void getModels(false)}
				>
					{#if modelFetch.running}
						<ProgressBar value={modelFetch.fraction * 100} label={COPY.models.bar} />
						<p class="status">{modelFetch.status}</p>
						<p>{COPY.models.size}</p>
					{/if}
				</ActionRow>
			{/if}
		{/snippet}
		{#snippet work()}
			{#if enabled && status?.supported}
				{#if modelFetch.outcome}
					<p>{modelFetch.outcome}</p>
				{/if}

				<!--
					The bar measures FILES DESCRIBED, not files queued: the run only puts work in the
					queue and finishes in seconds, while the describing goes on for hours. So this
					counts the thing itself, from two numbers the server already computes, which is
					also what makes it survive leaving the screen.
				-->
				{#if describing.running && describing.fraction !== null}
					<ProgressBar value={describing.fraction * 100} label="Describing your library" />
					<p class="status">
						<!-- The counts' own separator (`joinCounts`), and grouped like every other count. -->
						{joinCounts(
							`${describing.done.toLocaleString()} of ${(describing.done + describing.left).toLocaleString()}`,
							`${Math.round(describing.fraction * 100)}%`
						)}
						{#if describing.remaining !== null}
							&middot; {describeWait(describing.remaining)}
						{/if}
					</p>
					<p>{COPY.leave}</p>
				{:else}
					{#if status.described_by_another_model > 0}
						<!-- The model changed. Those files' numbers are the previous model's: in the index,
						     out of every search until they are described again, and already in the count
						     of what is still to do. -->
						<p>{COPY.previous(status.described_by_another_model)}</p>
					{/if}
					<!--
						Stopped is not finished, and it must not read as either progress or completion.
						Switching the feature off cancels the queued work, and switching back on does
						not bring it back, so the honest screen says how many were left and what to
						press. What to press is the task's Run now, on Tasks, where every press lives.
					-->
					{#if describing.stopped}
						<p>
							{COPY.stopped(describing.left)}
							{COPY.stoppedHow}
							<SettingLink section="tasks" setting="tasks.smart-search.when"
								>{COPY.stoppedWhere}</SettingLink
							>.
						</p>
					{:else if describing.outcome}
						<p>{describing.outcome}</p>
					{/if}
				{/if}
			{/if}
		{/snippet}
		{#snippet rows()}
			{#if enabled && status?.supported}
				<ActionRow
					id="semantic.more"
					label={COPY.more.label}
					help={COPY.more.help}
					action={COPY.more.action}
					onclick={() => openPage(morePage)}
				/>
			{/if}
		{/snippet}
		{#snippet danger()}
			{#if status && status.indexed_frames > 0}
				<SettingGroup heading={DANGER_HEADING}>
					<ActionRow
						id="semantic.forget"
						label={DELETE_INDEX.name}
						help={COPY.forget.help}
						action={COPY.forget.action}
						destructive
						busy={indexRemoval.running}
						onclick={() => (removing = true)}
					/>
				</SettingGroup>
			{/if}
		{/snippet}
	</RecognitionPane>
{/if}

<ConfirmDialog
	bind:open={removing}
	title="Delete the Smart Search index?"
	consequence={'Every description Sift has made is deleted. Nothing else in your library ' +
		'changes. Describing the library again opens every file again.'}
	confirmLabel="Delete index"
	onconfirm={() => void removeIndex()}
/>

<style>
	/* The percentage beside the bar. Tabular figures so the number does not jitter sideways as it
	   climbs, which is the whole reason a progress reading is hard to read. */
	.status {
		color: var(--sift-ink-2);
		font: var(--text-data);
		font-variant-numeric: tabular-nums;
	}
</style>
