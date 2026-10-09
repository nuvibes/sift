<script lang="ts">
	/*
	 * Settings > Tasks and Activity > Tasks: WHEN every piece of work Sift does over the library runs.
	 *
	 * ## What is on it
	 *
	 * Quiet hours at the top: one row saying the range, the install's one clock, with its two
	 * times and the keep-awake switch on its own page behind Edit. Then Import tasks (`Importing`:
	 * Scan, Generate and Identify, each with its Edit, every other task folded under them as Other
	 * tasks, then Folder-specific import settings), and Activity, which the screen hands in. Each
	 * task is one row: its When (as files arrive or on a schedule, during quiet hours, or only when
	 * pressed), its press, how its last run ended, when the next one is and how much is waiting.
	 *
	 * Presses live here and nowhere else in Settings: the panes that own a task draw its row
	 * without one. Upkeep nobody times (the prunes, the update check) is not on the list at all.
	 *
	 * ## One row, two doors
	 *
	 * The row is `TaskWhen`, and the section that OWNS the task draws the very same component
	 * beside the thing it does: Faces beside recognition, Music beside its lookup. The owning
	 * section decides WHAT the task does; this is the overview of WHEN every task runs. One
	 * setting, one component, two doors, so the two cannot disagree, because there is only one of
	 * each.
	 *
	 * ## Every setting filed here is drawn here
	 *
	 * The pane reads its whole section and draws each entry exactly once: a task's When as that
	 * task's row, a task's own settings under its row (how often the backup runs, how long
	 * quarantined files are kept), quiet hours at the top, and anything else under "Other settings"
	 * at the end. A setting filed here that nothing drew would be registered, stored, acted on and
	 * on no screen anybody could open.
	 *
	 * A task's When is the one exception: it is drawn by its task's row or not at all. The server
	 * leaves upkeep nobody times out of the list, and its When stays filed here; drawn as a plain
	 * row it would be the control the list deliberately does not offer.
	 *
	 * No table of recent runs: History is the one screen that answers "what ran", and it keeps a
	 * task's runs for longer than a job row's week. The row keeps the one fact that belongs beside
	 * the choice: how the LAST run ended.
	 */
	import { onMount, type Snippet } from 'svelte';
	import { Button, LabelledRow, Problem, Skeleton } from '$lib/components/common';
	import { STAGES, quietRange, quietTime, taskList, type TaskView } from '$lib/jobs/tasks.svelte';
	import Importing from './Importing.svelte';
	import SettingGroup from './SettingGroup.svelte';
	import SettingRow from './SettingRow.svelte';
	import TaskWhen from './TaskWhen.svelte';
	import { drilldown } from './drilldown.svelte';
	import { SettingsPanel } from './panel.svelte';
	import { COPY } from './ScheduledTasks.search';
	import Fold from '$lib/components/common/Fold.svelte';
	import { explainAbsentRows, hiddenWhile } from '$lib/settings-ui/settings-anchor.svelte';

	interface Props {
		/** The queue, Activity, drawn under Other tasks. Tasks and Activity hands it in. */
		activity?: Snippet;
	}

	let { activity }: Props = $props();

	const panel = new SettingsPanel();

	/* The settings section this pane is the screen for. Every entry in it is drawn below. */
	const SECTION = 'Scheduled tasks';

	/* Quiet hours' three rows, in reading order. Named because the pane draws them first, above
	   every task, and apart from them; each is still an ordinary registry row. */
	const QUIET_KEYS = ['tasks.quiet_from', 'tasks.quiet_until', 'tasks.keep_awake'] as const;

	const section = $derived(panel.sections.find((one) => one.name === SECTION));
	const declared = $derived(section?.settings ?? []);

	/* What a task row already draws: its When, and the settings that decide what it does. */
	const claimed = $derived(
		new Set<string>([
			...QUIET_KEYS,
			...taskList.tasks.flatMap((one) => [one.when_key, ...one.setting_keys])
		])
	);
	/* A When (`tasks.<id>.when`, the one shape the server gives every task's When) is its row's to
	   draw. And nothing is unclaimed until the list has come back: before it, every task's settings
	   would be drawn here for a moment and a link to one would ring the row that then vanished. */
	const WHEN_KEY = /^tasks\.[^.]+\.when$/;
	const unclaimed = $derived(
		taskList.view === null
			? []
			: declared.filter((entry) => !claimed.has(entry.key) && !WHEN_KEY.test(entry.key))
	);

	/* The three stages are Import tasks; everything else is folded under them. Read from the list
	   the server sends, so a task it leaves out is on neither. */
	const others = $derived(
		taskList.tasks.filter((one) => !(STAGES as readonly string[]).includes(one.id))
	);

	/* The range as a person reads it: "11 PM to 7 AM", the words every "During quiet hours" on a
	   task row carries after it (`quietRange`), so the row and the choices never say it two ways. */
	const range = $derived.by(() => {
		const quiet = taskList.view?.quiet_hours;
		return quiet ? quietRange(quiet) : '';
	});

	/* The two times and the keep-awake switch are the quiet-hours row's own page. A link naming any
	   of them opens it first, the way every sub-page answers a deep link. */
	function openQuiet(): void {
		drilldown.open(COPY.quiet.heading, quietPage, COPY.quiet.editShort);
	}
	$effect(() => drilldown.own(QUIET_KEYS, openQuiet));

	/* A task's setting its When leaves out (`drawn_keys`) is still searched for and pasted: the
	   task's When is rung in its place, and the sentence names the answer that hides it. */
	$effect(() =>
		explainAbsentRows((key) => {
			if (panel.loading) return null;
			const task = taskList.tasks.find(
				(one) => one.setting_keys.includes(key) && !one.drawn_keys.includes(key)
			);
			const label = panel.entry(key)?.label;
			if (!task || !label) return null;
			const chosen = task.whens.find((one) => one.value === task.when);
			const answer = chosen ? taskList.whenLabel(chosen.value, chosen.label) : task.cadence;
			return { because: hiddenWhile(label, task.title, answer), near: task.when_key };
		})
	);

	/* Where quiet hours stand this moment, in one sentence. The times are the two the setting
	   holds, in the shape the row's range says them, so the row never says one time two ways. */
	const quietNow = $derived.by(() => {
		const quiet = taskList.view?.quiet_hours;
		if (!quiet) return null;
		if (quiet.open && quiet.closes_at === null) return COPY.quiet.allDay;
		if (quiet.open) return COPY.quiet.openNow(quietTime(quiet.ends));
		return COPY.quiet.nextAt(quietTime(quiet.starts));
	});

	onMount(() => {
		void taskList.ensure();
		void panel.load();
	});
</script>

{#snippet quietState()}
	{#if quietNow}
		<span>
			{quietNow}
			{#if taskList.view?.awake_now}{COPY.quiet.awakeNow}{/if}
		</span>
	{/if}
{/snippet}

<!-- The quiet-hours row's page: the two times and whether the device is kept awake for them. -->
{#snippet quietPage()}
	<SettingGroup>
		{#each panel.pick(...QUIET_KEYS) as entry (entry.key)}
			<SettingRow
				{entry}
				value={panel.value(entry.key)}
				onchange={(next: unknown) => panel.save(entry.key, next)}
			/>
		{/each}
	</SettingGroup>
{/snippet}

<!-- One task's row and the settings that decide what it does, each drawn once. Only those that
     mean something under its When now, as the server says (`drawn_keys`): How often is nothing
     while only a press runs the backup, and its time of day nothing during quiet hours. A row hidden
     this way is still claimed above, so it never falls through to "Other settings". -->
{#snippet taskRows(task: TaskView)}
	<TaskWhen task={task.id} />
	{@render settingRows(task)}
{/snippet}
{#snippet settingRows(task: TaskView)}
	{#each panel.pick(...task.drawn_keys) as entry (entry.key)}
		<SettingRow
			{entry}
			value={panel.value(entry.key)}
			onchange={(next: unknown) => panel.save(entry.key, next)}
		/>
	{/each}
{/snippet}

<!-- Folded under the stages, which are what most people come here for. A deep link to a row in
     here opens it, as it opens any closed disclosure. -->
{#snippet otherTasks()}
	{#if others.length > 0}
		<Fold summary={COPY.others.name} id="tasks.others">
			<SettingGroup help={COPY.others.help}>
				{#each others as task (task.id)}
					{@render taskRows(task)}
				{/each}
			</SettingGroup>
		</Fold>
	{/if}
{/snippet}

<section>
	<p class="lede">{COPY.lede}</p>

	<SettingGroup>
		<LabelledRow
			id="tasks.quiet-hours"
			label={COPY.quiet.heading}
			help={COPY.quiet.help}
			foot={quietNow ? quietState : undefined}
		>
			<div class="quiet-hours">
				<span class="range">{range}</span>
				<Button icon="edit" onclick={openQuiet} aria-label={COPY.quiet.edit}
					>{COPY.quiet.editShort}</Button
				>
			</div>
		</LabelledRow>
	</SettingGroup>

	{#if taskList.view === null && !taskList.failed}
		<Skeleton lines={4} />
	{:else if taskList.failed}
		<!-- Deliberately not "nothing is scheduled". A request that failed says nothing about what
		     runs, and the reassuring reading of silence here is the wrong one. -->
		<Problem message={COPY.cannotLoad} />
	{:else}
		<Importing rows={settingRows} after={otherTasks} />
	{/if}

	{#if activity}
		<div class="activity">
			<SettingGroup id="tasks.activity" heading={COPY.activity.heading} help={COPY.activity.help} />
			{@render activity()}
		</div>
	{/if}

	{#if unclaimed.length > 0}
		<SettingGroup heading={COPY.other.name} help={COPY.other.help}>
			{#each unclaimed as entry (entry.key)}
				<SettingRow
					{entry}
					value={panel.value(entry.key)}
					onchange={(next: unknown) => panel.save(entry.key, next)}
				/>
			{/each}
		</SettingGroup>
	{/if}
</section>

<style>
	.activity {
		margin-block-start: var(--space-8);
	}

	.lede {
		margin: 0 0 var(--space-4);
		color: var(--sift-ink-2);
		font: var(--text-body);
	}

	/* The range as the row's value, and its Edit, ending at the column's far edge. */
	.quiet-hours {
		display: flex;
		align-items: center;
		justify-content: var(--row-pack, flex-end);
		gap: var(--space-3);
		inline-size: 100%;
	}

	.range {
		font: var(--text-body);
		color: var(--sift-ink-2);
		font-variant-numeric: tabular-nums;
		white-space: nowrap;
	}
</style>
