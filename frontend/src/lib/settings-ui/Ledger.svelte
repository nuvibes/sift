<script lang="ts" module>
	/*
	 * WHERE THIS FEED WAS LEFT, kept outside the component so it outlives one.
	 *
	 * Turning to page five, opening a name from a line and coming back must not give the first
	 * page again, at the top. Opening a name RUNS a route, so this pane is torn down and built
	 * again, and everything it had read goes with it. The order of the lines is fixed and the
	 * narrowing is drawn from a control, so the page and the position on it are the whole of what
	 * a person would lose.
	 *
	 * A module and not storage, deliberately. This is where somebody is standing in a list right
	 * now, not an arrangement they chose: it is worth nothing after the tab is closed, it needs no
	 * serialising and it cannot be refused by a browser with storage switched off. The one thing
	 * storage would add (surviving a full page load) is the case where the list has to be read
	 * from the server again anyway.
	 *
	 * Keyed by the narrowing, because two narrowings are two different lists: page five of
	 * "everything" says nothing about where somebody was in "only shares".
	 */
	type Place = { offset: number; at: number };

	const places = new Map<string, Place>();
</script>

<script lang="ts">
	/*
	 * Activity > History: everything this installation has done, newest first.
	 *
	 * ## Why it is a tab of Activity, beside Now and Log
	 *
	 * Because those two are the other answers to "what has this thing been doing". Now says what it
	 * is doing, the log says what it wrote down while doing it, and this says what happened, and
	 * all three are facts about the installation rather than about a library, which is what makes
	 * them an admin's and not a guest's. Other lists that answer "what happened" are narrowings of
	 * this one rather than lists beside it: "Saved to a device" (the `saved` act), the runs of
	 * the long passes, whose `ran` lines each open a copyable report, and Decisions, every act a
	 * queue can take back (Organize's answers and Sift's own filings) with its Undo. One place to
	 * look for what happened, whichever kind of thing happened.
	 *
	 * **Not on Organize.** That screen's promise is that it empties: it is the work waiting on
	 * somebody, and a record of what was decided competes with the work. This list only ever grows,
	 * and a growing list on a screen whose whole point is reaching nought is a screen that never
	 * looks finished.
	 *
	 * ## THERE IS NO PER-ACCOUNT "YOUR YEAR" HERE, and that is a decision rather than a gap
	 *
	 * A record of what the INSTALLATION did and a story about what one PERSON did are different
	 * surfaces with different audiences: this one is admin-only by its nature, and that one must
	 * not be: it is the thing a guest would most want to see about themselves. It is a fourth
	 * thing and it gets an address of its own the way `/recent` has one, rather than a tab on a
	 * settings pane. Nothing here is in its way; the events it would be built from are these.
	 *
	 * ## Why it re-reads on the library's bell and not on every one
	 *
	 * The change bus is separate bells on purpose, and the cost of each differs by an order of
	 * magnitude. `libraryChanges` is rung by the acts this record is made of: something shared,
	 * hidden, renamed, created, deleted. `arrivals` is rung on every beat of a scan, which on a
	 * large import is several times a second, and following it would put a page read and an exact
	 * count behind each one. So a scan's own lines land when the next act does or when this pane is
	 * opened again, which is the honest trade and is written down here rather than discovered.
	 */
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

	/* The line, what can be done about it, and its moment, each in a column of its own. Fixed
	   tracks, so every time ends at the same edge whether or not its line has a Report or an
	   Undo, and a line that wraps keeps its time beside it rather than under it. The time track
	   holds a full date in the data face; the action track holds two of the link-tone presses. */
	const HISTORY_COLUMNS: readonly Column[] = [
		{ id: 'what', width: 'minmax(0, 1fr)' },
		{ id: 'act', width: '9rem', align: 'end' },
		{ id: 'at', width: '11rem', align: 'end' }
	];

	/* The same row at a phone's width: the line alone, with its presses and its moment on a line
	   of their own under it. The two fixed tracks above are 20rem, and a phone line is about 24rem,
	   so beside them the sentence would be left a word's width and read one word to a line. */
	const HISTORY_CARD: readonly Column[] = [{ id: 'what', width: 'minmax(0, 1fr)' }];

	let events = $state<LedgerEvent[]>([]);
	let total = $state(0);
	let failed = $state<string | null>(null);
	let reading = $state(false);
	/* Nothing has been read yet, which is a different state from "there is nothing". One draws
	   nothing at all and the other draws the empty state, and showing the second while the first is
	   true tells somebody their library has no history when Sift has not looked yet. */
	let asked = $state(false);

	interface Props {
		/**
		 * The act this list opens narrowed to, where it was reached for one: "Saved to a device"
		 * opens it on `saved`, "How long tasks take" on `ran`. Only where it STARTS: the Action
		 * choice is still the reader's to change, and a caller that wants a different start mounts
		 * the list again (Activity keys it on this).
		 */
		verb?: string;
		/**
		 * Whether it opens on the decisions alone: what was decided on Organize and what Sift filed
		 * by itself, each with its Undo, where it was reached from Organize's Decisions (or the old
		 * address of the record that list replaced). Only where it starts, as `verb`.
		 */
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

	/* Built from the vocabulary rather than listed here. See `kindChoices`. A pane holding its own
	   list of acts is a pane that goes one line short the day an act is added. */
	const KIND_CHOICES = kindChoices();
	const VERB_CHOICES = verbChoices();

	const narrowing = $derived({
		kind: kind === ANY ? undefined : kind,
		verb: verb === ANY ? undefined : verb,
		decisions: showing === DECISIONS ? true : undefined
	});

	/* Where the page on screen starts, counting from zero. A page is `PAGE` lines, newest first, so
	   page three is the same lines on every screen and the pager can name it by number. */
	let offset = $state(0);

	/** Read the page starting at `at` for whatever is being asked for now, replacing the one on
	    screen. A page past the end (lines taken away since) is read again at the last page. */
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

	/*
	 * SOMETHING HAPPENED WHILE THIS WAS OPEN: the page on screen read again.
	 *
	 * The same page, not the first: somebody reading page five is not taken back to the top by an
	 * act landing somewhere else. On the first page the new lines arrive at its top. A folded line
	 * that grew (a task still running hands its line back with a newer id and a larger count) is
	 * the server's one line for its press, so reading the page again never holds the press twice.
	 */
	async function refresh(): Promise<void> {
		if (reading) return;
		/* A refusal is taken and dropped here, deliberately, and it is caught on the call rather than
		   around it so that nothing else in this function can be swallowed with it. Nothing was
		   asked for: the screen still shows what it read, and a red box about a background re-read
		   would be the only thing on this pane that was about nothing somebody did. The next press
		   says so if it is still broken. */
		const page = await readLedger({ ...narrowing, limit: PAGE, offset }).catch(() => null);
		if (page === null) return;
		events = page.items;
		total = page.total;
	}

	/*
	 * TAKING ONE BACK, from the list that holds every act rather than from the screen it was taken
	 * on.
	 *
	 * An event with a receipt IS a workbench decision (the row is the same row, and the event's
	 * id is the decision's), so the undo here is the very door a queue screen pressed, reached
	 * through `undoDecision`. Offered only where there is a receipt, it has not already been taken
	 * back, and the queue that wrote it can take any of its decisions back at all (`final`, which
	 * the server answers because reversibility is the queue registry's and not the row's).
	 *
	 * Here rather than in a band above the work on every queue screen: that would be context in
	 * front of the work, on every one of them, showing one queue's thread. This is one list of
	 * everything, in the place the other two answers to "what has this thing been doing" already
	 * live.
	 */
	let undoing = $state<string | null>(null);

	/** Whether this line can still be taken back. See the note above. A folded line is taken back
	    whole, by Undo all, and never by its newest act alone. */
	function canUndo(event: LedgerEvent): boolean {
		return (
			(event.folded ?? 1) <= 1 &&
			!!event.receipt &&
			!event.receipt.reversed_at &&
			!event.receipt.final
		);
	}

	/*
	 * UNDO ALL, on a line that stands for a press of many decisions: a task's four thousand
	 * filings. The server reads the press again under the same narrowing and puts each decision back
	 * through its own undo (`ledger_router.undo_all`); offered while any of them still stands.
	 */
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

	/* The folded line Undo all was pressed on, while the question is up. It asks first: one press
	   puts back every decision of a run, which may be thousands of filings, and the count is
	   what somebody needs to see before they agree to that. */
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

	/*
	 * A RUN'S OWN REPORT, from its line.
	 *
	 * Every long pass that finishes writes a `ran` event whose subject is the run itself (see
	 * `kernel/jobs/ledger.py`), and every run has a plain-text report on the server (what it did,
	 * how long, on which device, at what settings), made to be pasted to somebody comparing their
	 * machine with yours. The line in History that says the pass ran is the natural door to it, so
	 * it opens there, under the line, with the copy beside it. Read when asked for and not before:
	 * a page of fifty lines is fifty reports nobody asked to see.
	 */
	type RunReport = components['schemas']['RunReportView'];

	/**
	 * The run whose report a line opens, where the server says it has one.
	 *
	 * The SERVER's answer (`report`), not a guess from the subject: a task's own run line (a
	 * backup, a clean-up) names the task under the same subject kind and has no report, and
	 * offering one would answer "Couldn't load that report".
	 */
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

	/* Which list this is, as a key. Two narrowings are two different lists, so where somebody was in
	   one says nothing about the other. */
	const key = $derived(JSON.stringify(narrowing));

	/**
	 * The element this pane can be found by. The box it scrolls inside is worked out FROM it, at
	 * the moment it is wanted, and never kept.
	 *
	 * Kept, it would be wrong: `scrollParent` answers by computed overflow, and the box here is a
	 * scrolling area that declares itself scrollable after it has set itself up, so asked during
	 * mount it answers `document.documentElement`, which never scrolls. The listener would sit on
	 * `<html>`, nothing would ever be recorded, `at` would stay nought, and the restore below would
	 * be skipped every time.
	 */
	let anchor: HTMLElement | null = null;

	/**
	 * The scrolling box, once it has answered as one.
	 *
	 * Kept only after it has been found to be a real box: `document.documentElement` is what
	 * `scrollParent` answers before the area has declared itself scrollable, and it never scrolls,
	 * so accepting it would freeze the wrong element in place for the life of the pane. Held
	 * because the LAST moment the position is wanted is the moment this pane is taken apart, and by
	 * then walking up from `anchor` can find nothing at all.
	 */
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

	/*
	 * WHERE THE LIST WAS BEING READ, taken at the moment somebody starts to leave and never from a
	 * scroll event.
	 *
	 * Watching the scrolling records the WRONG POSITION: pressing a name in a line scrolls the box
	 * from deep down to near the top a fraction of a second later, with every line still drawn and
	 * the box still its full height: a screen on its way out being tidied, arriving as an
	 * ordinary scroll. So the last thing recorded would be a reset, and coming back would put
	 * people at the top of a list they had read a long way down. `navigation.svelte` follows the
	 * same rule for the walls.
	 *
	 * Leaving is the one moment the position is both wanted and honest, and this is the door that
	 * says so: it runs before the router has begun taking anything apart.
	 */
	beforeNavigate(() => {
		const here = scrollBox();
		if (!here || !asked) return;
		places.set(key, { offset, at: here.scrollTop });
	});

	/* The page on its own, so a feed somebody paged through without scrolling (a short window, a
	   long page) is still remembered. The position it already has is kept.

	   It is also where the scrolling box is FOUND and where the SECOND door below is hung on it: by
	   the time there are lines to scroll past, the area around this pane has settled into being
	   one. Asked again on every change, so an area that declares itself late is still found on the
	   next one, and the listener moves with it. */
	$effect(() => {
		if (events.length === 0) return;
		const on = offset;
		const here = scrollBox();
		untrack(() => places.set(key, { offset: on, at: places.get(key)?.at ?? 0 }));
		if (!here) return;

		/*
		 * THE SECOND DOOR: the press itself, because the leaving it answers announces nothing.
		 *
		 * Pressing a FILE's name opens the popout through `pushState`, and `pushState` in
		 * `@sveltejs/kit` assigns `page.state` wholesale, so `page.state.settings` is gone, the
		 * Settings panel is unmounted, and NO navigation is announced. Taken only at the navigation
		 * door, the position would be lost: standing at 347 in a 450-line feed, pressing a file's
		 * name and closing the popout with Escape would come back 450 lines deep (the line above
		 * keeps the depth) and at the top.
		 *
		 * Recording the position as the pane is taken apart does NOT work, and it is the obvious
		 * answer: it puts nothing back: 0 where 347 was standing. A teardown is late by exactly
		 * the amount that matters: the screen is already being tidied on its way out, which is the
		 * reset the note on `beforeNavigate` below is about.
		 *
		 * A press is early by the same amount. Nothing has moved yet when a finger goes down, so
		 * the reading is the one somebody can see, and the press is the act that may take them
		 * away. `keydown` beside it for the same act reached by the keyboard, where there is no
		 * pointer. In CAPTURE, so a handler that stops the event (a link that opens a panel does),
		 * cannot stop this seeing it. It is recorded on every press rather than only on the ones
		 * that leave, which costs one number written to a map and needs no list of which controls
		 * are a way out.
		 */
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

	/**
	 * Open where it was left: the same page, and the same place on it.
	 *
	 * The position is put back after the lines are drawn, because a box cannot be scrolled to a
	 * place that has nothing in it yet.
	 */
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
				   scrolling area has settled into being one. See `anchor` above. */
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
	<!-- TWO empty states, because they are two different facts and one sentence cannot be both. An
	     install with no record yet is waiting for its first act; a narrowed list with nothing in it
	     has a record and was asked the wrong question, and telling that person "nothing yet" is
	     telling them their library has no history. The same distinction the run history makes. -->
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
	   would match two nested elements on every row of this pane: the same collision as a
	   second `.pane` inside the settings shell. Styles are not at risk (Svelte scopes them), but
	   a test reading the sentences would find twice as many as there are. */
	/* WRAPPED. The row this sits in keeps its subject on ONE line and clips the end (`DataRow`'s
	   `.subject`, right for a folder name), and a line naming nineteen files would be cut off
	   after the fourth under the time beside it, with nothing saying so. A sentence is read to
	   its end, so it takes as many lines as it needs; `anywhere` lets a long file name break
	   rather than push the row wide. */
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
	   reversals reads as though nothing had ever happened), and says so in QUIETER INK rather than
	   by being struck through, as `HistoryRow` does: a line through a sentence is read as "this is
	   not true", and it was true at the time. */
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

	/* A run's report: the server's plain text, kept as it was written so it pastes as it reads. In
	   the data face, and wrapped, so a long line does not push the row wide. Set in from the line it
	   belongs to rather than boxed: a box is `Panel`'s to draw, and a rule down its edge says "this
	   belongs to the line above" without being a second surface inside a list. */
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
