<script lang="ts">
	/* The passes and housekeeping above the queue: one row each, with Run now, pause and cancel. */
	import {
		Badge,
		Button,
		DataRow,
		DataRows,
		ProgressBar,
		SettingLink
	} from '$lib/components/common';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import SectionHeading from '$lib/components/common/SectionHeading.svelte';
	import { phoneWidth } from '$lib/components/common/phone-width.svelte';
	import TasksLeftOut from '$lib/settings-ui/TasksLeftOut.svelte';
	import {
		ACTIVITY_ACTIONS,
		ACTIVITY_CARD,
		ACTIVITY_COLUMNS,
		nowState,
		type ChoreView,
		type Pass,
		type PassPart
	} from './family';
	import { COPY } from './JobsScreen.search';
	import type { Queue, Which } from './queue.svelte';
	import { pressTasks } from './tasks.svelte';
	import { leftOut } from './left-out.svelte';

	interface Props {
		queue: Queue;
		queues: Pass[];
		chored: ChoreView[];
		compact: boolean;
		onshowfailed: () => void;
		onpause: (which: Which, paused: boolean) => Promise<void>;
		oncancelpass: (which: Which, title: string) => void;
	}

	let {
		queue,
		queues,
		chored,
		compact,
		onshowfailed: showFailed,
		onpause: doPause,
		oncancelpass: askCancelPass
	}: Props = $props();

	/* The one list above the queue: a group heading, the group's rows, and under a pass of
	   several kinds one row per kind. */
	type Line =
		| { kind: 'group'; id: string; title: string }
		| { kind: 'pass'; id: string; pass: Pass }
		| { kind: 'part'; id: string; pass: Pass; part: PassPart }
		| { kind: 'chore'; id: string; chore: ChoreView };

	/* A pass of several kinds folds its rows under an arrow and starts folded; a phone's card
	   list has no arrow track, so there the kinds are always shown. */
	let openPasses = $state<string[]>([]);
	function togglePass(id: string): void {
		openPasses = openPasses.includes(id)
			? openPasses.filter((one) => one !== id)
			: [...openPasses, id];
	}

	const lines = $derived<Line[]>([
		{ kind: 'group', id: 'group:passes', title: COPY.groups.passes },
		...queues.flatMap((pass): Line[] => [
			{ kind: 'pass', id: `pass:${pass.id}`, pass },
			...(phoneWidth.yes || openPasses.includes(pass.id) ? pass.parts : []).map((part): Line => ({
				kind: 'part',
				id: `part:${pass.id}:${part.type}`,
				pass,
				part
			}))
		]),
		{ kind: 'group', id: 'group:housekeeping', title: COPY.groups.housekeeping },
		...chored.map((chore): Line => ({ kind: 'chore', id: `chore:${chore.id}`, chore }))
	]);

	/* Run now here is the task's own Run now (`pressTasks`), one press with one sentence; a pass
	   presses what the server says it is (`runs`), less a sub-task switched off. */
	function pressesOf(one: Pass | ChoreView): { task: string; parts?: string[] }[] {
		if ('runs' in one && one.runs.length > 0)
			return one.runs.map((run) => ({ task: run.task, parts: run.parts ?? undefined }));
		return one.task ? [{ task: one.task }] : [];
	}

	async function runNow(one: Pass | ChoreView): Promise<void> {
		await pressTasks(pressesOf(one), 'now');
		void queue.refresh();
	}
</script>

<!-- A pass's or a sub-task's pause (or resume) and cancel: glyph presses, named on the hover. -->
{#snippet holdAndCancel(which: Which, title: string, paused: boolean)}
	<Tooltip label={paused ? COPY.resume : COPY.pause}>
		<Button
			tone="ghost"
			onclick={() => void doPause(which, !paused)}
			aria-label="{paused ? COPY.resume : COPY.pause}: {title}"
		>
			<Icon name={paused ? 'play_arrow' : 'pause'} size={16} />
		</Button>
	</Tooltip>
	<Tooltip label={COPY.cancel}>
		<Button
			tone="ghost"
			onclick={() => askCancelPass(which, title)}
			aria-label="{COPY.cancel}: {title}"
		>
			<Icon name="close" size={16} />
		</Button>
	</Tooltip>
{/snippet}

<!-- One list for passes and housekeeping on the tab's columns (`ACTIVITY_COLUMNS`), so every
     status stands at one x; every row is listed, work or not, so none jumps as queues fill. -->
<!-- On a phone the same list as cards (`ACTIVITY_CARD`): a list reads its columns once, when
     it is made, so it is made again when the window crosses the phone's width. -->
{#key phoneWidth.yes}
	<DataRows
		items={lines}
		key={(line: Line) => line.id}
		label={COPY.summaryLabel}
		columns={phoneWidth.yes ? ACTIVITY_CARD : ACTIVITY_COLUMNS}
		actions={phoneWidth.yes ? undefined : ACTIVITY_ACTIONS}
		folds={!phoneWidth.yes}
		edges
	>
		{#snippet row(line: Line)}
			{#if line.kind === 'group'}
				<DataRow><SectionHeading>{line.title}</SectionHeading></DataRow>
			{:else if line.kind === 'part'}
				<!-- One kind of a pass that is several: under the pass, its own bar and count. -->
				<DataRow
					indent={1}
					cells={phoneWidth.yes
						? { card: partCard }
						: { name: partName, bar: partBar, count: partCount }}
					actions={phoneWidth.yes ? undefined : partActions}
					{compact}
				/>
				{#snippet partCard()}
					<span class="card">
						<span class="card-head">{@render partName()}{@render partActions()}</span>
						<span class="card-facts">{@render partCount()}</span>
						{@render partBar()}
					</span>
				{/snippet}
				<!-- A sub-task's own pause and cancel, as its pass has them. -->
				{#snippet partActions()}
					<span class="row-actions">
						{@render holdAndCancel({ type: line.part.type }, line.part.label, line.part.paused)}
					</span>
				{/snippet}
				{#snippet partName()}<span class="pass-title">{line.part.label}</span>{/snippet}
				{#snippet partBar()}<ProgressBar
						value={line.part.progress}
						label={`${line.part.label} \u2014 ${line.part.count}`}
					/>{/snippet}
				{#snippet partCount()}<span class="pass-count">{line.part.count}</span>{/snippet}
			{:else}
				{@const one = line.kind === 'pass' ? line.pass : line.chore}
				<DataRow
					cells={phoneWidth.yes
						? { card: lineCard }
						: line.kind === 'pass'
							? {
									name: title,
									status: nowWord,
									left: timeLeft,
									bar: passBar,
									count: passCount
								}
							: { name: title, status: nowWord, left: timeLeft, bar: lastRun }}
					spans={line.kind === 'chore' && !phoneWidth.yes ? { bar: 'count' } : undefined}
					actions={phoneWidth.yes ? undefined : lineActions}
					expanded={line.kind === 'pass' && line.pass.parts.length > 0 && !phoneWidth.yes
						? openPasses.includes(line.pass.id)
						: undefined}
					ontoggle={line.kind === 'pass' && line.pass.parts.length > 0 && !phoneWidth.yes
						? () => togglePass(line.pass.id)
						: undefined}
					toggleLabel="the kinds of {one.title}"
					{compact}
				/>
				<!-- Run now, the task's own press; on a pass its pause and cancel after it. -->
				{#snippet lineActions()}
					<span class="row-actions">
						{#if pressesOf(one).length > 0}
							<Button tone="link" size="small" onclick={() => void runNow(one)}
								>{COPY.runNow}</Button
							>
						{/if}
						{#if line.kind === 'pass'}
							{@render holdAndCancel(
								{ family: line.pass.id as Which['family'] },
								one.title,
								line.pass.paused
							)}
						{/if}
					</span>
				{/snippet}
				<!--
					The row as a card: the same cells, one under another, in the order they read
					across.
				-->
				{#snippet lineCard()}
					<span class="card">
						<span class="card-head">{@render title()}{@render lineActions()}</span>
						<span class="card-facts"
							>{@render nowWord()}{@render timeLeft()}{#if line.kind === 'pass'}{@render passCount()}{:else}{@render lastRun()}{/if}</span
						>
						{#if line.kind === 'pass'}{@render passBar()}{/if}
					</span>
				{/snippet}
				{#snippet title()}<span class="pass-title">{one.title}</span>{/snippet}
				<!-- The state as a pill, the shape Done and Failed wear on the rows under it: up to
			     date is done's, waiting on something is blocked's, work under way is the In
			     progress chip the job rows under it wear (blue, its mark turning), anything else
			     is quiet. -->
				<!-- On the app's tooltip as well, as a column can cut the pill's words short; a failed
			     pass's hover says why, and pressed it opens the failed ones below. -->
				{#snippet nowWord()}
					{#if line.kind === 'pass' && line.pass.why}
						<Tooltip label={COPY.failedWhy(line.pass.why)} stretch>
							<Button tone="quiet" onclick={showFailed}
								><Badge state={nowState(one.tone)} label={one.now} /></Button
							>
						</Tooltip>
					{:else if line.kind === 'pass' && line.pass.leftOut > 0}
						<TasksLeftOut
							products={leftOut.named(line.pass.leftOutOf)}
							title={one.title}
							tone="quiet"><Badge state={nowState(one.tone)} label={one.now} /></TasksLeftOut
						>
					{:else}
						<Tooltip label={one.now} stretch
							><Badge state={nowState(one.tone)} label={one.now} /></Tooltip
						>
					{/if}
				{/snippet}
				<!-- An estimate, or the reason there is not going to be one: "it cannot start" is an
			     answer to "when will it be done". A switched-off pass links to where it is
			     switched on. -->
				{#snippet timeLeft()}
					{#if line.kind === 'pass' && line.pass.settingLink}
						<span class="pass-eta"><SettingLink section="importing">{one.when}</SettingLink></span>
					{:else}
						<span class="pass-eta">{one.when}</span>
					{/if}
				{/snippet}
				<!-- DONE OVER WHAT WANTS DOING, both counted from the library, so the bar is defined
			     while nothing runs. A pass of several kinds draws its bars on the rows under it. -->
				{#snippet passBar()}
					{#if line.kind === 'pass' && (line.pass.parts.length === 0 || line.pass.moving)}
						<ProgressBar
							value={line.pass.progress}
							label={`${one.title} \u2014 ${line.pass.done}`}
						/>
					{/if}
				{/snippet}
				{#snippet passCount()}
					{#if line.kind === 'pass' && (line.pass.parts.length === 0 || line.pass.moving)}
						<span class="pass-count">{line.pass.done}</span>
					{/if}
				{/snippet}
				<!-- No bar for a chore: nothing counts the files that want a duplicate sweep, so the
			     bar and count columns say how the last run went instead. -->
				<!-- A run that failed says why on the hover, and the phrase opens the failed ones in
			     the list below, where its row carries the whole of what went wrong. -->
				{#snippet lastRun()}
					{#if line.kind === 'chore' && line.chore.why}
						<span class="pass-last">
							<Tooltip label={COPY.failedWhy(line.chore.why)}>
								<Button tone="link" size="small" class="failed-run" onclick={showFailed}
									>{line.chore.last}</Button
								>
							</Tooltip>
						</span>
					{:else if line.kind === 'chore'}<span class="pass-last">{line.chore.last}</span>{/if}
				{/snippet}
			{/if}
		{/snippet}
	</DataRows>
{/key}

<style>
	/* A row as a card on a phone: the cells one under another; the facts line wraps, not cuts. */
	.card {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		min-inline-size: 0;
		padding-block: var(--space-1);
	}

	.card-facts {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		column-gap: var(--space-3);
		row-gap: var(--space-1);
	}

	/* The name at the start and the row's one action at the card's end, on one line. */
	.card-head {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: var(--space-3);
	}

	/* Words on a card start where the name starts: the Done column's end edge is not there. */
	.card .pass-last {
		text-align: start;
	}

	.pass-title {
		color: var(--sift-ink-2);
		font: var(--text-body-sm);
	}

	/* How the last run went spans Progress and Done, ending on Done's edge as the counts do. */
	.pass-last {
		display: block;
		text-align: end;
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	/* A failed run's phrase in the failure's ink, dotted under: there is more behind it. */
	.pass-last :global(.failed-run) {
		font: inherit;
		color: var(--sift-bad-text);
		text-decoration: underline dotted;
		text-underline-offset: 0.15em;
	}

	/* Words, so left: right-aligned sentences of different lengths have a ragged left edge. */
	.pass-eta {
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	/* The count column is declared `end`; this is only its colour and face. */
	.pass-count {
		color: var(--sift-ink-3);
		font: var(--text-data);
	}

	/* A row's presses together at its end: Run now, then the glyphs. */
	.row-actions {
		display: inline-flex;
		align-items: center;
		gap: var(--space-1);
	}
</style>
