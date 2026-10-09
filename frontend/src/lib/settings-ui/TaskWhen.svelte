<script lang="ts">
	/* One task's row: its press (Run now, with when it runs and the other ways to run it behind
	 * the chevron), and what it last did and does next. */
	import { onMount, type Snippet } from 'svelte';
	import {
		Badge,
		Button,
		LabelledRow,
		Popover,
		Select,
		SettingLink,
		SplitButton,
		Tooltip,
		type SelectOption
	} from '$lib/components/common';
	import ContextMenuGroup from '$lib/components/common/ContextMenuGroup.svelte';
	import ContextMenuItem from '$lib/components/common/ContextMenuItem.svelte';
	import NarrowBox from '$lib/components/common/NarrowBox.svelte';
	import { refusalOf } from '$lib/settings-ui/settings';
	import { pressTask, taskList, type At, type Only, type TaskView } from '$lib/jobs/tasks.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { exactly, sayAgo, sayWhen } from '$lib/shell/when';
	import { counted } from '$lib/entity/entity-counts';
	import { COPY } from './ScheduledTasks.search';
	import { REGISTRY_HOME, labelFor } from './sections';

	interface Props {
		/** The task's id, as `/api/tasks` names it: `scan`, `faces`, `smart-search`, `backup`. */
		task: string;
		/** What the row is called. The task's own title when absent; an owning pane may say "When it
		 *  runs", since the pane's heading already names the thing. */
		label?: string;
		/** The sentence under it. The task's own line when absent; an empty string draws none. */
		help?: string;
		/** Whether the row carries the task's press. Only Tasks draws it; an owning pane says false. */
		press?: boolean;
		/** What stands before the press: an import stage's Edit. */
		beside?: Snippet;
		/** Facts the owning pane adds to the foot line, after the task's own. */
		more?: Snippet;
		/** Its When's key, written out where a search entry names it (the search gate reads it). */
		id?: string;
	}

	let { task, label, help, press = true, beside, more, id }: Props = $props();

	const row = $derived<TaskView | undefined>(taskList.row(task));
	const pressing = $derived<At | undefined>(taskList.pressing[task]);

	/* The moment every fact on the row is read against, kept half a minute fresh. */
	let now = $state(Math.floor(Date.now() / 1000));

	onMount(() => {
		void taskList.ensure();
		const tick = setInterval(() => (now = Math.floor(Date.now() / 1000)), 30_000);
		return () => clearInterval(tick);
	});

	async function choose(value: string): Promise<void> {
		try {
			await taskList.setWhen(task, value);
		} catch (error) {
			toasts.show(refusalOf(error), { tone: 'error' });
		}
	}

	/** How the last run ended, in words, with the moment. Null when none is on record. */
	function lastSaid(one: TaskView): string | null {
		if (!one.last) return null;
		const when = sayAgo(one.last.ended_at, now);
		if (one.last.outcome === 'failed') return COPY.when.lastFailed(when);
		if (one.last.outcome === 'canceled') return COPY.when.lastCanceled(when);
		return COPY.when.lastRan(when);
	}

	/* The answers offered, each in the words `whenLabel` gives it, and the current one where it is
	   none of them: a stage whose tasks disagree reads Mixed, which is drawn and cannot be chosen. */
	const said = (one: { value: string; label: string }): SelectOption => ({
		value: one.value,
		label: taskList.whenLabel(one.value, one.label)
	});
	const options = $derived.by((): SelectOption[] => {
		if (!row) return [];
		const answers = row.whens.map(said);
		if (row.whens.some((one) => one.value === row.when)) return answers;
		return [{ value: row.when, label: COPY.when.mixed, disabled: true }, ...answers];
	});

	/* Where the switch that turned this task's feature off is drawn: the pane its section maps to. */
	const offAt = $derived(
		row?.off ? { section: REGISTRY_HOME[row.off.section] ?? 'tasks', key: row.off.key } : null
	);

	/* Where the When is folded into the press, the answer it holds is still read at rest: the
	   first fact on the foot line, in the words the menu ticks. */
	const folded = $derived(press && !offAt);
	const chosen = $derived(options.find((one) => one.value === row?.when)?.label ?? null);

	/* What is ticked in the press's menu: some of the task's parts, some library folders. */
	let ticked = $state<string[]>([]);
	let tickedFolders = $state<string[]>([]);

	const folders = $derived(row?.locations ? (taskList.view?.folders ?? []) : []);
	const choices = $derived((row?.parts.length ?? 0) > 0 || folders.length > 0);
	const anyTicked = $derived(ticked.length > 0 || tickedFolders.length > 0);

	/* What is typed in the menu's box, and the parts and folders it leaves. */
	let narrowed = $state('');
	const needle = $derived(narrowed.trim().toLocaleLowerCase());
	const matching = <T extends { label: string }>(list: readonly T[]): T[] =>
		needle ? list.filter((one) => one.label.toLocaleLowerCase().includes(needle)) : [...list];
	const shownParts = $derived(matching(row?.parts ?? []));
	const shownFolders = $derived(matching(folders));

	/* Keys the menu walks its rows with pass through the box; every other key is typing, which
	   the menu's own type-to-find would otherwise take as a jump to a row. */
	const WALKS = new Set(['ArrowDown', 'ArrowUp', 'Escape', 'Tab']);
	function typing(event: KeyboardEvent): void {
		if (!WALKS.has(event.key)) event.stopPropagation();
	}

	function flip(list: string[], key: string): string[] {
		return list.includes(key) ? list.filter((one) => one !== key) : [...list, key];
	}

	/* The ticked part of the task, as the run route takes it: a list only where something in it
	   is ticked, so an untouched group is all of it. */
	function only(): Only {
		return {
			...(ticked.length ? { parts: ticked } : {}),
			...(tickedFolders.length ? { locations: tickedFolders } : {})
		};
	}

	/** How the last dry run ended, and its report for the tooltip. Null when there has been none. */
	function drySaid(one: TaskView): string | null {
		if (!one.dry_run) return null;
		const when = sayAgo(one.dry_run.ended_at, now);
		return one.dry_run.outcome === 'failed'
			? COPY.when.lastDryFailed(when)
			: COPY.when.lastDry(when);
	}

	/* A dry run's report, closed the way every one of them is. */
	const NOTHING_CHANGED = 'Nothing was changed.';

	/** The names a report did not list, counted: "and 1,575 more". */
	function andMore(count: number): string {
		return `and ${counted(count)} more`;
	}

	/** What waits, and whether it waits for quiet hours. Null when nothing does. */
	function waitingSaid(one: TaskView): string | null {
		if (one.waiting <= 0) return null;
		const said = one;
		const unit = one.waiting === 1 ? said.unit : said.units;
		return one.held >= one.waiting
			? COPY.when.held(one.waiting, unit)
			: COPY.when.waiting(one.waiting, unit);
	}
</script>

<!-- The press's trailing menu. See "One line, one control" and "Part of a task, and its dry run"
     above. The answers are ticks that stay open under the pointer, so the tick is seen to move. -->
{#snippet menu()}
	{#if row}
		<ContextMenuGroup label={COPY.when.whenHeading} heading>
			{#each options as one (one.value)}
				<ContextMenuItem
					label={one.label}
					checked={one.value === row.when}
					disabled={one.disabled}
					onselect={() => void choose(one.value)}
				/>
			{/each}
		</ContextMenuGroup>
		<ContextMenuGroup>
			<ContextMenuItem
				label={COPY.when.atQuiet}
				icon="schedule"
				onselect={() => void pressTask(task, 'quiet')}
			/>
			{#if choices}
				<ContextMenuItem
					label={COPY.when.runTicked}
					icon="play_arrow"
					disabled={!anyTicked}
					onselect={() => void pressTask(task, 'now', only())}
				/>
			{/if}
			{#if row.dry}
				<ContextMenuItem
					label={anyTicked ? COPY.when.dryRunTicked : COPY.when.dryRun}
					icon="checklist"
					onselect={() => void pressTask(task, 'now', { ...only(), dry: true })}
				/>
			{/if}
		</ContextMenuGroup>
		{#if choices}
			<div class="narrowing">
				<NarrowBox
					bind:value={narrowed}
					label={COPY.when.narrow(row.title)}
					inset="row"
					onkeydown={typing}
				/>
			</div>
			{#if shownParts.length === 0 && shownFolders.length === 0}
				<p class="unmatched">{COPY.when.nothingMatches}</p>
			{/if}
		{/if}
		{#if shownParts.length > 0}
			<ContextMenuGroup label={COPY.when.parts} heading>
				{#each shownParts as part (part.key)}
					<ContextMenuItem
						label={part.label}
						checked={ticked.includes(part.key)}
						onselect={() => (ticked = flip(ticked, part.key))}
					/>
				{/each}
			</ContextMenuGroup>
		{/if}
		{#if shownFolders.length > 0}
			<ContextMenuGroup label={COPY.when.folders} heading>
				{#each shownFolders as folder (folder.key)}
					<ContextMenuItem
						label={folder.label}
						checked={tickedFolders.includes(folder.key)}
						onselect={() => (tickedFolders = flip(tickedFolders, folder.key))}
					/>
				{/each}
			</ContextMenuGroup>
		{/if}
	{/if}
{/snippet}

{#snippet facts()}
	{#if row}
		<p class="facts">
			{#if folded && chosen}
				<span data-fact="when">{chosen}</span>
			{/if}
			{#if offAt}
				<span class="off">
					{COPY.when.off(row.title)}
					{COPY.when.turnOn}
					<SettingLink section={offAt.section} setting={offAt.key}
						>{labelFor(offAt.section)}</SettingLink
					>.
				</span>
			{/if}
			{#if row.running}
				<!-- The In progress chip, the one every screen draws for work under way (Activity's
				     rows, Downloads): a run going now is a state, never a phrase of its own. -->
				<span data-fact="running"><Badge state="running" /></span>
			{/if}
			{#if row.last}
				{@const said = lastSaid(row)}
				<Tooltip
					label={row.last.said
						? COPY.when.said(exactly(row.last.ended_at), row.last.said)
						: exactly(row.last.ended_at)}
				>
					<span class:failed={row.last.outcome === 'failed'}>{said}</span>
				</Tooltip>
			{/if}
			{#if row.next_run !== null}
				<Tooltip label={exactly(row.next_run)}>
					<span>{COPY.when.next(sayWhen(row.next_run, now))}</span>
				</Tooltip>
			{/if}
			{#if waitingSaid(row)}
				<span>{waitingSaid(row)}</span>
			{/if}
			{#if row.dry_running}
				<!-- Said until the report lands, since a plan of the whole library can take a while
				     and the row would otherwise say nothing about the press at all. A run going now,
				     so the In progress chip, its word saying which run. -->
				<span data-fact="dry-running"><Badge state="running" label={COPY.when.dryRunning} /></span>
			{/if}
			{#if row.dry_run}
				<!-- The phrase says when; the report opens beside it, on hover or on a press. -->
				{@const dry = row.dry_run}
				<Popover
					hover
					label={COPY.when.dryReport}
					side="bottom"
					align="start"
					width="min(28rem, 90vw)"
				>
					{#snippet trigger({ props })}
						<Button
							{...props}
							tone="link"
							size="small"
							class={dry.outcome === 'failed' ? 'dry failed' : 'dry'}>{drySaid(row)}</Button
						>
					{/snippet}
					{#if dry.report && dry.outcome !== 'failed'}
						<!-- The report as rows: what it would do, how many of each, the first of them by
						     name, what cannot run and why, and that nothing was changed. -->
						{@const report = dry.report}
						<div class="report">
							{#if report.headline}<p class="headline">{report.headline}</p>{/if}
							{#if report.parts.length > 0}
								<dl class="counts">
									{#each report.parts as part (part.label)}
										<div>
											<dt>{part.label}</dt>
											<dd>{counted(part.count)}</dd>
										</div>
									{/each}
								</dl>
							{/if}
							{#if report.names.length > 0}
								<div class="listed">
									<p class="named">{report.named}</p>
									<ul class="names">
										{#each report.names as name, at (at)}
											<li><Tooltip label={name}><span class="cut">{name}</span></Tooltip></li>
										{/each}
										{#if report.more > 0}<li class="more">{andMore(report.more)}</li>{/if}
									</ul>
								</div>
							{/if}
							{#each report.cannot as why (why)}<p class="cannot">{why}</p>{/each}
							<p class="unchanged">{NOTHING_CHANGED}</p>
						</div>
					{:else}
						<p class="report">{dry.said ?? exactly(dry.ended_at)}</p>
					{/if}
				</Popover>
			{/if}
			{@render more?.()}
		</p>
	{:else if taskList.failed}
		<p class="facts">{COPY.when.cannotLoad}</p>
	{/if}
{/snippet}

<LabelledRow
	id={id ?? `tasks.${task}.when`}
	label={label ?? row?.title ?? ''}
	help={help === undefined ? row?.explain : help || undefined}
	wide
	besideField
	foot={facts}
>
	<div class="when" data-task={task}>
		{#if row}
			{#if beside}
				<div class="press">{@render beside()}</div>
			{/if}
			{#if folded}
				<!-- The press and the choice in one control. See "One line, one control" above. -->
				<div class="press" data-setting-door>
					<SplitButton
						tone="secondary"
						icon="play_arrow"
						leadDisabled={pressing !== undefined}
						aria-busy={pressing === 'now'}
						onclick={() => void pressTask(task, 'now')}
						trailingLabel={COPY.when.more(row.title)}
						{menu}>{COPY.when.run}</SplitButton
					>
				</div>
			{:else}
				<div class="choice">
					<Select
						value={row.when}
						{options}
						label={COPY.when.choose(row.title)}
						sizeTo={taskList.tasks.flatMap((one) =>
							one.whens.map((when) => taskList.whenLabel(when.value, when.label))
						)}
						onValueChange={(value) => void choose(value)}
					/>
				</div>
			{/if}
		{/if}
	</div>
</LabelledRow>

<style>
	/* The choice and its press on one line, ending at the column's far edge like every control
	   on the pane. */
	.when {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		justify-content: var(--row-pack, flex-end);
		gap: var(--space-2);
		inline-size: 100%;
	}

	/* As wide as its widest answer, like every select; packed to the column's end beside its press. */
	.choice {
		flex: 0 1 auto;
		min-inline-size: 0;
	}

	.press {
		flex: none;
	}

	/* The box over the parts, at the rows' inset with the gap a menu group keeps. */
	.narrowing {
		padding: var(--space-1);
	}

	.unmatched {
		margin: 0;
		padding: var(--space-2) var(--space-3);
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	/* The facts, read left to right under the help: each one a phrase, the line wrapping between
	   them and never inside one. */
	.facts {
		display: flex;
		flex-wrap: wrap;
		column-gap: var(--space-4);
		row-gap: var(--space-1);
		margin: 0;
	}

	/* The sentence for a feature that is off: the whole foot line, so its link is not read as a fact. */
	.off {
		flex-basis: 100%;
	}

	/* A fact that is a sentence (why a feature is off, what a stage still has to do) carries the
	   `sentence` class and wraps like one; the rest are short phrases. */
	.facts > :global(*:not(.sentence)) {
		white-space: nowrap;
	}

	/* The last dry run, said as a fact on the foot in the foot's own ink; its report opens
	   beside it. */
	.facts :global(.dry) {
		font: inherit;
		color: inherit;
		text-decoration: underline dotted;
		text-underline-offset: 0.15em;
	}

	.facts :global(.dry.failed) {
		color: var(--sift-bad-text);
	}

	/* What the run cannot do and why, in the failure's ink: the one part of a report to act on. */
	.cannot {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-bad-text);
	}

	/* The report, at the panel's reading size; a long file name breaks rather than widening it. */
	.report {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
		overflow-wrap: anywhere;
	}

	/* As rows: the sentence first, then each count beside its label, the names as a list, and
	   the close. */
	div.report {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
	}

	div.report p {
		margin: 0;
	}

	.headline {
		color: var(--sift-ink);
	}

	/* Each count on its own row, the number at the row's end in figures that line up. */
	.counts {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		column-gap: var(--space-4);
		row-gap: var(--space-1);
		margin: 0;
	}

	.counts > div {
		display: contents;
	}

	.counts dd {
		margin: 0;
		text-align: end;
		font-variant-numeric: tabular-nums;
		color: var(--sift-ink);
	}

	/* The names under their heading, closer to it than to the blocks around them. */
	.listed {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
	}

	.named {
		font: var(--text-label);
		color: var(--sift-ink-3);
	}

	.names {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	/* One name to a line, cut at the end rather than broken mid-extension ("j" over "pg"); the
	   whole name is on the app's own tooltip. */
	.cut {
		display: block;
		overflow: hidden;
		white-space: nowrap;
		text-overflow: ellipsis;
	}

	.names .more,
	.unchanged {
		color: var(--sift-ink-3);
	}

	/* A run that failed is the one fact on the row somebody has to do something about. */
	.failed {
		color: var(--sift-bad-text);
	}
</style>
