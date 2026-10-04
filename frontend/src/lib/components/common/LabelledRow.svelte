<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'LabelledRow',
		category: 'composition',
		role: 'the two-column row a settings pane is made of: the label, then the control',
		basis: 'own',
		states: ['default', 'with help', 'wide control column', 'with a foot line', 'stacked']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: there is nothing to behave. This is the two-column grid a settings pane is
	   made of: a name and its help on the left, whatever the row is about on the right. The
	   controls that go in the right column ARE the library's, and they come in as a snippet. */

	/*
	 * A name, a sentence explaining it, and the thing itself.
	 *
	 * The shared row behind `SettingRow` (a control drawn from a declared schema) and `FactRow` (a
	 * read-only fact on About and Accounts): one left column and one row grid, so a fix to one
	 * reaches both. The label is semibold, so a muted sentence below it never reads as a heading,
	 * and the second column is a fixed width, so every control sits the same distance from the
	 * edge.
	 *
	 * When two copies of a shape disagree, the one carrying the written reasons wins; anything the
	 * other did differently becomes a prop with a reason of its own, or it was a bug.
	 *
	 * `DataRow` looks like this and is a different thing: one item of many in a keyed list whose
	 * population changes at runtime, with machine facts in the tabular font, hover actions and an
	 * expandable subtree. This is one of a fixed few written into the page.
	 */
	import type { Snippet } from 'svelte';
	import MoreAbout from './MoreAbout.svelte';
	import PathCopy from '$lib/settings-ui/PathCopy.svelte';
	import { givePressSize } from './press-size';
	import { paneHeld } from '$lib/settings-ui/read-only';

	interface Props {
		/** What the row is about. Plain text; the caller owns whether it is also a `<label for>`. */
		label?: string;
		/**
		 * An address for this row, so something elsewhere can send somebody straight to it.
		 *
		 * The id goes on the ROW rather than on the control inside it, and that is the point: a
		 * link that lands on the control alone scrolls the name and the sentence explaining it off
		 * the top of the screen, which is everything the reader was sent here to read. It is also
		 * the seam: `$lib/settings-ui/settings-anchor` looks this up by id and rings it, and nothing outside
		 * has to know what a row is made of.
		 */
		id?: string;
		/** The label as markup, for a row that has to bind it to a control with `for`. */
		name?: Snippet;
		/** One sentence saying what this is. The part that is usually missing. */
		help?: string;
		/** The help's `id`, so a control in the right column can point at it. */
		helpId?: string;
		/**
		 * Reference material folded away: licence terms, download sizes, measured figures.
		 *
		 * Read once, and then between a person and the next control forever if it is left inline.
		 */
		disclosure?: string;
		/** The right column: a control, a fact, a badge, a button. */
		children: Snippet;
		/**
		 * Dim the whole row, for a setting hanging off another that is switched off.
		 *
		 * The WHOLE row, not just its control: a full-strength name beside a faded box reads as a
		 * control that has broken rather than one that is waiting for something.
		 */
		inert?: boolean;
		/**
		 * Let the right column size itself instead of taking the pane's control column.
		 *
		 * For a row whose right side is a sentence rather than a control: a version number, a
		 * licence name. A fixed column is what makes a stack of CONTROLS read as a column; a stack of
		 * short facts pinned to the same far edge reads as a table with a gap in the middle.
		 */
		looseColumn?: boolean;
		/**
		 * A wider control column, for a row whose control is two controls side by side (a choice
		 * and the press that goes with it). Still packed to the same right edge as every row.
		 */
		wide?: boolean;
		/**
		 * A line under the help about the row's STATE (when it last ran, what waits), on the left
		 * with the words it qualifies rather than stacked under the control.
		 */
		foot?: Snippet;
		/**
		 * The control column holds a field (a select, a text box) with a press beside it. The press
		 * then takes the field's height, so the two stand level; on every other row a press is the
		 * small control. The row decides, so no press on a pane names a size of its own.
		 */
		besideField?: boolean;
		/**
		 * The control under the name and its help, the row's whole width: a text area something
		 * long is pasted into, which a control column cannot hold.
		 */
		stacked?: boolean;
	}

	let {
		label,
		id,
		name,
		help,
		helpId,
		disclosure,
		children,
		inert = false,
		looseColumn = false,
		wide = false,
		foot,
		besideField = false,
		stacked = false
	}: Props = $props();

	// svelte-ignore state_referenced_locally
	givePressSize(besideField ? 'medium' : 'small');

	/* Whether the pane this row is on is read only here (a pane about the computer Sift runs on, on a
	   phone): the control still says what it is set to, and a disabled fieldset around it is what
	   stops every button, box and switch inside answering, whatever the control is made of. */
	const held = paneHeld();

	/* The row's name as drawn (a caller's `name` is its own markup), for its settings path. */
	let named = $state<HTMLElement | null>(null);
	const said = () => label ?? named?.querySelector('.name')?.textContent?.trim() ?? '';
</script>

<div
	{id}
	class="row ruled-row"
	class:name-alone={!help && !(foot && !wide)}
	class:inert
	class:loose={looseColumn}
	class:wide
	class:beside-field={besideField}
	class:stacked
>
	<div class="named path-host" bind:this={named}>
		<!-- Inside Settings only: elsewhere there is no settings path and it draws nothing. -->
		<PathCopy name={said} />
		{#if name}{@render name()}{:else}<span class="name">{label}</span>{/if}

		{#if help}<p class="help" id={helpId}>{help}</p>{/if}

		{#if foot && !wide}<div class="foot">{@render foot()}</div>{/if}

		{#if disclosure}<MoreAbout text={disclosure} />{/if}
	</div>

	<div class="control">
		{#if held()}
			<fieldset class="held" disabled>{@render children()}</fieldset>
		{:else}
			{@render children()}
		{/if}
	</div>

	<!-- A wide row's control column leaves the name's column narrower than the reading measure,
	     so its foot lines take a line of the row's own under both columns. -->
	{#if foot && wide}<div class="foot spans">{@render foot()}</div>{/if}
</div>

<style>
	/*
	 * The right column is a FIXED width, not `auto`.
	 *
	 * With `auto` each row sizes its own second column, so a row holding a switch and the row under
	 * it holding a menu would agree about nothing: every control at a different distance from the
	 * edge, and the pane a stack of unrelated things. One width, declared once, is what makes a
	 * column a column.
	 */
	.row {
		display: grid;
		grid-template-columns: minmax(0, 1fr) var(--settings-control-col, 15rem);
		align-items: start;
		column-gap: var(--space-6);
		padding: var(--space-4) 0;
	}

	/*
	 * The line between two rows belongs to the LOWER row, on its top edge, and is drawn only when
	 * another row comes right before it: as its sibling, or as the FIRST thing held by an element
	 * that follows a row (each task's block on Tasks). Only the first: a block that opens with a
	 * heading or a sentence of its own has no row right before its first row, and a line there would
	 * sit under the sentence, not between two rows. So the first row under a heading and the last row
	 * of a group draw none, and the group heading's hairline is the only line between two groups.
	 * `ruled-row` is the mark `ActionRow` carries too, so the two kinds of row rule each other.
	 */
	:global(:is(.ruled-row, :has(> .ruled-row))) + .row,
	:global(:is(.ruled-row, :has(> .ruled-row)) + *) > .row:first-child {
		border-block-start: 1px solid var(--sift-line);
	}

	/*
	 * A GROUP OF CHOICES draws no line between its rows: its rows are the positions of one kind of
	 * choice (how a record is drawn, which units), and a hairline between each of them would cut one
	 * decision into pieces. They separate by the rows' own space; the group heading's hairline stays
	 * the only line, between two groups. A group says it is one with `choices` (`SettingGroup`'s
	 * prop, or the class on the block that holds the rows). After the rule above, and as heavy, so
	 * it wins.
	 */
	:global(.choices) > .row,
	:global(.choices) > :global(*) > .row:first-child {
		border-block-start: 0;
	}

	/* Wider, and never more than most of the row, so a narrow window still leaves the name room. */
	.row.wide {
		grid-template-columns: minmax(0, 1fr) var(--settings-control-col-wide, min(26rem, 60%));
	}

	.row.stacked {
		grid-template-columns: minmax(0, 1fr);
		row-gap: var(--space-3);
	}

	.row.loose {
		grid-template-columns: minmax(0, 1fr) auto;
		align-items: baseline;
	}

	/* A name with nothing under it stands level with the control it names. A wide row's foot is
	   a line of the row's own, so it leaves the name alone in its column too. */
	.row.name-alone:not(.stacked) {
		align-items: center;
	}

	.row.inert .named,
	.row.inert .foot.spans {
		opacity: 0.5;
	}

	.named {
		position: relative;
		min-width: 0;
	}

	/*
	 * The contrast between the name and its help IS the hierarchy, and size and colour alone are not
	 * enough to carry it: read down a pane and a muted sentence still looks like a heading. The
	 * name carries WEIGHT as well: semibold full ink against regular muted small. Three differences,
	 * not one, because a person scanning a page reads weight before anything else.
	 *
	 * `:global` as well as the local rule, because a caller passing `name` writes its own element
	 * (a `<label for>` bound to the control beside it) and that element is compiled in the caller's
	 * scope where this stylesheet cannot reach it.
	 */
	.name,
	.named :global(.name) {
		display: block;
		font: var(--text-body);
		font-weight: 600;
		color: var(--sift-ink);
		line-height: 1.3;
	}

	.help {
		margin: var(--space-1) 0 0;
		max-width: var(--reading-measure);
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
		line-height: 1.5;
	}

	.foot {
		margin: var(--space-2) 0 0;
		max-width: var(--reading-measure);
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
		line-height: 1.5;
	}

	/* Every foot line of every row at the one reading measure, whatever the control column took. */
	.foot.spans {
		grid-column: 1 / -1;
	}

	/*
	 * Packed to the end of the column, so every control ends on one right edge whatever its width;
	 * a control is as wide as its content. A loose row, whose right side is a sentence, follows
	 * the same rule.
	 */
	.control {
		/* Where the column packs its controls, published for what a row puts in it: a control made
		   of several parts (a choice and its press, a range and its Edit) lays them out with
		   `justify-content: var(--row-pack, flex-end)` and so follows the row onto a phone, where
		   the column starts where the name starts. A part packed to the end on its own would leave
		   the row stacked and its control still at the far edge. */
		--row-pack: flex-end;
		display: flex;
		align-items: center;
		justify-content: flex-end;
		gap: var(--space-2);
		min-inline-size: 0;
	}

	/* A field beside its press: the box takes what the press leaves, and the press stays on the
	   line. Without this the box fills the column (a box in a column fills it, TextInput says so)
	   and the press wraps under it on the left, the one place a control never ends. The pair
	   stands in the control column directly, and this is the one rule for it. */
	.row.beside-field .control > :global(.text-input) {
		flex: 1 1 0;
	}

	/* A held control column: the same line the column lays its controls on, with none of the
	   fieldset's own frame, so a read-only row stands exactly where the live one stood. */
	.held {
		display: flex;
		align-items: inherit;
		justify-content: inherit;
		gap: inherit;
		min-inline-size: 0;
		margin: 0;
		padding: 0;
		border: 0;
	}

	.row.loose .control {
		justify-content: flex-end;
		align-items: baseline;
		text-align: end;
	}

	/* A phone's width has no room for a fixed control column beside the name: the name would be
	   squeezed to a word a line beside a column mostly empty. There the control goes under the
	   name and its help, the whole width of the row, starting where the name starts. */
	@media (max-width: 767px) {
		.row,
		.row.wide,
		.row.loose {
			grid-template-columns: minmax(0, 1fr);
			row-gap: var(--space-3);
		}

		.control,
		.row.loose .control {
			--row-pack: flex-start;
			justify-content: flex-start;
			text-align: start;
		}
	}
	/* The marker the copy press stands against: PathCopy places itself in the gutter of whatever
	   carries this class, so the class itself is what makes that place. */
	.path-host {
		position: relative;
	}
</style>
