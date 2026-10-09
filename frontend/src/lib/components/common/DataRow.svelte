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

	/** Where a typed key is text, not a command: a field being written in. */
	export const TYPING_FIELD =
		'input:not([type="checkbox"]):not([type="radio"]):not([type="button"]):not([type="submit"]), textarea, select, [contenteditable]:not([contenteditable="false"])';
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: bits-ui has no list or table primitive. This renders an <li>; what is interesting about it
	is the ordering rule its parent holds, which is Sift's own and not a widget. */
	/*
	 * One row, inside DataRows, which holds the order still while a pointer or the keyboard is in
	 * it.
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
		 * The row's subject; with no `cells` in a declared list, a group heading running across.
		 */
		children?: Snippet;
		/** The cells by column `id`, only in a list that declared columns; refused otherwise. */
		cells?: Record<string, Snippet>;
		/** A cell running on to a later column, by that column's `id`. */
		spans?: Record<string, string>;
		/** How many steps in the first cell stands, for a row under the row holding it. */
		indent?: number;
		/**
		 * Whether what this row opens is showing; with `ontoggle` it draws the arrow at the end.
		 */
		expanded?: boolean;
		/** Open or fold the row. With `expanded`. */
		ontoggle?: () => void;
		/** What the arrow opens, for its name: "the steps of clip.mp4". Read as "Show more: ...". */
		toggleLabel?: string;
		/** Sizes, counts, a status badge, in the data face so they do not jitter. */
		trailing?: Snippet;
		/** The row's one number in an end column with a floor width, so figures line up. */
		figure?: string;
		/** Hover buttons, always shown on a touch screen. */
		actions?: Snippet;
		/** What is under the row when opened, inside the same list item. */
		expansion?: Snippet;
		/**
		 * The row's verbs as data: a right-click menu and a three-dot button from one declaration.
		 */
		verbs?: readonly Verb[];
		ids?: string[];
		/** Whose row it is, for the menu and the button's accessible name. "More for Photos". */
		menuLabel?: string;
		/**
		 * A plain press anywhere on the row, also Enter or Space; a control inside keeps its own.
		 */
		onpress?: () => void;
		/**
		 * The row's own keys, heard wherever focus is inside it, but not text typed into a field.
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

	/* Each cell's column and span; a spanned column draws nothing. */
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

	/* A row with actions or a fold needs its track, refused rather than squeezed. */
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

<!-- The arrow, at the row's end: the way the row will move. -->
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
		<!-- The trigger takes no box of its own. -->
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
	/* `contents`, so the row stays the list item's child for layout. */
	:global(.row-trigger) {
		display: contents;
	}

	/* A pressable row shows the pointer and its focus. */
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
		/* A floor, not a height, so five actions still fit. */
		min-block-size: 44px;
		flex-wrap: wrap;
		padding-block: var(--space-1);
		padding-inline: var(--space-3);
		border-radius: var(--radius-sm);
		/* No stripes. What the state layers mix into: the page's ground or the selection's tint. */
		--row-ground: transparent;
		transition: background-color var(--dur-instant) var(--ease);
	}

	.row.selected {
		--row-ground: var(--selected-row);
		--row-solid: var(--selected-row);
		background-color: var(--row-ground);
		color: var(--selected-row-ink);
	}

	/* The line between two rows is the lower one's top edge, never at the list's ends or under a
	 * heading; global, as the earlier line is another instance. */
	:global(.line) + .line {
		border-block-start: 1px solid var(--sift-line);
	}

	:global(.line:has(> .row.across, > .row-trigger > .row.across)) + .line {
		border-block-start: 0;
	}

	/* None above a heading's line either. */
	:global(.line) + .line:has(> .row.across, > .row-trigger > .row.across) {
		border-block-start: 0;
	}

	/* Compact redefines --control-height, so every control in the row shrinks too. */
	.row.compact {
		min-block-size: 36px;
		--control-height: var(--control-height-sm);
	}

	/* State layers on the row's ground; only a pressable row has a pressed state. */
	.row:hover {
		background-color: color-mix(in srgb, currentColor var(--layer-hover), var(--row-ground));
	}

	.row.pressable:active {
		background-color: color-mix(in srgb, currentColor var(--layer-pressed), var(--row-ground));
	}

	/* In a declared list, each part is a subgrid of the list's tracks. */
	.line.columned,
	.row.columned,
	.expansion {
		display: grid;
		grid-column: 1 / -1;
		grid-template-columns: subgrid;
		align-items: center;
	}

	/* An expansion spans the row unless placed. */
	.expansion > :global(*) {
		grid-column: 1 / -1;
	}

	/* The list's column gap, so cells stay on the tracks. */
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

	/* Numbers against the column's end, in the data face. */
	.cell.end {
		justify-self: end;
		text-align: end;
		font-variant-numeric: tabular-nums;
	}

	/* The actions overlay the row's end on its own ground rather than taking a track, so the last
	 * column runs to the edge; only the arrow keeps a track (`--data-row-base` names the ground).
	 * */
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
		/* As wide as what it holds, so a label does not wrap word by word. */
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

	/* The ground fades in, so a figure under it fades rather than being cut. */
	.cell.do .actions::before {
		content: '';
		position: absolute;
		inset-block: 0;
		inset-inline-end: 100%;
		inline-size: var(--space-12);
		background: linear-gradient(to left, var(--actions-ground), transparent);
		pointer-events: none;
	}

	/* No ground with nothing to offer; not `:empty`, which whitespace defeats. */
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

	/* The arrow, never faded: it says which way the row is. */
	.disclose {
		display: inline-flex;
		flex: none;
		order: 3;
		color: var(--sift-ink-3);
	}

	/* A floor under the name, so hover actions wrap the row rather than squeezing it away. */
	.subject {
		flex: 1 1 16rem;
		min-inline-size: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	/* Half that floor on a phone, so the arrow stays beside the name. */
	@media (max-width: 767px) {
		.subject {
			flex-basis: 8rem;
		}

		/* Room under a row's link for its finger ring, which the next row would cover. */
		.row.columned:has(:global(.btn.link)) {
			padding-block-end: var(--space-3);
		}
	}

	/* Pinned to the row's end by `order`, so rows with fewer actions stay in line. */
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

	/* A row with a field focused hides its hover actions. */
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
