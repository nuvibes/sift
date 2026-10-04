<script module lang="ts">
	import type { FacetValue } from './facet-labels';

	type Value = FacetValue;

	/*
	 * What the same question answered last time, kept outside the component.
	 *
	 * The panel unmounts when the menu shuts, so without this every reopen draws "Counting..." for a
	 * round trip; it is seeded from here and re-asks in the background. Keyed on the question, the
	 * columns and `vault.generation`, so counts taken with the vault open are unreachable once it
	 * locks. Bounded, oldest out first. `<script module>`, since an instance `const` is rebuilt by
	 * the mount it serves.
	 */
	const REMEMBERED = new Map<string, Record<string, Value[]>>();
	const REMEMBER_AT_MOST = 12;

	function remember(key: string, answer: Record<string, Value[]>) {
		REMEMBERED.delete(key);
		REMEMBERED.set(key, answer);
		while (REMEMBERED.size > REMEMBER_AT_MOST) {
			const oldest = REMEMBERED.keys().next().value;
			if (oldest === undefined) break;
			REMEMBERED.delete(oldest);
		}
	}
</script>

<script lang="ts">
	import { untrack, type Snippet } from 'svelte';
	import BarPanel from '$lib/components/common/BarPanel.svelte';
	/*
	 * What the files on screen are made of, in columns, with a count on every value.
	 *
	 * It drops down and pushes the grid rather than covering it, so each adjustment is seen; never a
	 * dialog or a sheet. A click filters at once, with no Apply. Every count is the server's, from
	 * the statement that decides what the grid shows, so on a person's page the numbers are theirs.
	 */
	import { goto } from '$app/navigation';
	import { clearStored, readStored, writeStored } from '$lib/shell/remembered.svelte';
	import { api } from '$lib/api/client';
	import { Button, DateRange, NarrowBox, Pressable, Select } from '$lib/components/common';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Checkbox, { type CheckState } from '$lib/components/common/Checkbox.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Scroller from '$lib/components/common/Scroller.svelte';
	import { quoted } from '$lib/search/search.svelte';
	import { counted, filesSaid } from '$lib/entity/entity-counts';
	import { session } from '$lib/shell/session.svelte';
	import { vault } from '$lib/shell/vault.svelte';
	import { arrivals, libraryChanges } from '$lib/library/changes.svelte';
	import {
		facetIcon,
		facetLabel,
		bandOrder,
		countsUp,
		facetRoute,
		facetsFor,
		facetValueIcon,
		facetValueLabel,
		boxColour,
		rememberFacetNames,
		type FacetCounts,
		type Subject
	} from './facet-labels';
	import { stage } from './stage.svelte';
	import type { Pointing } from './screen-bar.svelte';

	interface Props {
		/*
		 * WHICH NOUN IS ON THE WALL, which decides the columns wherever that noun is listed. Files
		 * unless a screen says otherwise.
		 */
		subject?: Subject;
		/*
		 * What this screen is asking for, so the counts describe the set on screen. A name may hold
		 * SEVERAL values, in the noun's grammar (`picksAreRepeated`).
		 */
		query: Record<string, string | string[]>;
		/** Turn one value of one column on or off. The bar owns how that is written down. */
		onpick: (facet: string, value: string, next?: CheckState) => void;
		/** Replace a whole named filter with one value, or clear it when the value is empty. */
		onset: (facet: string, value: string) => void;
		/** Which of the three this value of this column is in: off, included, or excluded. */
		stance: (facet: string, value: string) => CheckState;
		/** What each column is filtered by right now, so a column can be counted WITHOUT its own. */
		chosen: (facet: string) => string[];
		/*
		 * What is drawn UNDER the columns: the kept filters, a snippet because applying one is an
		 * address on the bar and a cell's source in Theater, which this panel knows nothing of.
		 */
		foot?: Snippet;
		/**
		 * A control at the HEAD of the panel, above the columns (which source a wall is filtering),
		 * inside the box, since anything outside the absolutely positioned panel lands on the page.
		 */
		head?: Snippet;
		/**
		 * How many files the screen is showing, scoped and filtered: the same number the page
		 * header carries. A span column has no values to count, so it says this instead.
		 */
		total?: number;
		/*
		 * Dimensions to bring to the FRONT of the columns, for editing a kept filter, so its own
		 * dimensions show. FIXED while the edit is open (the caller's stored dimensions, so a column
		 * never leaves under the pointer), and the remembered columns are not written meanwhile.
		 */
		lead?: string[];
		/**
		 * What the columns spread, so pointing at anything that WILL filter says what it will filter:
		 * Theater lights the cells it edits (`Narrowing.pointing`). Not the kept filters in the foot,
		 * which also edit and delete. Absent where the target is the screen itself.
		 */
		pointing?: Pointing;
		/**
		 * What the target filters by on its own control, applied to every column's count, its own
		 * included, since the panel cannot take it back (`Narrowing.within`).
		 */
		within?: Record<string, string | string[]>;
		/**
		 * The facets every row on this wall has one value of (`ScreenTools.fixed`), left out of the
		 * columns: a column of one row says only what the wall already is.
		 */
		fixed?: readonly string[];
	}

	let {
		subject = 'asset',
		query,
		onpick,
		onset,
		stance,
		chosen,
		foot,
		head,
		lead = [],
		total,
		pointing,
		within = {},
		fixed = []
	}: Props = $props();

	/** What a span column says in place of a list: the count it filters, or nothing yet. */
	function matching(count: number | undefined): string {
		if (count === undefined) return 'Counting\u2026';
		return filesSaid(count);
	}

	/* The dimensions are `facet-labels.ts`'s, since `SavedFilterButton` names them too. */

	/**
	 * Whether a dimension is a span the panel draws itself rather than a set the server counts: one
	 * question for what to draw and what not to ask about.
	 */
	function isASpan(key: string): boolean {
		return facetsFor(subject).some((one) => one.key === key && one.span === true);
	}

	/** The dimensions this account may ask about, for the noun on this wall. */
	const OFFERED = $derived(
		facetsFor(subject).filter((one) => (!one.admin || session.isAdmin) && !fixed.includes(one.key))
	);

	/*
	 * The order the columns come in: `OFFERED`'s, with any lead first, both keeping their own order,
	 * so paging past the lead lands on the ordinary next five.
	 */
	const ORDER = $derived.by(() => {
		if (lead.length === 0) return OFFERED;
		const wanted = new Set(lead);
		return [
			...OFFERED.filter((one) => wanted.has(one.key)),
			...OFFERED.filter((one) => !wanted.has(one.key))
		];
	});

	/** Five at a time, each header swapping what it shows, so every filter is one click away; five,
	 *  not six, so a name still fits a column on a 1280 screen. */
	const COLUMNS = 5;

	/*
	 * Which dimension each column is showing, remembered between visits.
	 *
	 * Not in the address (a way of looking, not the question), and in the browser, as the panel's
	 * open state is, so nothing reshuffles after load. Validated on the way back in (a dropped or
	 * admin-only dimension, duplicates) and topped up from the ordinary order. Per noun, since
	 * walls share no columns.
	 */
	const COLUMNS_KEY = 'sift.filters.columns';

	/** Where this noun's columns are remembered. */
	function columnsKey(noun: Subject): string {
		return `${COLUMNS_KEY}.${noun}`;
	}

	/*
	 * A note kept under the file wall's older key is moved to its own key rather than dropped: from
	 * `remembered`, so a browser that never opens this is never written, and with no "done" flag.
	 */
	function movedToTheFileWall(): void {
		const old = readStored(COLUMNS_KEY);
		if (old === null) return;
		if (readStored(columnsKey('asset')) === null) writeStored(columnsKey('asset'), old);
		clearStored(COLUMNS_KEY);
	}

	function firstFive(): string[] {
		return ORDER.slice(0, COLUMNS).map((one) => one.key);
	}

	function remembered(): string[] {
		movedToTheFileWall();
		const stored = readStored(columnsKey(subject));
		if (!stored) return firstFive();
		/* Widened: an `as const` key set refuses to be asked about any other string, which is the job. */
		const offered: Set<string> = new Set(OFFERED.map((one) => one.key));
		const kept: string[] = [];
		for (const key of stored.split(',')) {
			if (offered.has(key) && !kept.includes(key)) kept.push(key);
		}
		for (const key of firstFive()) {
			if (kept.length >= COLUMNS) break;
			if (!kept.includes(key)) kept.push(key);
		}
		return kept.slice(0, COLUMNS);
	}

	/** The five columns to open on: the lead's, when there is one, otherwise what was remembered. */
	function seeded(led: string): string[] {
		return led ? ORDER.slice(0, COLUMNS).map((one) => one.key) : remembered();
	}

	/* Seeded at INIT, not by the effect below, which runs after mount and would undo a press made
	   in between. `untrack`: this wants the value the panel was built with; the effect follows it. */
	let showing = $state<string[]>(seeded(untrack(() => lead.join(','))));

	/** How far along the list of dimensions the columns have been paged, written by the re-seeding. */
	let from = $state(0);

	/*
	 * A lead arriving or leaving re-seeds the columns, and so does the noun (the drawer keeps this
	 * panel mounted between screens).
	 *
	 * Keyed on a derived string, since a defaulted prop (`lead = []`) is a fresh value on every read
	 * and would reset the page on any prop change. `untrack` around the writes, which it would
	 * otherwise depend on.
	 */
	const led = $derived(lead.join(','));
	const seedOn = $derived(`${subject}|${led}`);
	let seededFor: string = untrack(() => `${subject}|${lead.join(',')}`);

	$effect(() => {
		const now = seedOn;
		untrack(() => {
			if (seededFor === now) return;
			seededFor = now;
			from = 0;
			showing = seeded(led);
		});
	});

	/* Written from what is TRUE rather than from each press, since three things change the columns.
	   Not while a lead holds (those are the app's, for an edit), read FIRST so nothing else is a
	   dependency then. */
	$effect(() => {
		if (led.length > 0) return;
		const noun = subject;
		const columns = showing;
		/* Not before seeding for this noun, or the last wall's columns land under the new key. */
		if (seededFor !== `${noun}|${led}`) return;
		writeStored(columnsKey(noun), columns.join(','));
	});

	/*
	 * `added:` as a calendar rather than as something to type: not a column (a date has unbounded
	 * values), a range (`added:<from>..<to>`). Read from `query`, never the address, since a
	 * Theater cell's search is the cell's.
	 */
	const span = $derived.by(() => {
		// One span: a repeated parameter was hand-written, and the server reads the last.
		const held = query.added;
		const only = Array.isArray(held) ? (held.at(-1) ?? '') : (held ?? '');
		const [from, to] = only.split('..');
		return { from: from || undefined, to: to || undefined };
	});

	function setSpan(next: { from?: string; to?: string }) {
		// One end alone is still a range: `added:<from>..` is "since then", which the server reads.
		if (!next.from && !next.to) return onset('added', '');
		onset('added', `${next.from ?? ''}..${next.to ?? ''}`);
	}

	function pageBy(step: number) {
		const next = Math.max(0, Math.min(ORDER.length - COLUMNS, from + step));
		if (next === from) return;
		from = next;
		showing = ORDER.slice(from, from + COLUMNS).map((one) => one.key);
	}

	/* Choosing a dimension already on screen EXCHANGES the two columns: five slots, no duplicates. */
	function swap(at: number, key: string) {
		const already = showing.indexOf(key);
		showing = showing.map((each, index) =>
			index === at ? key : index === already ? showing[at] : each
		);
	}

	/* What came back, per dimension, replaced WHOLE, so no older numbers linger under a new question. */
	let counts = $state<Record<string, Value[]>>({});
	let asking = $state(false);
	/* The question last asked, without the bells, so a bell's re-count can be told from a new one. */
	let lastQuestion = '';

	const asked = $derived(
		JSON.stringify(
			[query, within].map((one) =>
				Object.entries(one).sort(([name], [other]) => name.localeCompare(other))
			)
		)
	);

	$effect(() => {
		void asked;
		/* A span is drawn from the query and has nothing to count, so it is never asked about. */
		const wanted = showing.filter((facet) => !isASpan(facet));
		/* The NOUN is in the key: `tags` counts files on one wall and people on another. */
		const noun = subject;
		/* The bells too, so a change elsewhere re-counts; the numbers on screen stay meanwhile. */
		const key = `${vault.generation}|${libraryChanges.generation}|${arrivals.generation}|${noun}|${wanted.join(',')}|${asked}`;
		/* Seeded before the request, so "Counting..." shows only for a question never asked. */
		const known = REMEMBERED.get(key);
		if (known) counts = known;
		const question = `${noun}|${wanted.join(',')}|${asked}`;
		const moved = question !== lastQuestion;
		lastQuestion = question;
		void (async () => {
			asking = known === undefined && moved;
			try {
				const answers = await Promise.all(
					wanted.map(async (facet) => {
						try {
							const answer = await api.get<FacetCounts>(facetRoute(noun), {
								query: { ...queryFor(facet), facet, limit: 200 }
							});
							/* The names beside the ids, kept for the chips, which hold only the id. */
							rememberFacetNames(facet, answer.values);
							return [facet, answer.values] as const;
						} catch {
							// A column that could not be filled draws nothing; the rest still holds.
							return [facet, []] as const;
						}
					})
				);
				const next = Object.fromEntries(answers);
				counts = next;
				remember(key, next);
			} finally {
				asking = false;
			}
		})();
	});

	/* How many values a column shows before "view more", per column. */
	const FIRST = 5;
	let expanded = $state<Record<string, boolean>>({});
	let narrowing = $state<Record<string, string>>({});

	/**
	 * The values to draw for one column: what the box filters to, then the cut. The box is needed:
	 * Tags and People run to hundreds. A banded column is put in the server's cut order
	 * (`facet-labels`' table, gate-held), since count order scrambles a ladder; it is cut and
	 * searched like the rest, so what folds is its tail.
	 */
	function inBandOrder(facet: string, rows: Value[]): Value[] | null {
		/* A column of numbers (an age, an O count) is its own ladder: ascending, by the number. */
		if (countsUp(facet))
			return [...rows].sort((one, other) => Number(one.value) - Number(other.value));
		const ladder = bandOrder(facet);
		if (!ladder) return null;
		const at = (row: Value) => {
			const found = ladder.indexOf(row.value);
			// A band this client does not know sinks rather than vanishing; `sort` is stable.
			return found === -1 ? ladder.length : found;
		};
		return [...rows].sort((one, other) => at(one) - at(other));
	}

	/**
	 * What one column holds once the box has filtered it, in band order where there is one: matched
	 * against the words on screen ("1 to 3 minutes") and the stored value (`60s..<3m`) alike.
	 */
	function narrowed(facet: string): Value[] {
		const all = counts[facet] ?? [];
		const needle = (narrowing[facet] ?? '').trim().toLowerCase();
		const kept = needle
			? all.filter(
					(one) =>
						nameOf(facet, one).toLowerCase().includes(needle) ||
						one.value.toLowerCase().includes(needle)
				)
			: all;
		return inBandOrder(facet, kept) ?? kept;
	}

	function shown(facet: string): Value[] {
		const kept = narrowed(facet);
		return expanded[facet] ? kept : kept.slice(0, FIRST);
	}

	function hiddenCount(facet: string): number {
		return Math.max(0, narrowed(facet).length - FIRST);
	}

	/**
	 * Whether a column offers a box to filter itself with: past the cut, the same number, so a column
	 * offering "View N more" always has a box.
	 */
	function needsABox(facet: string): boolean {
		// A ladder too: it is cut like the rest, so it gets the same way through.
		return (counts[facet] ?? []).length > FIRST;
	}

	/*
	 * A column is counted with every filter in force EXCEPT its own, so it stays multi-select: each
	 * alternative shows what it would give if picked, instead of the column emptying itself. Every
	 * other column applies, and `within` applies to all, a repeat meaning "and".
	 */
	function queryFor(facet: string): Record<string, string | string[]> {
		const { [facet]: _mine, ...rest } = query;
		const out: Record<string, string | string[]> = { ...rest };
		for (const [name, value] of Object.entries(within)) {
			const held = [out[name] ?? [], value].flat();
			out[name] = held.length === 1 ? held[0] : held;
		}
		return out;
	}

	/**
	 * What a value READS as: the server's `label` first, the only source of a name for an id value,
	 * else derived from the value (`facet-labels`).
	 */
	function nameOf(facet: string, row: Value): string {
		return row.label ?? facetValueLabel(facet, row.value);
	}

	/*
	 * The chooser's list, in alphabetical order, which is NOT the order the columns come in: those are
	 * chosen by how often people filter by them, while a list being read needs an order guessable
	 * without learning. `localeCompare`, so capitals do not sort first.
	 */
	const options = $derived(
		OFFERED.map((one) => ({ value: one.key, label: one.label })).sort((a, b) =>
			a.label.localeCompare(b.label)
		)
	);

	/** Which of the three a row is in, so it reads as chosen, refused, or merely clickable. */
	function picked(facet: string, value: string): CheckState {
		return stance(facet, value);
	}

	/* How many of a column's own values are chosen, said on its heading so it reads as multi-select. */
	function pickedCount(facet: string): number {
		return chosen(facet).length;
	}
</script>

<BarPanel label="Filter by" row>
	{#if head}
		<!-- What the columns are ABOUT, on a row of its own above them. -->
		<div class="head">{@render head()}</div>
	{/if}
	<Tooltip label="Earlier filters">
		<Button
			tone="ghost"
			size="small"
			icon="chevron_left"
			aria-label="Earlier filters"
			disabled={from === 0}
			onclick={() => pageBy(-COLUMNS)}
		/>
	</Tooltip>

	<!-- The whole block of columns spreads `pointing`, so crossing a gap does not blink the wash. -->
	<div {...pointing} class="columns">
		{#each showing as facet, at (at)}
			{@const isSpan = isASpan(facet)}
			<section class="column">
				<!-- The header is a dropdown, so every filter is one click from any column. `portalTo`, so it
				     opens while the screen is filled (`Select.portalTo`); searchable past the chooser's
				     threshold (`Select.searchable`). -->
				<Select
					value={facet}
					{options}
					label="What this column shows"
					portalTo={stage.whatFillsTheWindow}
					onValueChange={(next) => swap(at, next)}
				>
					<!-- The dimension's own mark (`facetIcon`), in the chooser and on the heading through
					     `preview`, taking no width; not on the bar's chip, whose token names it. -->
					{#snippet preview(option)}
						{@const mark = facetIcon(option.value, subject)}
						<!-- Every heading's mark in the ordinary ink: a heading found nothing on its own. -->
						{#if mark}
							<Icon name={mark} size={16} />
						{/if}
					{/snippet}
				</Select>

				<!-- How many this column is filtered by: the invitation to pick more than one. -->
				{#if pickedCount(facet) > 0}
					<p class="picked">
						<!-- How many, not how they combine, which the bar's chip says. -->
						{pickedCount(facet) === 1 ? '1 chosen' : `${pickedCount(facet)} chosen`}
					</p>
				{/if}

				<!-- A date is a span: the field takes the column, and its calendar opens over the panel. -->
				{#if isSpan}
					<div class="when">
						<DateRange value={span} onchange={setSpan} label="Added between">
							{#snippet beside()}
								{#if span.from || span.to}
									<Button tone="quiet" onclick={() => onset('added', '')}>Clear</Button>
								{/if}
							{/snippet}
						</DateRange>
					</div>
				{:else if needsABox(facet)}
					<NarrowBox
						value={narrowing[facet] ?? ''}
						oninput={(event) => (narrowing[facet] = event.currentTarget.value)}
						label="Filter the {facetLabel(facet, subject)} list"
					/>
				{/if}

				<!-- The column scrolls like every region; its ceiling is on this file's own box. -->
				<div class="column-box">
					<Scroller>
						<ul>
							{#each shown(facet) as row (row.value)}
								{@const state = picked(facet, row.value)}
								<li>
									<!-- The whole row is the target, the box inside not separately clickable: two
									     nested controls a pixel apart would be two answers to one click. -->
									<Pressable
										class="value {state}"
										feedback="wash"
										radius="md"
										aria-pressed={state !== 'off'}
										onclick={() => onpick(facet, row.value)}
									>
										<span class="mark">
											<Checkbox {state} mark />
										</span>
										{#if facetValueIcon(facet, row.value)}
											{@const paint = boxColour(row.value)}
											<!-- The author's mark, before the word, named on hover. A stash-box row in that
											     box's colour, as `EnrichmentMarks` paints it (`boxColour`; the rule in
											     `app.css`). -->
											<Tooltip label={nameOf(facet, row)} placement="top">
												<span
													class="glyph"
													class:box-mark={paint !== undefined}
													style:--box-colour={paint}
												>
													<Icon name={facetValueIcon(facet, row.value)!} size={16} />
												</span>
											</Tooltip>
										{/if}
										<span class="name">{nameOf(facet, row)}</span>
										<!-- `count`, not `files`: on a wall of people the number is people. -->
										<span class="files">{counted(row.count)}</span>
									</Pressable>
								</li>
							{/each}
							{#if shown(facet).length === 0}
								<!-- A span says how many files the screen shows; an empty value column stays and
								     says so, so the panel never reshuffles between pages. -->
								<li class="none">
									{asking
										? 'Counting\u2026'
										: isSpan
											? matching(total)
											: 'Nothing to filter by here'}
								</li>
							{/if}
						</ul>
					</Scroller>
				</div>

				{#if hiddenCount(facet) > 0 || expanded[facet]}
					<!-- Expands IN PLACE, never a sheet over the results; quiet, a footnote to a list. -->
					<Button
						tone="quiet"
						size="small"
						class="more"
						onclick={() => (expanded[facet] = !expanded[facet])}
					>
						{expanded[facet] ? 'View fewer' : `View ${counted(hiddenCount(facet))} more`}
					</Button>
				{/if}
			</section>
		{/each}
	</div>

	<Tooltip label="Further filters">
		<Button
			tone="ghost"
			size="small"
			icon="chevron_right"
			aria-label="Further filters"
			disabled={from >= ORDER.length - COLUMNS}
			onclick={() => pageBy(COLUMNS)}
		/>
	</Tooltip>

	<!-- Under the columns, inside the same panel (`SavedFilters`); nothing when the host offers none. -->
	{#if foot}
		<div class="foot">{@render foot()}</div>
	{/if}
</BarPanel>

<style>
	/* Every column is one track wide, the calendar included: it floats over the panel at whatever
	   size it needs. */

	/*
	 * The foot and the head each take a line of their own (`flex-basis: 100%` in the wrapping row),
	 * inset by what the paging arrows take (a small icon button, `--space-8`, plus the row gap), so
	 * head, columns and foot start at one edge.
	 */
	.head {
		flex-basis: 100%;
		padding-inline: calc(var(--space-8) + var(--space-2));
	}

	.foot {
		flex-basis: 100%;
		display: grid;
		gap: var(--space-3);
		padding-inline: calc(var(--space-8) + var(--space-2));
	}

	/* No arrows to clear when the columns are stacked; the same breakpoint as `.columns`. */
	@media (max-width: 900px) {
		.foot {
			padding-inline: 0;
		}
	}

	.columns {
		display: grid;
		grid-template-columns: repeat(5, minmax(0, 1fr));
		gap: var(--space-4);
		flex: 1;
		min-width: 0;
	}

	/*
	 * On a narrow screen the columns become one, still in the flow of the page, pushing the grid down
	 * rather than covering the results.
	 */
	@media (max-width: 900px) {
		.columns {
			grid-template-columns: 1fr;
		}
	}

	.picked {
		margin: 0;
		color: var(--sift-accent-text);
		font: var(--text-micro);
	}

	.column {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		min-width: 0;
	}

	.column ul {
		display: flex;
		flex-direction: column;
		gap: 2px;
		margin: 0;
		padding: 0;
		list-style: none;
	}

	/*
	 * The expanded column scrolls inside the panel: one `minmax(0, 1fr)` row gives the scroller a
	 * definite height, which an auto row would not, as in the pick dialog and the select's menu.
	 */
	.column-box {
		display: grid;
		grid-template-rows: minmax(0, 1fr);
		max-block-size: 320px;
	}

	/* `:global`, a class handed to Button, scoped to the column. */
	.column :global(.more) {
		align-self: center;
	}

	/*
	 * Box, then name, then count: the name takes the free space, so labels share one edge and counts
	 * sit hard right. A whole pressed row (`Pressable`, `:global` for the handed class), in the bar
	 * chip's three states (`off`, `on`, `out`).
	 */
	.column :global(.value) {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		width: 100%;
		padding: var(--space-1) var(--space-2);
		color: var(--sift-ink-2);
		font: var(--text-label);
		text-align: start;
		/* The change steps over --dur-instant rather than happening between frames. */
		transition:
			color var(--dur-instant) var(--ease),
			background var(--dur-instant) var(--ease);
	}

	/* A finger's height for a row on a phone, as every list of rows there. */
	@media (max-width: 767px) {
		.column :global(.value) {
			min-block-size: var(--touch-target);
		}

		/* One inset on the phone's sheet: the head, the foot and the columns end at one right edge. */
		.head,
		.foot {
			padding-inline: var(--space-4);
		}
	}

	.column :global(.value:hover) {
		color: var(--sift-ink);
	}

	.column :global(.value.on) {
		background: var(--sift-accent-bg);
		color: var(--sift-accent-text);
	}

	/*
	 * Refused, in the refusal colour at its quiet weight, since two tints of one hue cannot be told
	 * apart at a glance; the struck-through name says it in shape too.
	 */
	.column :global(.value.out) {
		background: var(--sift-bad-bg);
		color: var(--sift-bad-text);
	}

	/* Struck through as well as tinted: the row reads as refused even in monochrome. */
	.column :global(.value.out .name) {
		text-decoration: line-through;
		text-decoration-thickness: 1px;
	}

	/* The box does not take the click: the whole row does. See the markup. */
	.mark {
		display: inline-flex;
		pointer-events: none;
	}

	/*
	 * The author's mark on an Enriched by or Created by row, in the accent, as on the card and the
	 * file's row; a stash-box row is painted in its box's colour (`box-mark`) over this.
	 */
	.glyph {
		display: inline-flex;
		flex: none;
		color: var(--sift-accent);
	}

	/* Takes the leftover width, so the count sits right; ellipsises rather than widen the track. */
	.name {
		flex: 1;
		min-inline-size: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		text-align: start;
	}

	/* The count is the server's, scoped to this account and narrowed to this screen. */
	.files {
		flex: none;
		color: var(--sift-ink-3);
		font: var(--text-data);
	}

	/* Readable ink: whether the column is empty or still counting is read. */
	.none {
		padding: var(--space-1) var(--space-2);
		color: var(--sift-ink-3);
		font: var(--text-label);
	}

	/*
	 * The date field, at the top of the column like every column's box; no `flex-basis: 100%`, which
	 * in a flex column would push it to the bottom. Its Clear is the field's own (`DateRange.beside`).
	 */
	.when {
		display: block;
	}
</style>
