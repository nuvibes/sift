<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'DataRow',
		category: 'composition',
		role: 'one row of a list, whose cells obey the columns the list declared',
		basis: 'site:<li>',
		states: ['default', 'selected', 'in declared columns', 'folded', 'opened out']
	} satisfies DesignEntry;

	/** What inside a row takes its own press. One rule, shared with `Tree`. */
	export const INSIDE_CONTROL = 'button, a, input, select, textarea, label, [role="menu"]';

	/**
	 * Where a typed key is TEXT rather than a command: a field somebody is writing in. A checkbox,
	 * a radio and a button are controls that take a press, not a letter, so a key typed on one of
	 * them is still the row's to hear.
	 */
	export const TYPING_FIELD =
		'input:not([type="checkbox"]):not([type="radio"]):not([type="button"]):not([type="submit"]), textarea, select, [contenteditable]:not([contenteditable="false"])';
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: bits-ui has no list or table primitive. This renders an <li>; what is interesting about it
	   is the ordering rule its parent holds, which is Sift's own and not a widget. */
	/* One row. Put it inside DataRows: it is not meant to be used on its own.
	 *
	 * This renders an <li>, so a row outside a list is malformed to begin with. The reason that
	 * matters is the other thing DataRows carries: it holds the order still while a pointer or the
	 * keyboard is in the list. Build a list out of these with a plain {#each} and everything looks
	 * right, the rows are the rows, and the sort quietly moves under the cursor again, which is how
	 * someone reaches for Cancel on a live queue and stops the wrong job.
	 */
	import { getContext, type Snippet } from 'svelte';
	import Button from '$lib/components/common/Button.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { COLUMNS, type Declared } from '$lib/components/common/DataRows.svelte';
	import ContextMenu from '$lib/components/common/ContextMenu.svelte';
	import RowMenu from '$lib/components/common/RowMenu.svelte';
	import VerbMenuItems from '$lib/components/common/VerbMenuItems.svelte';
	import type { Verb } from '$lib/components/common/verbs';

	interface Props {
		/** 36px instead of 44. Used where a list is long enough that its height is what you notice. */
		compact?: boolean;
		/** One of the rows a selection holds: the accent's tinted ground under the full ink. */
		selected?: boolean;
		/**
		 * The row's subject: a name, a title, a path. Left, in the UI face.
		 *
		 * In a list that declared columns, a row with this and no `cells` runs ACROSS every column:
		 * that is a group heading ("Passes", "Housekeeping"), drawn without the hover and the rule
		 * a row of data wears.
		 */
		children?: Snippet;
		/**
		 * The row's cells, by the `id` of the column each belongs in, only in a list that declared
		 * columns, which is where each one is placed and aligned. The row never positions a cell
		 * itself. Given in a list with no declaration it is REFUSED: there are no
		 * tracks to put the cells in, and drawing them one after another would be a row laying out
		 * its own cells, the fault the declaration exists to end.
		 */
		cells?: Record<string, Snippet>;
		/**
		 * A cell that runs on across the columns after its own, by the `id` of the last one it
		 * covers: `{ bar: 'count' }` puts the `bar` cell over the bar and count columns.
		 */
		spans?: Record<string, string>;
		/** How many steps in from the list's edge the first cell stands: a row under the row that
		 *  holds it (a product under its pass, a step under its file). */
		indent?: number;
		/**
		 * Whether what this row opens out to is showing. Given with `ontoggle`, the row draws its
		 * arrow, in one place for every list that folds: the end of the row, after everything else,
		 * and always visible, because it says which way the row is, and a state nobody can see at
		 * rest is no state.
		 */
		expanded?: boolean;
		/** Open or fold the row. With `expanded`. */
		ontoggle?: () => void;
		/** What the arrow opens, for its name: "the steps of clip.mp4". Read as "Show more: ...". */
		toggleLabel?: string;
		/**
		 * Sizes, counts, durations, a status badge. Right, and machine facts belong in `--text-data`
		 * so a column of them does not jitter sideways every time a value ticks.
		 */
		trailing?: Snippet;
		/**
		 * The row's one machine NUMBER (a size, a count) in a column of its own at the end.
		 *
		 * Not simply more of `trailing`: a figure's width changes row by row ("84.2 MB", "--"), and
		 * in a flex row everything before it would slide sideways to make room, leaving a column of
		 * status badges a ragged edge. This cell has a floor width and its text is right-aligned, so every badge
		 * before it stands in one column and the figures line up on their last digit.
		 */
		figure?: string;
		/**
		 * Retry, cancel. Ghost icon buttons that appear on hover, and stay put on a touch screen,
		 * where there is no hover to reveal them with and an invisible control is no control.
		 */
		actions?: Snippet;
		/**
		 * What is UNDER this row when it is opened: a folder's subtree, and nothing else so far.
		 *
		 * A block rather than another cell, drawn inside the same list item so it belongs to the row
		 * that opened it, for a screen reader as much as for the eye. A caller that draws nothing
		 * here costs an empty element and no layout at all.
		 */
		expansion?: Snippet;
		/**
		 * The row's own verbs, declared as data.
		 *
		 * Given these, the row answers a right-click with Sift's menu and grows a three-dot button at
		 * the end offering the same list, which is the menu rule in full, from one declaration, for
		 * every list built out of these. The visible hover buttons in `actions` are unaffected and
		 * stay: the two are in addition to each other, the buttons being the discoverable path for
		 * the one or two common verbs and the menu carrying the rest.
		 *
		 * A row with no verbs is drawn with no wrapper and no button.
		 */
		verbs?: readonly Verb[];
		/** What the verbs act on. This row's id. */
		ids?: string[];
		/** Whose row it is, for the menu and the button's accessible name. "More for Photos". */
		menuLabel?: string;
		/**
		 * What a plain press anywhere on the row does. Given, the row is pressable; absent, it is
		 * inert.
		 *
		 * A press on a control inside the row (a switch, a button, a link) is that control's, and
		 * is left alone. The keyboard reaches the same act with Enter or Space, and the row is a
		 * tab stop only where this is given, so a list that declares nothing gains no stops.
		 *
		 * A press opens the row rather than its verb menu, because that is the gesture everybody
		 * tries first; the menu keeps its own two doors (the right-click and the trigger).
		 */
		onpress?: () => void;
		/**
		 * The row's own keys (Delete, a letter) heard WHEREVER the keyboard sits inside the row.
		 *
		 * The row is the tab stop, but the keyboard is as often on something inside it: the tick,
		 * a glyph button, a link. A handler a caller put on one of its own descendants would hear
		 * only the keys typed on THAT descendant, so a download row's Delete and P would work with
		 * the tick focused and do nothing with the row itself focused: the stop Tab lands on first. The
		 * key bubbles to the row from every one of them, so the row is the one place to listen.
		 *
		 * Handed every key before the row's own Enter and Space; call `preventDefault` on the ones
		 * it takes and the row leaves them alone. Not handed a key typed into a text field inside
		 * the row (`TYPING_FIELD`): a letter written into a box is text, not a command. Given this,
		 * the row is a tab stop even with no `onpress`, since keys nobody can reach are no keys.
		 */
		onkeys?: (event: KeyboardEvent) => void;
	}

	let {
		compact = false,
		selected = false,
		children,
		cells,
		spans,
		indent = 0,
		expanded = false,
		ontoggle,
		toggleLabel = 'this row',
		trailing,
		figure,
		actions,
		expansion,
		verbs,
		ids = [],
		menuLabel = 'Actions',
		onpress,
		onkeys
	}: Props = $props();

	const offered = $derived(verbs !== undefined && verbs.length > 0);

	/* The columns of the list this row is in, if it declared any. */
	const declared = getContext<Declared | undefined>(COLUMNS);
	// svelte-ignore state_referenced_locally
	if (cells && !declared) {
		throw new Error('DataRow: cells were given in a list that declared no columns');
	}

	/* A declared list's row that runs across every column: a group heading. */
	const across = $derived(declared !== undefined && cells === undefined);

	/* Which column each cell stands in and how far it runs. A column a span covers draws nothing,
	   so the cell that spans it is not pushed onto a second line. */
	const placed = $derived.by(() => {
		if (!declared || !cells) return [];
		const ids = declared.columns.map((column) => column.id);
		const out: { id: string; end: boolean; run: number; first: boolean }[] = [];
		for (let at = 0; at < declared.columns.length;) {
			const column = declared.columns[at];
			const last = spans?.[column.id];
			const through = last === undefined ? at : ids.indexOf(last);
			const run = through > at ? through - at + 1 : 1;
			out.push({ id: column.id, end: column.align === 'end', run, first: at === 0 });
			at += run;
		}
		return out;
	});

	/* A row of a declared list with something to do must have a track to do it in where there is
	   no hover, and a row that folds needs the arrow's track. Refused rather than squeezed into the
	   last data column, which would move that column on this row alone. */
	// svelte-ignore state_referenced_locally
	if (declared && cells && declared.actions === undefined && (actions || offered)) {
		throw new Error('DataRow: this row has actions but its list declared no actions track');
	}
	// svelte-ignore state_referenced_locally
	if (declared && cells && ontoggle && !declared.folds) {
		throw new Error('DataRow: this row folds but its list declared no track for the arrow');
	}

	let menuOpen = $state(false);

	/** Whether the press was aimed at something inside the row that takes its own. */
	function inside(event: Event): boolean {
		return (event.target as Element | null)?.closest(INSIDE_CONTROL) !== null;
	}

	/** Act on a press that was not aimed at something inside the row. */
	function pressed(event: MouseEvent) {
		if (!onpress || inside(event)) return;
		onpress();
	}

	/** The row's own keys first, then the same act as a press: the two keys anything pressable answers. */
	function typed(event: KeyboardEvent) {
		if (onkeys && !(event.target as Element | null)?.closest(TYPING_FIELD)) {
			onkeys(event);
			if (event.defaultPrevented) return;
		}
		if (!onpress || inside(event)) return;
		if (event.key !== 'Enter' && event.key !== ' ') return;
		// Space scrolls the page otherwise, and what the row just opened would leave the screen.
		event.preventDefault();
		onpress();
	}
</script>

{#snippet line()}
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
	<div
		class="row"
		class:columned={declared !== undefined}
		class:across
		class:compact
		class:selected
		class:pressable={onpress !== undefined}
		class:keyed={onkeys !== undefined}
		tabindex={onpress || onkeys ? 0 : undefined}
		onclick={pressed}
		onkeydown={typed}
	>
		{#if across}
			<div class="cell across">{@render children?.()}</div>
		{:else if declared}
			{#each placed as cell (cell.id)}
				<div
					class="cell"
					class:end={cell.end}
					style:grid-column-end={cell.run > 1 ? `span ${cell.run}` : undefined}
					style:--row-indent={cell.first && indent > 0 ? indent : undefined}
					class:indented={cell.first && indent > 0}
				>
					{@render cells?.[cell.id]?.()}
				</div>
			{/each}
			{#if declared.actions !== undefined || declared.folds}
				<div class="cell do" class:folds={declared.folds}>
					{#if actions || offered}
						<div class="actions">
							{@render actions?.()}
							{#if offered}
								<RowMenu verbs={verbs ?? []} {ids} label={menuLabel} bind:open={menuOpen} />
							{/if}
						</div>
					{/if}
					{@render arrow()}
				</div>
			{/if}
		{:else}
			<div
				class="subject"
				class:indented={indent > 0}
				style:--row-indent={indent > 0 ? indent : undefined}
			>
				{@render children?.()}
			</div>

			{#if trailing || figure !== undefined}
				<div class="trailing data">
					{@render trailing?.()}
					{#if figure !== undefined}<span class="figure">{figure}</span>{/if}
				</div>
			{/if}

			{#if actions || offered}
				<div class="actions">
					{@render actions?.()}
					{#if offered}
						<RowMenu verbs={verbs ?? []} {ids} label={menuLabel} bind:open={menuOpen} />
					{/if}
				</div>
			{/if}

			{@render arrow()}
		{/if}
	</div>
{/snippet}

<!-- THE ARROW, in its one place: the end of the row. `expand_more` folded, `expand_less` open:
     the direction the row will move when it is pressed. A button, so the row's own press leaves
     it alone (`INSIDE_CONTROL`). -->
{#snippet arrow()}
	{#if ontoggle}
		<span class="disclose">
			<Tooltip label={expanded ? 'Show less' : 'Show more'}>
				<Button
					tone="ghost"
					size="small"
					icon={expanded ? 'expand_less' : 'expand_more'}
					aria-label="{expanded ? 'Show less' : 'Show more'}: {toggleLabel}"
					aria-expanded={expanded}
					onclick={() => ontoggle?.()}
				/>
			</Tooltip>
		</span>
	{/if}
{/snippet}

<li class="line" class:columned={declared !== undefined}>
	{#if offered}
		<!-- The trigger takes no box of its own: the row underneath is what somebody sees and what
		     the list lays out, and a wrapper with a size would put a second block between the two. -->
		<ContextMenu label={menuLabel} triggerClass="row-trigger">
			{@render line()}
			{#snippet items()}
				<VerbMenuItems verbs={verbs ?? []} {ids} />
			{/snippet}
		</ContextMenu>
	{:else}
		{@render line()}
	{/if}

	{#if declared !== undefined && expansion}
		<!-- On the row's own tracks, so a list opened out under it lines up with it. -->
		<div class="expansion">{@render expansion()}</div>
	{:else}
		{@render expansion?.()}
	{/if}
</li>

<style>
	/* See the comment on the trigger. `contents` rather than `display: block`, so the row it wraps is
	   still the list item's own child for layout: without it the row would gain a block ancestor and
	   any list arranging its rows (a grid, a flex column with a gap) would arrange the wrappers instead. */
	:global(.row-trigger) {
		display: contents;
	}

	/* A row a press opens says so with the pointer, the way a link does, and, because it is a tab
	   stop, says where the keyboard is as well. A focusable thing with no visible focus is a
	   keyboard user lost in a list. */
	.row.pressable {
		cursor: pointer;
	}

	.row.pressable:focus-visible,
	.row.keyed:focus-visible {
		outline: none;
		box-shadow: var(--focus-ring);
	}

	.row {
		display: flex;
		align-items: center;
		gap: var(--space-3);
		/* A minimum rather than a fixed height. A row with one or two actions is 44px; a row
		   carrying five of them, which the library folders do, would
		   otherwise push the last ones past the edge of the pane, where they are unreachable and
		   read as missing. */
		min-block-size: 44px;
		flex-wrap: wrap;
		padding-block: var(--space-1);
		padding-inline: var(--space-3);
		border-radius: var(--radius-sm);
		/* No stripes. A hairline where rows genuinely need separating (see the rule below), and
		   hover to say which one is under the pointer: stripes say nothing and never stop saying it. */
		/* What the state layers mix into: the page's own ground, or the selection's tint. */
		--row-ground: transparent;
		transition: background-color var(--dur-instant) var(--ease);
	}

	.row.selected {
		--row-ground: var(--selected-row);
		--row-solid: var(--selected-row);
		background-color: var(--row-ground);
		color: var(--selected-row-ink);
	}

	/*
	 * THE LINE IS DRAWN BETWEEN TWO ROWS, by the lower one on its top edge, and nowhere else. The
	 * first row of a list and the last draw none, so whatever holds the list (a heading's hairline, a
	 * panel's edge) is the only line around it and two lines never meet with nothing between them.
	 * A row directly under a group heading draws none either: the heading runs across the list and
	 * its line is the one above it.
	 *
	 * On the line (the list item) rather than the row, because the row may sit inside its menu's
	 * trigger. The earlier line is another instance of this component, which the compiler cannot
	 * see, so it is written global; the line that draws is this file's own.
	 */
	:global(.line) + .line {
		border-block-start: 1px solid var(--sift-line);
	}

	:global(.line:has(> .row.across, > .row-trigger > .row.across)) + .line {
		border-block-start: 0;
	}

	/* And the line that HOLDS a group heading draws none above itself either: the heading's own
	   hairline is the one line between the groups, so a row line under it would double it. */
	:global(.line) + .line:has(> .row.across, > .row-trigger > .row.across) {
		border-block-start: 0;
	}

	/*
	 * Compact has to reach the controls, or on most rows it does nothing at all.
	 *
	 * 44px is the control height plus this row's padding, so a row carrying a button sits exactly
	 * at its floor; lowering only the floor to 36 leaves every row with a button the same height.
	 * So the token is redefined rather than reached into: `--control-height` sizes every button,
	 * box and select, and setting it here makes the whole row compact without this file knowing
	 * what is in it. A compact row carrying a control is 40 (the small control plus the same
	 * padding); one carrying only text is its 36px floor.
	 */
	.row.compact {
		min-block-size: 36px;
		--control-height: var(--control-height-sm);
	}

	/* The state layers (see `--layer-hover`) on whatever ground the row has, selected or not, so a
	   row under the pointer and a selected one never look alike. Only a row a press opens is
	   pressed: a row of facts has nothing to press. */
	.row:hover {
		background-color: color-mix(in srgb, currentColor var(--layer-hover), var(--row-ground));
	}

	.row.pressable:active {
		background-color: color-mix(in srgb, currentColor var(--layer-pressed), var(--row-ground));
	}

	/*
	 * IN A LIST THAT DECLARED COLUMNS: the item, the row and what it opens out to are each a
	 * SUBGRID of the list's tracks, so a cell stands in its column however deep it is drawn. The
	 * flex arrangement below is the row of a list that declared none, unchanged.
	 */
	.line.columned,
	.row.columned,
	.expansion {
		display: grid;
		grid-column: 1 / -1;
		grid-template-columns: subgrid;
		align-items: center;
	}

	/* What a row puts under itself spans the row by default: left to the grid, it lands in the
	 * first column, which on a list with a tick-box is twenty pixels wide. A caller that wants
	 * its detail to start at a later column places it itself. */
	.expansion > :global(*) {
		grid-column: 1 / -1;
	}

	/* `normal`, so the row takes the list's column gap: a gap of its own would move its cells off
	   the tracks every other row stands on. */
	.row.columned {
		column-gap: normal;
	}

	/* A group heading runs across the row, and is not a row of data to hover or rule off. */
	.row.across {
		min-block-size: 0;
		padding-block: var(--space-2) 0;
	}

	.row.across:hover {
		background: none;
	}

	.cell {
		min-inline-size: 0;
	}

	.cell.across {
		grid-column: 1 / -1;
	}

	/* Numbers, sizes, durations and counts: against the column's end, in the data face, so a
	   column of them lines up on its last digit. */
	.cell.end {
		justify-self: end;
		text-align: end;
		font-variant-numeric: tabular-nums;
	}

	/*
	 * THE ACTIONS ARE LAID OVER THE END OF THE ROW, not given a track of their own: a track for
	 * buttons seen only on hover is an empty column at rest, and every list carrying one would stop
	 * its last column short of the pane's edge. So the list's last column runs to its end, and the
	 * hovered or focused row draws its buttons over its own last cells, on the row's own ground
	 * so nothing under them shows through. Only the arrow, seen at rest, keeps a track.
	 *
	 * Without a fold track the cell takes no box and the buttons stand at the row's end; with one
	 * the cell is the arrow's track and the buttons stand just before it. A list standing on a
	 * surface other than the page's names that ground as `--data-row-base`.
	 */
	.row.columned {
		position: relative;
	}

	.cell.do {
		display: contents;
	}

	.cell.do.folds {
		display: flex;
		position: relative;
		align-self: stretch;
		align-items: center;
		justify-content: flex-end;
	}

	.cell.do .actions {
		position: absolute;
		inset-block: 0;
		inset-inline-end: 0;
		/* As wide as what it holds. Placed against the arrow's narrow track, an overlay sized by its
		   container would wrap "Run in Tasks" one word to a line. */
		inline-size: max-content;
		flex-wrap: nowrap;
		padding-inline: var(--space-2) var(--space-3);
		border-radius: var(--radius-sm);
		--actions-ground: color-mix(
			in srgb,
			currentColor var(--layer-hover),
			var(--row-solid, var(--data-row-base, var(--sift-bg)))
		);
		background-color: var(--actions-ground);
		pointer-events: none;
	}

	/* The ground fades in over what it covers, so a figure under the buttons fades out rather than
	   being cut through the middle of a digit. */
	.cell.do .actions::before {
		content: '';
		position: absolute;
		inset-block: 0;
		inset-inline-end: 100%;
		inline-size: var(--space-12);
		background: linear-gradient(to left, var(--actions-ground), transparent);
		pointer-events: none;
	}

	/* A row with nothing to offer draws no ground, or the fade would dim its own last words. Not
	   `:empty`: the snippet leaves whitespace behind, which `:empty` counts. */
	.cell.do .actions:not(:has(*)) {
		display: none;
	}

	.cell.do.folds .actions {
		inset-inline-end: calc(100% + var(--space-1));
		padding-inline-end: 0;
	}

	.row:hover .cell.do .actions,
	.row:focus-within .cell.do .actions {
		pointer-events: auto;
	}

	/* No hover: the list gives the actions a track, and they stand in it at rest before the arrow. */
	@media (hover: none) {
		.cell.do {
			display: flex;
			align-items: center;
			justify-content: flex-end;
			gap: var(--space-1);
		}

		.cell.do .actions,
		.cell.do.folds .actions {
			position: static;
			padding-inline: 0;
			background: none;
			pointer-events: auto;
		}

		.cell.do .actions::before {
			display: none;
		}
	}

	/* A row under the one that holds it. One step is the width of a row's own glyph. */
	.indented {
		padding-inline-start: calc(var(--row-indent) * var(--space-5));
	}

	/* The arrow. After everything, and never faded with the hover actions: it says which way the
	   row is. */
	.disclose {
		display: inline-flex;
		flex: none;
		order: 3;
		color: var(--sift-ink-3);
	}

	/* A floor under the name, not just a share of what is left.
	 *
	 * `flex: 1` with no basis lets this shrink to nothing: the library rows carry six action
	 * buttons and a switch, so on hover the name would be squeezed to its first few letters, the
	 * one part of the row that says which folder it is. With a basis the row wraps instead, which is
	 * what `flex-wrap` on it was already for. */
	.subject {
		flex: 1 1 16rem;
		min-inline-size: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	/* On a phone the floor is half of that. A whole phone line is about 23rem, so a 16rem floor plus
	   a finger's menu, a mark and the arrow is more than the line: the arrow would drop to a line of
	   its own under the name, at the row's start, with nothing beside it, on every folder of the
	   library. Half still keeps the name readable (the reason the floor exists); a row with more
	   than the line holds still wraps. */
	@media (max-width: 767px) {
		.subject {
			flex-basis: 8rem;
		}

		/* A link in a row's words (Activity's "12 hours ago, failed") reaches a finger through its
		   ring, and a columned row is positioned, so the row after it is drawn over whatever of the
		   ring leaves this one: a press 9px under the link would go to the next row. The row keeps room
		   under its last line for the ring instead. */
		.row.columned:has(:global(.btn.link)) {
			padding-block-end: var(--space-3);
		}
	}

	/*
	 * Pinned to the end of the row, with the actions in front of it.
	 *
	 * `order` rather than a different arrangement of the markup: the subject reads first, then what
	 * this row is (a size, a state, a switch), with the things you can do between them.
	 *
	 * Pinned because the actions are invisible until hover but still take room, and rows have
	 * different numbers of them; in source order a row with one fewer action (a folder that is
	 * already the download default) would put its switch a button's width out of line with every
	 * other.
	 */
	.trailing {
		display: flex;
		align-items: center;
		gap: var(--space-3);
		flex: none;
		order: 2;
		margin-inline-start: auto;
		color: var(--sift-ink-2);
		font-variant-numeric: tabular-nums;
	}

	/* The floor is what makes it a column: "--" takes the same room as "84.2 MB". */
	.figure {
		min-inline-size: var(--data-figure);
		text-align: end;
	}

	.actions {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: var(--space-1);
		flex: none;
		order: 1;
		margin-inline-start: auto;
		opacity: 0;
		transition: opacity var(--dur-instant) var(--ease);
	}

	.row:hover .actions,
	.row:focus-within .actions {
		opacity: 1;
	}

	/* A row with a field open in it is being answered, not hovered: its hover actions stay away
	   while the field (or the form around it) has the focus, rather than fading in over the right
	   of what is being typed. */
	@media (hover: hover) {
		.row:has(.cell:not(.do) :global(:is(form, input, textarea):focus-within)) .actions {
			opacity: 0;
			pointer-events: none;
		}
	}

	/* No pointer to hover with. The actions are simply here. */
	@media (hover: none) {
		.actions {
			opacity: 1;
		}
	}
</style>
