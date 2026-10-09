<script lang="ts" module>
	/* WHERE THIS FEED WAS LEFT, kept outside the component so it outlives one. */
	type Place = { offset: number; at: number };

	const places = new Map<string, Place>();
</script>

<script lang="ts">
	/* Activity > History: everything this installation has done, newest first. */
	import { onMount, tick, untrack } from 'svelte';
	import { beforeNavigate } from '$app/navigation';
	import {
		Button,
		ConfirmDialog,
		DataRow,
		DataRows,
		Empty,
		HistorySentence,
		LabelledRow,
		Note,
		Select,
		Skeleton,
		Tooltip
	} from '$lib/components/common';
	import { undoDecision } from '$lib/api/history';
	import { api } from '$lib/api/client';
	import { copyText } from '$lib/shell/clipboard';
	import type { components } from '$lib/api/schema';
	import { libraryChanges, whenChanged } from '$lib/library/changes.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import {
		ANY,
		PAGE,
		kindChoices,
		readLedger,
		undoAll,
		verbChoices,
		type LedgerEvent
	} from '$lib/library/ledger';
	import { exactly, onRecord } from '$lib/shell/when';
	import type { Column } from '$lib/components/common/DataRows.svelte';
	import { phoneWidth } from '$lib/components/common/phone-width.svelte';
	import { SvelteMap } from 'svelte/reactivity';
	import { scrollParent } from '$lib/grid/cards.svelte';
	import SettingGroup from './SettingGroup.svelte';
	import Thumb from '$lib/components/organize/Thumb.svelte';
	import Pager from '$lib/components/common/Pager.svelte';
	import { COPY } from './Ledger.search';

	/* The line, what can be done about it, and its moment, each in a column of its own. */
	const HISTORY_COLUMNS: readonly Column[] = [
		{ id: 'what', width: 'minmax(0, 1fr)' },
		{ id: 'act', width: '9rem', align: 'end' },
		{ id: 'at', width: '11rem', align: 'end' }
	];

	/* The same row at a phone's width: the line alone, with its presses and its moment on a line
	   of their own under it. */
	const HISTORY_CARD: readonly Column[] = [{ id: 'what', width: 'minmax(0, 1fr)' }];

	let events = $state<LedgerEvent[]>([]);
	let total = $state(0);
	let failed = $state<string | null>(null);
	let reading = $state(false);
	/* Nothing has been read yet, which is a different state from "there is nothing". */
	let asked = $state(false);

	interface Props {
		/** The act this list opens narrowed to, where it was reached for one: "Saved to a device"
		 * opens it on `saved`, "How long tasks take" on `ran`. */
		verb?: string;
		/** Whether it opens on the decisions alone: what was decided on Organize and what Sift
		 * filed by itself, each with its Undo, where it was reached from Organize's Decisions
		 * (or the old address of the record that list replaced). */
		decisions?: boolean;
	}

	let { verb: startingVerb = ANY, decisions: startingDecisions = false }: Props = $props();

	let kind = $state(ANY);
	// The first value only, deliberately. See `verb` above.
	let verb = $state(untrack(() => startingVerb));
	/* Everything, or the decisions alone. The first value only, as `verb`. */
	const EVERYTHING = 'everything';
	const DECISIONS = 'decisions';
	let showing = $state(untrack(() => (startingDecisions ? DECISIONS : EVERYTHING)));
	const SHOW_CHOICES = [
		{ value: EVERYTHING, label: COPY.show.everything },
		{ value: DECISIONS, label: COPY.show.decisions }
	];

	/* Built from the vocabulary rather than listed here. */
	const KIND_CHOICES = kindChoices();
	const VERB_CHOICES = verbChoices();

	const narrowing = $derived({
		kind: kind === ANY ? undefined : kind,
		verb: verb === ANY ? undefined : verb,
		decisions: showing === DECISIONS ? true : undefined
	});

	/* Where the page on screen starts, counting from zero. */
	let offset = $state(0);

	/** Read the page starting at `at` for whatever is being asked for now, replacing the one on
	   screen. */
	async function readPage(at: number, problem: string = COPY.cannotLoad): Promise<void> {
		reading = true;
		failed = null;
		try {
			let page = await readLedger({ ...narrowing, limit: PAGE, offset: at });
			if (at > 0 && at >= page.total) {
				at = Math.max(0, Math.floor((page.total - 1) / PAGE) * PAGE);
				page = await readLedger({ ...narrowing, limit: PAGE, offset: at });
			}
			events = page.items;
			total = page.total;
			offset = at;
			asked = true;
		} catch {
			failed = problem;
		} finally {
			reading = false;
		}
	}

	/** The first page, for a narrowing just chosen. */
	async function start(): Promise<void> {
		await readPage(0);
	}

	/** Another page, from the pager under the list. */
	async function turnTo(at: number): Promise<void> {
		await readPage(Math.max(0, at), COPY.cannotLoadMore);
	}

	/* SOMETHING HAPPENED WHILE THIS WAS OPEN: the page on screen read again. */
	async function refresh(): Promise<void> {
		if (reading) return;
		/* A refusal is taken and dropped here, deliberately, and it is caught on the call rather
		   than around it so that nothing else in this function can be swallowed with it. */
		const page = await readLedger({ ...narrowing, limit: PAGE, offset }).catch(() => null);
		if (page === null) return;
		events = page.items;
		total = page.total;
	}

	/* TAKING ONE BACK, from the list that holds every act rather than from the screen it was
	 * taken on. */
	let undoing = $state<string | null>(null);

	/** Whether this line can still be taken back. See the note above. */
	function canUndo(event: LedgerEvent): boolean {
		return (
			(event.folded ?? 1) <= 1 &&
			!!event.receipt &&
			!event.receipt.reversed_at &&
			!event.receipt.final
		);
	}

	/* UNDO ALL, on a line that stands for a press of many decisions: a task's four thousand
	 * filings. */
	function canUndoAll(event: LedgerEvent): boolean {
		return (
			(event.folded ?? 1) > 1 &&
			(event.standing ?? 0) > 0 &&
			!!event.receipt &&
			!event.receipt.final
		);
	}

	/** Whether a line reads as undone: its decision taken back, or every decision of its press. */
	function undone(event: LedgerEvent): boolean {
		if ((event.folded ?? 1) > 1) return !!event.receipt && (event.standing ?? 0) === 0;
		return !!event.receipt?.reversed_at;
	}

	/** What else a decision wrote, said under its line. */
	function moreOf(event: LedgerEvent): string {
		return event.more ?? '';
	}

	/* The folded line Undo all was pressed on, while the question is up. */
	let asking = $state<LedgerEvent | null>(null);
	let askingOpen = $state(false);

	function askToUndoEvery(event: LedgerEvent): void {
		if (undoing || !canUndoAll(event)) return;
		asking = event;
		askingOpen = true;
	}

	async function undoEvery(event: LedgerEvent): Promise<void> {
		if (undoing || !canUndoAll(event)) return;
		undoing = event.id;
		try {
			const done = await undoAll(event.id, narrowing);
			toasts.show(COPY.undoneAll(done.undone, done.of));
			await reload();
		} catch {
			toasts.show(COPY.notUndone, { tone: 'error' });
		} finally {
			undoing = null;
		}
	}

	async function undo(event: LedgerEvent): Promise<void> {
		if (undoing || !canUndo(event)) return;
		undoing = event.id;
		try {
			const answer = await undoDecision(event.id);
			/* The line redraws as undone either way; a batch that went back only in part also
			   says how many, which the line itself cannot. */
			if (answer?.said) toasts.show(answer.said);
			await reload();
		} catch {
			toasts.show(COPY.notUndone, { tone: 'error' });
		} finally {
			undoing = null;
		}
	}

	/* A RUN'S OWN REPORT, from its line. */
	type RunReport = components['schemas']['RunReportView'];

	/** The run whose report a line opens, where the server says it has one. */
	function runOf(event: LedgerEvent): string | null {
		return event.report ?? null;
	}

	const reports = new SvelteMap<string, string>();
	let reportFailed = $state<string | null>(null);
	let reportCopied = $state<string | null>(null);

	async function toggleReport(run: string): Promise<void> {
		reportFailed = null;
		if (reports.has(run)) {
			reports.delete(run);
			return;
		}
		try {
			const report = await api.get<RunReport>(
				`/performance/runs/${encodeURIComponent(run)}/report`
			);
			reports.set(run, report.text);
		} catch {
			reportFailed = COPY.cannotReport;
		}
	}

	async function copyReport(run: string): Promise<void> {
		const text = reports.get(run);
		if (text === undefined) return;
		if (await copyText(text)) {
			reportCopied = run;
			setTimeout(() => (reportCopied = null), 2000);
		} else {
			reportFailed = COPY.blocked;
		}
	}

	/* The page on screen again, which is what shows a line an Undo just took back as taken back,
	   without moving anybody off the page they were on. */
	async function reload(): Promise<void> {
		await readPage(offset);
	}

	whenChanged(libraryChanges, () => void refresh());

	/* Which list this is, as a key. Two narrowings are two different lists, so where somebody
	   was in one says nothing about the other. */
	const key = $derived(JSON.stringify(narrowing));

	/** The element this pane can be found by. The box it scrolls inside is worked out FROM it, at
	 * the moment it is wanted, and never kept. */
	let anchor: HTMLElement | null = null;

	/** The scrolling box, once it has answered as one. */
	let box: HTMLElement | null = null;

	/** The box now, or the last real one. Null until the area has become one. */
	function scrollBox(): HTMLElement | null {
		if (anchor?.isConnected) {
			const found = scrollParent(anchor);
			if (found && found !== document.documentElement) box = found;
		}
		return box;
	}

	/** Hold on to the element this pane can be found by. The box is worked out from it. */
	function keepPlace(element: HTMLElement): () => void {
		anchor = element;
		return () => {
			anchor = null;
			box = null;
		};
	}

	/* WHERE THE LIST WAS BEING READ, taken at the moment somebody starts to leave and never from
	 * a scroll event. */
	beforeNavigate(() => {
		const here = scrollBox();
		if (!here || !asked) return;
		places.set(key, { offset, at: here.scrollTop });
	});

	/* The page on its own, so a feed somebody paged through without scrolling (a short window, a
	   long page) is still remembered. */
	$effect(() => {
		if (events.length === 0) return;
		const on = offset;
		const here = scrollBox();
		untrack(() => places.set(key, { offset: on, at: places.get(key)?.at ?? 0 }));
		if (!here) return;

		/* THE SECOND DOOR: the press itself, because the leaving it answers announces nothing. */
		const take = () => {
			if (asked) places.set(key, { offset, at: here.scrollTop });
		};
		here.addEventListener('pointerdown', take, true);
		here.addEventListener('keydown', take, true);
		return () => {
			here.removeEventListener('pointerdown', take, true);
			here.removeEventListener('keydown', take, true);
		};
	});

	/** Open where it was left: the same page, and the same place on it. */
	onMount(() => {
		const place = places.get(key);
		/* Left on the first page at its top is not a place: it is where a list opens anyway. */
		if (!place || (place.offset === 0 && place.at <= 0)) {
			void start();
			return;
		}
		void readPage(place.offset).finally(() => {
			void tick().then(() => {
				/* The box is asked for HERE and not at mount: by now the lines are drawn and the
				   scrolling area has settled into being one. */
				const here = scrollBox();
				if (!here || place.at <= 0) return;
				here.scrollTop = place.at;
			});
		});
	});
</script>

<SettingGroup id="activity.history" heading={COPY.all.name} help={COPY.all.help} />

<!-- The narrowing carries the addresses the two narrowed doors land on ("Saved to a device" here,
     "How long tasks take" on the Action row), so the ring falls on the choice that was made for the
     reader, which is the thing worth seeing. -->
<div class="filters" id="activity.saved" {@attach keepPlace}>
	<LabelledRow label={COPY.type.label} help={COPY.type.help}>
		<Select
			label={COPY.type.label}
			value={kind}
			options={KIND_CHOICES}
			onValueChange={(next) => {
				kind = next;
				void start();
			}}
		/>
	</LabelledRow>
	<LabelledRow id="activity.runs" label={COPY.action.label} help={COPY.action.help}>
		<Select
			label={COPY.action.label}
			value={verb}
			options={VERB_CHOICES}
			onValueChange={(next) => {
				verb = next;
				void start();
			}}
		/>
	</LabelledRow>
	<!-- Everything, or the decisions alone: the record Organize's answers and Sift's own filings
	     make, each line with its Undo, kept here rather than on a screen of its own so there is one
	     place to look for what happened. "Decisions" on Organize lands here, on this choice. -->
	<LabelledRow id="activity.decisions" label={COPY.show.label} help={COPY.show.help}>
		<Select
			label={COPY.show.label}
			value={showing}
			options={SHOW_CHOICES}
			onValueChange={(next) => {
				showing = next;
				void start();
			}}
		/>
	</LabelledRow>
</div>

{#if failed}
	<!-- A Note and not a Problem: `Problem` is this pane's channel for a refused save, and nothing
	     here saves anything. -->
	<Note tone="caution">{failed}</Note>
{/if}
{#if reportFailed}
	<Note tone="caution">{reportFailed}</Note>
{/if}

{#if !asked}
	<!-- Not read yet: the loading state every list has, never the filters over nothing. -->
	{#if !failed}<Skeleton lines={6} />{/if}
{:else if events.length === 0}
	<!-- TWO empty states, because they are two different facts and one sentence cannot be both. -->
	{#if kind === ANY && verb === ANY && showing === EVERYTHING}
		<Empty scope="page" icon="history" title={COPY.emptyTitle}>{COPY.empty}</Empty>
	{:else}
		<Empty scope="page" icon="history" title={COPY.noMatchTitle}>
			{COPY.noMatch}
		</Empty>
	{/if}
{:else}
	<!-- ONE LIST, newest first, each line with its own moment in the one form every record
	     takes ("Today 11:37 PM", the full time on hover: `$lib/shell/when`). No day headings: a
	     heading saying "Today" over lines saying "Today" is the fact twice. -->
	<!-- Keyed on the width, because a list reads its declaration when it is made. -->
	{#key phoneWidth.yes}
		<DataRows
			items={events}
			key={(one) => one.id}
			label={COPY.list}
			columns={phoneWidth.yes ? HISTORY_CARD : HISTORY_COLUMNS}
			edges
		>
			{#snippet row(one)}
				<!-- The cells are snippets beside the row, handed in by name, as every columned list does. -->
				<DataRow compact cells={phoneWidth.yes ? { what } : { what, act, at }} />
				{#snippet what()}
					<!-- The line is the SERVER's, built by the one builder every History screen uses and
					     drawn by the one component that draws a line: the actor first, every named thing
					     where it sits, a folded list opening in place, and what it stands for under
					     "Show each". Nothing here assembles a word of it. -->
					<!-- On Decisions a decision's line leads with the picture its area draws for it (the
					     server's `still`, one read for the page), so the record can be checked and not
					     only read. At the row's own height, so a list of them keeps one rhythm; a line
					     with no picture draws none. -->
					<div class="what" class:taken-back={undone(one)} class:stilled={!!one.still}>
						{#if one.still}
							<span class="still">
								<Thumb
									kind={one.still.kind}
									id={one.still.id}
									href={one.still.href}
									art={one.still.art}
								/>
							</span>
						{/if}
						<span class="said">
							<HistorySentence pieces={one.pieces} detail={one.detail} quiet={undone(one)} />
							{#if !undone(one) && moreOf(one)}
								<span class="since">{moreOf(one)}</span>
							{/if}
							{#if undone(one) && one.receipt?.taken_back}
								<!-- What is true now it has been taken back, in place of the promise the
								     decision made about itself. Its area's own words, from the server. -->
								<span class="since">{one.receipt.taken_back}</span>
							{/if}
						</span>
					</div>
					{#if phoneWidth.yes}
						<!-- A phone's row has no track for these two: the presses start where the line
					     starts and the moment ends at the row's end, as the desktop's columns do. -->
						<span class="under">{@render act()}{@render at()}</span>
					{/if}
					{#if runOf(one) && reports.has(runOf(one) ?? '')}
						<!-- The report as the server wrote it, selectable, under the line it is about. -->
						<pre class="report">{reports.get(runOf(one) ?? '')}</pre>
					{/if}
				{/snippet}
				{#snippet at()}
					<Tooltip label={exactly(one.at)}>
						<span class="at">{onRecord(one.at)}</span>
					</Tooltip>
				{/snippet}
				{#snippet act()}
					<!-- On the RIGHT of the row, where an action goes, and drawn at all only
					     where there is something to press: a line with no receipt was never a
					     decision, one already undone reads as undone, and a queue that cannot
					     reverse anything would be offering a button whose only way of saying no
					     is being pressed. -->
					{#if runOf(one)}
						{@const run = runOf(one) ?? ''}
						{#if reports.has(run)}
							<Button tone="link" size="small" onclick={() => void copyReport(run)}>
								{reportCopied === run ? COPY.copied : COPY.copyReport}
							</Button>
						{/if}
						<Button
							tone="link"
							size="small"
							aria-expanded={reports.has(run)}
							onclick={() => void toggleReport(run)}
						>
							{reports.has(run) ? COPY.hideReport : COPY.showReport}
						</Button>
					{/if}
					{#if canUndo(one)}
						<Button
							tone="link"
							size="small"
							disabled={undoing !== null}
							onclick={() => void undo(one)}
						>
							{undoing === one.id ? COPY.undoing : COPY.undo}
						</Button>
					{:else if canUndoAll(one)}
						<Button
							tone="link"
							size="small"
							disabled={undoing !== null}
							onclick={() => askToUndoEvery(one)}
						>
							{undoing === one.id ? COPY.undoing : COPY.undoAll}
						</Button>
					{/if}
				{/snippet}
			{/snippet}
		</DataRows>
	{/key}

	<!-- A page of lines at a time, by number, and a page typed in: the pager every list has, in
	     its numbered form, because a page here is the same lines on every screen. -->
	{#if total > PAGE}
		<Pager
			{offset}
			shown={events.length}
			{total}
			noun="lines"
			perPage={PAGE}
			onfirst={() => void turnTo(0)}
			onprevious={() => void turnTo(offset - PAGE)}
			onnext={() => void turnTo(offset + PAGE)}
			onlast={() => void turnTo(Math.floor((total - 1) / PAGE) * PAGE)}
			onjump={(position) => void turnTo(Math.floor((position - 1) / PAGE) * PAGE)}
		/>
	{/if}
{/if}

<ConfirmDialog
	bind:open={askingOpen}
	title={COPY.undoAllAsk(asking?.standing ?? 0)}
	consequence={COPY.undoAllSays}
	confirmLabel={COPY.undoAll}
	destructive={false}
	onconfirm={() => {
		const event = asking;
		asking = null;
		if (event) void undoEvery(event);
	}}
/>

<style>
	/* The two narrowings, one row each, in the pane's row shape: the line between them runs the
	   pane's width like every other line between two settings rows. */
	.filters {
		display: block;
	}

	/* `what` and not `line`: `DataRow` puts `class="line"` on the `<li>` it draws, so the name
	   would match two nested elements on every row of this pane: the same collision as a second
	   `.pane` inside the settings shell. */
	/* WRAPPED. */
	.what {
		margin: 0;
		font: var(--text-body-sm);
		white-space: normal;
		overflow-wrap: anywhere;
	}

	/* A decision's picture, then its line, the picture at the compact row's own control height. */
	.what.stilled {
		display: flex;
		align-items: center;
		gap: var(--space-3);
	}

	.still {
		flex: none;
	}

	.still :global(img),
	.still :global(.stand-in) {
		inline-size: var(--control-height);
		block-size: var(--control-height);
	}

	.said {
		display: flex;
		flex-direction: column;
		min-inline-size: 0;
	}

	.since {
		color: var(--sift-ink-3);
	}

	/* An event that was undone keeps its place in the order (a record that quietly loses its
	   reversals reads as though nothing had ever happened), and says so in QUIETER INK rather
	   than by being struck through, as `HistoryRow` does: a line through a sentence is read as
	   "this is not true", and it was true at the time. */
	.taken-back {
		color: var(--sift-ink-3);
	}

	/* A phone's second line: the presses at the start, the moment at the end. */
	.under {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-2);
		margin-block-start: var(--space-1);
	}

	/* A machine fact in the data face, so a column of times does not jitter sideways. */
	.at {
		color: var(--sift-ink-3);
		font: var(--text-data);
		white-space: nowrap;
	}

	/* A run's report: the server's plain text, kept as it was written so it pastes as it reads. */
	.report {
		margin: var(--space-2) 0 0;
		padding-inline-start: var(--space-3);
		border-inline-start: 2px solid var(--sift-line);
		color: var(--sift-ink-2);
		font: var(--text-data);
		white-space: pre-wrap;
		overflow-wrap: anywhere;
	}
</style>
