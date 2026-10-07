<script lang="ts">
	/*
	 * Where a stash-box disagrees, one field at a time.
	 *
	 * Two values side by side and two buttons, deliberately all of it: any other arrangement (a
	 * diff, a merge view, a form) makes somebody read a layout before the question, and the
	 * question is always which of these two is right.
	 *
	 * Yours is on the left because it is the one that stays if nothing is pressed. Keeping it
	 * writes nothing, which is why that button is the quiet one: it makes the row's current state
	 * explicit so the row can leave the list.
	 *
	 * It is the timeline's size, a constraint rather than a taste. These sit in the column beside
	 * the history thread, so they are read against a history row, about 50px (a mark, a sentence
	 * and a footnote). Everything is on one line at the thread's own row height; only a value too
	 * long to sit beside its neighbour makes a second line.
	 *
	 * The rows are a table. A flex line sizes every cell to its content, so the field, the
	 * stash-box and the two answers would begin at different x on each row with no column to read
	 * down. Columns fix that by construction, and a header row says "Yours" and "Theirs" once
	 * instead of on every row. The row height is the small button plus the row's vertical inset;
	 * the header row adds only a label's height.
	 */
	import { Button, Chip, Problem } from '$lib/components/common';
	import { decided } from '$lib/organize/organize.svelte';
	import { fields } from '$lib/entity/records.svelte';
	import {
		boxesOf,
		disagreementsOf,
		problemFrom,
		settle,
		type Disagreement
	} from '$lib/entity/reconcile.svelte';

	interface Props {
		/**
		 * Which record. Required, deliberately: a list of unrelated fields from unrelated records,
		 * asked away from the thing that settles them, is a coin toss ("StashDB says 1991 and you
		 * have 1990"), while on the person's own page it is a question somebody can answer. The
		 * record is the one place.
		 */
		subject: string;
		localId: string;
		/** Told when the list changes, so the line above it can say how many and which boxes. */
		onchange?: (waiting: number, boxes: string[]) => void;
		/**
		 * Told when a press WROTE a field onto the record (taking a box's answer), and again when
		 * that is undone, so the page holding a copy of the record can read it again. Keeping
		 * yours writes no field and does not call it.
		 */
		onwritten?: () => void;
	}

	let { subject, localId, onchange, onwritten }: Props = $props();

	/*
	 * This record's rows, as the server answered them.
	 *
	 * Asked about this record rather than the whole library filtered here. Links are made by a bulk
	 * pass as well as by hand, so a library can hold thousands, and a library-wide memo is
	 * invalidated by every announced change, so the whole-library read would cost tens of seconds
	 * per page view to draw a few rows.
	 *
	 * There is still one question: the server builds both lists from the one rule (see
	 * `Reconciler._conflicts`), so a per-record read cannot disagree with the whole.
	 */
	let items = $state<Disagreement[]>([]);
	let failed = $state<string | null>(null);

	/* The rows whose answer is on its way to the server. A plain Set rather than `$state`: nothing
	   draws it (a pressed row has already left `items`) and `load` reads it, so as state it
	   would make the read an effect that depends on its own presses. A read that was already in
	   flight when a row was pressed must not bring that row back. */
	const sending = new Set<string>();

	/* Which read is the current one. A plain counter rather than `$state`: `load` reads it and
	   writes the rows, and a guard that reads state its own body writes is an effect that runs
	   itself twice. Two records are one address apart and this panel survives the step between
	   them, so a slow answer for the one somebody has left must not land under the one they are
	   looking at. */
	let generation = 0;

	$effect(() => {
		void load();
	});

	function key(one: Disagreement): string {
		return `${one.subject}:${one.local_id}:${one.box_id}:${one.key}`;
	}

	async function load() {
		const mine = ++generation;
		failed = null;
		try {
			const found = await disagreementsOf(subject, localId);
			if (mine !== generation) return;
			items = found.filter((one) => !sending.has(key(one)));
		} catch (error) {
			if (mine !== generation) return;
			// The rows go with the failure. A list left standing under a read that did not happen is
			// a panel reporting yesterday's questions as today's.
			items = [];
			failed = problemFrom(error);
		}
		// Told in every case, not only on the way through the `try`. The line above this panel and
		// the mark beside the History tab both say how many are waiting; a failed read that left
		// the count untouched would leave a sentence counting rows standing over nothing. The
		// honest answer either way is what this panel can show.
		onchange?.(items.length, boxesOf(items));
	}

	/*
	 * One press: the row leaves the moment it is pressed, and the server is told behind it.
	 *
	 * The answer is not in doubt at the press, since the row was on screen because the server said
	 * it was waiting, so the row goes immediately and comes back only if the server refuses, in the
	 * place it stood, with the refusal said above the table.
	 *
	 * `decided` is the workbench's one way of saying a decision was made: the toast with its Undo
	 * (which re-reads this record's rows, so an undone answer is a question again), and the
	 * `answered` signal the record's History thread re-reads on, so "Kept your answer" appears
	 * there without a reload. It is called after the write lands, because a re-read before then
	 * would miss it.
	 *
	 * No button waits on another: each press is its own request about its own row, and the server
	 * serialises the writes. Disabling the whole table for one row's round trip would make three
	 * answers cost three round trips of somebody's time.
	 */
	function choose(one: Disagreement, takeTheirs: boolean) {
		const id = key(one);
		if (sending.has(id)) return;
		sending.add(id);
		const at = items.findIndex((row) => key(row) === id);
		items = items.filter((row) => key(row) !== id);
		failed = null;
		onchange?.(items.length, boxesOf(items));
		void send(one, takeTheirs, at);
	}

	async function send(one: Disagreement, takeTheirs: boolean, at: number) {
		const id = key(one);
		try {
			const done = await settle(one, takeTheirs);
			const wrote = (done.fields ?? 0) > 0;
			if (wrote) onwritten?.();
			const said = done.pieces?.length ? done.pieces : done.said || saidOf(one, takeTheirs);
			decided(said, done.decision_id ?? null, {
				after: async () => {
					await load();
					if (wrote) onwritten?.();
				}
			});
			// And this record's rows read again, BEHIND the row that already left: taking one box's
			// answer changes "yours", so a second box's row about the same field may now agree or
			// disagree differently. Nothing waits on it (the next row can be pressed meanwhile)
			// and a read that began before a later press landed is thrown away by `generation`.
			await load();
		} catch (error) {
			// Back where it stood, and only onto the record it belongs to: this panel survives the
			// step from one record to the next, and a refusal landing after that step is about a
			// record nobody is looking at any more.
			if (one.subject === subject && one.local_id === localId) {
				if (!items.some((row) => key(row) === id)) {
					const place = at < 0 ? items.length : Math.min(at, items.length);
					items = [...items.slice(0, place), one, ...items.slice(place)];
				}
				failed = problemFrom(error);
				onchange?.(items.length, boxesOf(items));
			}
		} finally {
			sending.delete(id);
		}
	}

	/**
	 * What the toast says a press did when the server did not say it.
	 *
	 * The toast reads the receipt's own title (`said` on the answer), because only the server can
	 * say all of it: taking one box's value can set a second box's different value aside, and that
	 * second row does not exist until the first value is written. This is the same sentence for a
	 * single box, for an answer carrying no `said`: the field, both values and the box, never "Took
	 * Example's answer" with nothing saying what the answer was.
	 */
	function saidOf(one: Disagreement, takeTheirs: boolean): string {
		const field = labelOf(one).toLowerCase();
		return takeTheirs
			? `Took ${one.box_name}'s ${field} for ${one.name}: ${theirsOf(one)}, where you had ${mineOf(one)}`
			: `Kept your ${field} for ${one.name}: ${mineOf(one)}, not ${one.box_name}'s ${theirsOf(one)}`;
	}

	/** What one field is called, from the registry every record screen reads. */
	function labelOf(one: Disagreement): string {
		return fields.one(one.subject, one.key)?.label ?? one.key;
	}

	/* Each side IN THE SERVER'S WORDS (`mine_said`, `theirs_said`), which are the words the History
	   line recording the same answer uses: a box's constant reads "Natural" there, and the raw
	   "NATURAL" beside it would be one value in two spellings. The rule is the server's
	   (`records.value_said`), never a copy here; the raw value is drawn only where it has no words. */
	function mineOf(one: Disagreement): string {
		return one.mine_said ?? short(one.mine);
	}

	function theirsOf(one: Disagreement): string {
		return one.theirs_said ?? short(one.theirs);
	}

	/** A value as one short line. Nothing at all is said in words rather than left blank, because a
	 *  blank half of a two-column comparison reads as a missing row. */
	function short(value: unknown): string {
		if (value === null || value === undefined || value === '') return 'nothing';
		if (Array.isArray(value)) return value.map((one) => String(one)).join(', ') || 'nothing';
		return String(value);
	}
</script>

<section class="pile" aria-label="Where a stash-box disagrees">
	<!-- No heading of its own when it is on a record: the line above it already says what these are
	     and how many, and a second heading inside a page that has one is furniture. On the
	     Stash-boxes pane the pane writes the heading, for the same reason. -->
	<Problem message={failed} />

	<!--
		NOTHING IS DRAWN WHILE IT WAITS, and that is the same decision as drawing nothing when there
		is nothing.

		Every other list in the app draws skeleton lines, to say that something is coming so the
		page does not appear to be missing it. That reasoning does not apply to a block which is
		ABSENT BY DEFAULT. Most records are not linked to a box at all, so the honest answer is
		almost always nothing, and three grey lines under every person's name would promise
		something that is not coming.

		A skeleton is a promise that space is about to be filled. Only a block that will certainly
		have content may make it. This one appears when it has something and does not exist until
		then.
	-->
	{#if items.length === 0}
		<!-- Nothing at all. A line reading "nothing disagrees" under every record in the library
		     would be a sentence about a feature rather than a fact about the thing on screen, and
		     this sits at the top of a tab somebody opened to read a history. -->
	{:else}
		<!--
			A table, because it is one. A flex row sizes every cell to its own content, so the
			field, the stash-box and the two answers would begin at a different x on every line with
			nothing to read down.

			A real `<table>` rather than a grid of `<li>`s with `subgrid`: a header row says "Yours"
			and "Theirs" once, for the eye and for a screen reader alike (`<th scope="col">` ties
			`1991-02-02` to Theirs without repeating it on every row), and a grid would have to
			hand-roll `role="table"` and `role="columnheader"` to say the same. `SupportedSites`
			writes one this way.
		-->
		<table>
			<thead>
				<tr>
					<th scope="col">Field</th>
					<th scope="col">Stash-box</th>
					<!-- Yours first because it is the one that stays if nothing is pressed. -->
					<th scope="col">Yours</th>
					<th scope="col">Theirs</th>
					<!-- The answers column carries no word. Both buttons say what they do, so a
					     heading over them would name the column twice on every row. -->
					<th scope="col"></th>
				</tr>
			</thead>
			<tbody>
				{#each items as one (key(one))}
					<tr>
						<th scope="row">{labelOf(one)}</th>
						<td><Chip size="sm" tone="quiet" inert>{one.box_name}</Chip></td>
						<td class="value">{mineOf(one)}</td>
						<td class="value">{theirsOf(one)}</td>
						<!-- Both answers on the right of the row, where an action sits. Keeping
						     yours is the quiet one because it writes nothing: it is the row's
						     current state said out loud so the row can leave the list. Both are
						     `small`, because the row is the height of a history row and a default
						     button would be most of it. -->
						<td class="acts">
							<div class="both">
								<Button type="button" tone="quiet" size="small" onclick={() => choose(one, false)}>
									Keep yours
								</Button>
								<Button type="button" tone="primary" size="small" onclick={() => choose(one, true)}>
									Take theirs
								</Button>
							</div>
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	{/if}
</section>

<style>
	/*
	 * The block the table and any problem sit in. It lives on the History tab, in the column right
	 * of the history thread, where it has room without pushing anything down, and the mark beside
	 * that tab's word says it is there; in the header's capped column beside the record it would
	 * push the fields and the tab strip off the screen. The column and the fold are
	 * `EntityHistory`'s: this file is a table and does not know how wide it has been given.
	 */
	.pile {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	/*
	 * THE TABLE ITSELF. It takes the column it is given, whatever that is.
	 *
	 * `border-collapse` so the hairline between two rows is ONE line rather than two abutting
	 * edges, which is the difference between a rule and a thick smudge.
	 *
	 * Left in AUTO layout on purpose. Fixed layout would need a width per column, and the widths
	 * are not knowable from here: a field's name, a stash-box's name and two values are different
	 * on every record. Auto gives each column what its content needs and hands the slack to the
	 * ones that can wrap, which is exactly the two value columns: everything else is told not to.
	 */
	table {
		inline-size: 100%;
		border-collapse: collapse;
	}

	/*
	 * One cell's inset, and its vertical half holds the row at the timeline's height: the small
	 * button in the answers cell plus this inset make a row the history thread's own 50px.
	 *
	 * The horizontal half is end padding only, so the first column starts flush with the pane and
	 * the last ends flush with it; an inset on both sides would indent the whole table from the
	 * thread beside it.
	 */
	th,
	td {
		padding-block: var(--space-3);
		padding-inline-end: var(--space-3);
		text-align: start;
		vertical-align: middle;
		/* Everything but a value is a short label or a control, and a column that wraps steals the
		   width the answers need. The two value cells turn this back on below. */
		white-space: nowrap;
	}

	th:last-child,
	td:last-child {
		padding-inline-end: 0;
	}

	/* The words that say which column is which, said ONCE rather than beside every value on every
	 * row, which is where a flex line would have to carry them. */
	thead th {
		font: var(--text-label);
		color: var(--sift-ink-3);
		padding-block: var(--space-1);
	}

	/*
	 * A hairline UNDER the header and BETWEEN rows, and none around the table.
	 *
	 * Nothing above the header, because a rule there would close off the sentence above that says
	 * how many are waiting; nothing below the last row, because that would draw the bottom of a box
	 * this deliberately is not. Same token as every other line in the app.
	 *
	 * Declared on the cells and not on the `<tr>`, because a row is not a box a border can be put
	 * on in every engine: the cells are, and `border-collapse` then makes the run of them one
	 * line rather than five abutting ones.
	 */
	thead tr > * {
		border-block-end: 1px solid var(--sift-line);
	}

	tbody tr + tr > * {
		border-block-start: 1px solid var(--sift-line);
	}

	/* What the field is called. Quiet, because it is the question's subject rather than its answer,
	 * and the two answers beside it are what somebody is reading. */
	tbody th {
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	/*
	 * The two answers, in ONE type size: they are the same kind of fact and the row is a choice
	 * between them, so a difference in weight or size here would be the screen taking a side.
	 *
	 * `normal` and `anywhere` together are what keep the table inside its column: a forty-character
	 * value wraps within its own cell instead of widening the table past the pane. Auto layout
	 * hands these two the slack precisely because they are the only cells that can take it.
	 */
	.value {
		color: var(--sift-ink);
		font: var(--text-body);
		white-space: normal;
		overflow-wrap: anywhere;
	}

	/* The answers column, hard against the end of the row, which is where an action sits. The
	 * cell is right-aligned and the pair inside it keeps its own gap; a flex `<td>` would stop being
	 * a table cell and take its column out of the alignment this table is for. */
	.acts {
		text-align: end;
	}

	.both {
		display: flex;
		align-items: center;
		justify-content: flex-end;
		gap: var(--space-2);
	}
</style>
