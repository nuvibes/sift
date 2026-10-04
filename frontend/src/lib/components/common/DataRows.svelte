<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'DataRows',
		category: 'composition',
		role: 'a list of rows with its columns declared once, so every row lines up',
		basis: 'own',
		states: ['default', 'empty', 'columns declared']
	} satisfies DesignEntry;

	/**
	 * ONE COLUMN OF A LIST, declared once on the list and obeyed by every row.
	 *
	 * A row does not position its cells: it hands them over by `id` and the list puts each one in
	 * its track, aligned the way the column says. A row that has no cell for a column leaves the
	 * column empty rather than letting its neighbours slide into it.
	 */
	export interface Column {
		/** Which of a row's `cells` goes in this column. */
		id: string;
		/** The heading over the column, on the list's one heading row. Absent, the column has none. */
		label?: string;
		/**
		 * The track: a length (`8rem`) or a share of what is left (`minmax(0, 2fr)`).
		 *
		 * NEVER A CONTENT SIZE: `auto`, `max-content`, `min-content`, `fit-content` are refused. A
		 * track sized by what is in it is as wide as whichever rows happen to be showing, so two lists
		 * declared alike stop lining up and the same list moves when a page turns: two groups on one
		 * screen, each sizing its own, would stand the same column at two places.
		 */
		width: string;
		/** Words `start` (the default); numbers, sizes, durations and counts `end`. */
		align?: 'start' | 'end';
	}

	/** What a row reads from the list it is in. Absent: the list declared no columns. */
	export interface Declared {
		columns: readonly Column[];
		/**
		 * How wide a row's actions are where there is no hover to reveal them: the track they
		 * stand in on a touch screen, always last. With a pointer they take no track at all and
		 * are laid over the end of the row they belong to while it is hovered or focused.
		 */
		actions: string | undefined;
		/** Whether any row can fold: the arrow's own track at the end, the one drawn at rest. */
		folds?: boolean;
	}

	/** The arrow's track: one small glyph button. */
	export const FOLD_TRACK = 'var(--control-height-sm)';

	/** The context key the declaration travels under, from the list to its rows. */
	export const COLUMNS = Symbol('data-rows-columns');

	/** A track that sizes itself by its content, which a declared column may not be. */
	const CONTENT_SIZED = /\b(auto|max-content|min-content|fit-content)\b/;

	/**
	 * The declaration, checked: a content-sized track is REFUSED rather than drawn, because the
	 * list would look right on the rows in front of whoever wrote it and wrong on the next page.
	 */
	export function checkColumns(columns: readonly Column[], actions: string | undefined): void {
		const ids = new Set<string>();
		for (const column of columns) {
			if (ids.has(column.id))
				throw new Error(`DataRows: the column "${column.id}" is declared twice`);
			ids.add(column.id);
			if (CONTENT_SIZED.test(column.width)) {
				throw new Error(
					`DataRows: the column "${column.id}" is sized by its content (${column.width}); give it a length or a share`
				);
			}
		}
		if (actions !== undefined && CONTENT_SIZED.test(actions)) {
			throw new Error(`DataRows: the actions track is sized by its content (${actions})`);
		}
	}

	/**
	 * The tracks with a pointer, in order: the columns, then the arrow's track where a row folds.
	 * The actions take none, so the last column ends where the list ends.
	 */
	export function tracksOf(declared: Declared): string {
		const widths = declared.columns.map((column) => column.width);
		if (declared.folds) widths.push(FOLD_TRACK);
		return widths.join(' ');
	}

	/** The tracks on a touch screen: the actions are always showing, so they stand in a track. */
	export function touchTracksOf(declared: Declared): string {
		const widths = declared.columns.map((column) => column.width);
		const tail = declared.actions ?? (declared.folds ? FOLD_TRACK : undefined);
		if (tail !== undefined) widths.push(tail);
		return widths.join(' ');
	}
</script>

<script lang="ts" generics="T">
	/* WHY NOT BITS-UI: bits-ui has no list primitive, and the thing worth having here is not a widget at all: it
	   is the rule that holds a live list still while a pointer or the keyboard is inside it. */
	import { getContext, setContext, type Snippet } from 'svelte';
	import { applyFrozenOrder } from './frozen-order';

	interface Props {
		/** The live list, in whatever order the screen sorts it. Re-sort it freely: that is the point. */
		items: readonly T[];
		/** A stable identity per item. The order is held by these, so they must not be array indices. */
		key: (item: T) => string;
		/** One row per item. Render a DataRow here. */
		row: Snippet<[T]>;
		/** Names the list for a screen reader. */
		label: string;
		/**
		 * The list's columns, declared once. Given, the list is a grid and every row lays its `cells`
		 * into these tracks; absent, every row is a flex row.
		 *
		 * A list inside a row of a declared list (the steps a row opens out to) declares nothing
		 * and INHERITS the tracks of the list it is in, so what a row opens lines up with the row.
		 */
		columns?: readonly Column[];
		/**
		 * How wide a row's hover buttons, its More button and its arrow are together. A track that
		 * wide is drawn only where there is no hover (a touch screen shows them at rest); with a
		 * pointer they are laid over the end of the row, on its own ground, and take no room. Only
		 * with `columns`; a declared list whose rows have actions must give one.
		 */
		actions?: string;
		/** Some row folds: the arrow, seen at rest, keeps a small track of its own at the end. */
		folds?: boolean;
		/**
		 * The list stands on the pane's edges: its words start where every row's name starts and
		 * its last control ends where every control ends, and only the hover ground reaches past
		 * them. For a list on a settings pane; a list that is the whole screen keeps its inset.
		 */
		edges?: boolean;
	}

	let { items, key, row, label, columns, actions, folds = false, edges = false }: Props = $props();

	/* THE DECLARATION, OWN OR INHERITED. Read once when the list is made: a list's columns are a
	   fact about the list, not something that changes while it is on screen. */
	const inherited = getContext<Declared | undefined>(COLUMNS);
	// svelte-ignore state_referenced_locally
	const own: Declared | undefined = columns ? { columns, actions, folds } : undefined;
	if (own) checkColumns(own.columns, own.actions);
	const declared = own ?? inherited;
	/* Nested: laid on the tracks of the row it sits in rather than on tracks of its own. */
	const nested = own === undefined && inherited !== undefined;
	setContext(COLUMNS, declared);

	/* One heading row over the columns, drawn once and only by the list that declared them. */
	const headed = own !== undefined && own.columns.some((column) => column.label);

	// The order as it was when a pointer or the keyboard arrived, or null when nobody is here.
	let frozenKeys = $state<string[] | null>(null);

	const shown = $derived(applyFrozenOrder(items, frozenKeys, key));

	// Snapshotted from what is currently on screen, not from `items`: while a freeze is already in
	// force those two differ, and re-reading the live order here would apply the reorder that the
	// freeze exists to prevent.
	function hold() {
		if (frozenKeys === null) frozenKeys = shown.map(key);
	}

	// A press inside the list is the person's own act, and what it adds lands where it belongs: a
	// row opened out by its arrow puts its rows under it, not after the last row of the list, which
	// is where a held order puts anything it has not seen. The hold is let go for the press and
	// taken again on the next frame, once what the press changed is on screen, so the list is still
	// held against changes nobody made while the pointer stays.
	function releaseForPress(event: MouseEvent) {
		if (frozenKeys === null) return;
		frozenKeys = null;
		const list = event.currentTarget as HTMLElement;
		requestAnimationFrame(() => {
			if (list.matches(':hover') || list.contains(document.activeElement)) hold();
		});
	}

	// The pointer left the list, or focus did. Whatever arrived while it was held lands now.
	//
	// `focusout` fires when focus moves between two rows as well, so it checks where focus went: a
	// list that unfroze on every tab step would reorder under the keyboard, which is the same bug
	// with a different input device.
	function releaseOnBlur(event: FocusEvent) {
		const next = event.relatedTarget;
		const list = event.currentTarget as HTMLElement;
		if (next instanceof Node && list.contains(next)) return;
		frozenKeys = null;
	}
</script>

<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
<ul
	class="rows"
	class:columned={declared !== undefined}
	class:nested
	class:edges
	style:--data-tracks={own ? tracksOf(own) : undefined}
	style:--data-tracks-touch={own ? touchTracksOf(own) : undefined}
	aria-label={label}
	onpointerenter={hold}
	onpointerleave={() => (frozenKeys = null)}
	onfocusin={hold}
	onfocusout={releaseOnBlur}
	onclickcapture={releaseForPress}
>
	{#if headed && own}
		<li class="heads">
			{#each own.columns as column (column.id)}
				<span class="head" class:end={column.align === 'end'}>{column.label ?? ''}</span>
			{/each}
			{#if own.actions !== undefined || own.folds}<span class="head tail" class:rest={own.folds}
				></span>{/if}
		</li>
	{/if}
	{#each shown as item (key(item))}
		{@render row(item)}
	{/each}
</ul>

<style>
	.rows {
		list-style: none;
		margin: 0;
		padding: 0;
	}

	/* The tracks belong to the list, so every row lines up; each row is a subgrid of them. */
	.rows.columned {
		display: grid;
		grid-template-columns: var(--data-tracks);
		column-gap: var(--space-4);
	}

	/* The heading over the actions exists only where they have a track: with no hover. */
	.head.tail:not(.rest) {
		display: none;
	}

	@media (hover: none) {
		.rows.columned {
			grid-template-columns: var(--data-tracks-touch);
		}

		.head.tail {
			display: block;
		}
	}

	/* A list a row opened out: the row's own tracks, not a second set of its own. */
	.rows.nested {
		grid-column: 1 / -1;
		grid-template-columns: subgrid;
	}

	.heads {
		display: grid;
		grid-column: 1 / -1;
		grid-template-columns: subgrid;
		padding-inline: var(--space-3);
		padding-block-end: var(--space-1);
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	.head.end {
		text-align: end;
	}

	/*
	 * On the pane's edges. A row pads its words in from its own hover ground, so the list is pulled
	 * out by what the pane's scroller leaves before it clips a ground or a ring (`--space-2` at the
	 * start) and by the row's own inset at the end, and the row's start inset matches the first.
	 * The row is `DataRow`'s, reached as a direct child of each line (or of the right-click
	 * trigger around it), so a list a row opens out keeps its own inset.
	 */
	.rows.edges {
		margin-inline-start: calc(-1 * var(--space-2));
		margin-inline-end: calc(-1 * var(--space-3));
	}

	.rows.edges > :global(.line > .row),
	.rows.edges > :global(.line > .row-trigger > .row),
	.rows.edges > .heads {
		padding-inline-start: var(--space-2);
	}

	/*
	 * The line between two rows stands on the pane's edges too; only the hover ground reaches
	 * past them. The item is pulled out to the ground's extent, so its border would run past both
	 * edges: the border stays for its pixel of room and goes clear, and the line is painted inside
	 * it, in from each end by what the list was pulled out by.
	 */
	.rows.edges > :global(.line + .line) {
		border-block-start-color: transparent;
		background-image: linear-gradient(var(--sift-line), var(--sift-line));
		background-repeat: no-repeat;
		background-origin: border-box;
		background-position: var(--space-2) 0;
		background-size: calc(100% - var(--space-2) - var(--space-3)) 1px;
	}

	/* No line under a row that runs across the list (a group heading), and none over the line
	   that holds one: the heading's own hairline is the one line there, as `DataRow` holds. */
	.rows.edges > :global(.line:has(> .row.across, > .row-trigger > .row.across) + .line),
	.rows.edges > :global(.line + .line:has(> .row.across, > .row-trigger > .row.across)) {
		background-image: none;
	}
</style>
