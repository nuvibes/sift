<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'DataRows',
		category: 'composition',
		role: 'a list of rows with its columns declared once, so every row lines up',
		basis: 'own',
		states: ['default', 'empty', 'columns declared']
	} satisfies DesignEntry;

	/** One column of a list, declared on the list: every row hands its cells over by `id`. */
	export interface Column {
		/** Which of a row's `cells` goes in this column. */
		id: string;
		/** The heading over the column, on the list's one heading row. Absent, the column has none. */
		label?: string;
		/** The track: a length or a share, never content-sized, or lists stop lining up. */
		width: string;
		/** Words `start` (the default); numbers, sizes, durations and counts `end`. */
		align?: 'start' | 'end';
	}

	/** What a row reads from the list it is in. Absent: the list declared no columns. */
	export interface Declared {
		columns: readonly Column[];
		/**
		 * The actions' track where there is no hover; with a pointer they overlay the row's end.
		 */
		actions: string | undefined;
		/** Whether any row can fold: the arrow's own track at the end, the one drawn at rest. */
		folds?: boolean;
	}

	/** The arrow's track: one small glyph button. */
	export const FOLD_TRACK = 'var(--control-height-sm)';

	export const COLUMNS = Symbol('data-rows-columns');

	/** A track that sizes itself by its content, which a declared column may not be. */
	const CONTENT_SIZED = /\b(auto|max-content|min-content|fit-content)\b/;

	/** The declaration, checked: a content-sized track is refused. */
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

	/** The tracks with a pointer: the columns, then the arrow's. */
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
		/** The list's columns; a nested list inherits its row's tracks. */
		columns?: readonly Column[];
		/** How wide a row's actions are together, for the touch track. */
		actions?: string;
		/** Some row folds: the arrow, seen at rest, keeps a small track of its own at the end. */
		folds?: boolean;
		/** Stand on the pane's edges, for a list on a settings pane. */
		edges?: boolean;
	}

	let { items, key, row, label, columns, actions, folds = false, edges = false }: Props = $props();

	/* The declaration, own or inherited, read once. */
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

	// Snapshotted from the screen, not `items`, which differ while held.
	function hold() {
		if (frozenKeys === null) frozenKeys = shown.map(key);
	}

	// A press inside lets the hold go for a frame, so what it opens lands under its row.
	function releaseForPress(event: MouseEvent) {
		if (frozenKeys === null) return;
		frozenKeys = null;
		const list = event.currentTarget as HTMLElement;
		requestAnimationFrame(() => {
			if (list.matches(':hover') || list.contains(document.activeElement)) hold();
		});
	}

	// Released when the pointer or focus leaves the list, not between two rows.
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

	/* On the pane's edges: pulled out by the scroller's room, the row keeping its inset. */
	.rows.edges {
		margin-inline-start: calc(-1 * var(--space-2));
		margin-inline-end: calc(-1 * var(--space-3));
	}

	.rows.edges > :global(.line > .row),
	.rows.edges > :global(.line > .row-trigger > .row),
	.rows.edges > .heads {
		padding-inline-start: var(--space-2);
	}

	/* The line between rows painted inside, from edge to edge. */
	.rows.edges > :global(.line + .line) {
		border-block-start-color: transparent;
		background-image: linear-gradient(var(--sift-line), var(--sift-line));
		background-repeat: no-repeat;
		background-origin: border-box;
		background-position: var(--space-2) 0;
		background-size: calc(100% - var(--space-2) - var(--space-3)) 1px;
	}

	/* No line next to a heading row, whose hairline is the one line. */
	.rows.edges > :global(.line:has(> .row.across, > .row-trigger > .row.across) + .line),
	.rows.edges > :global(.line + .line:has(> .row.across, > .row-trigger > .row.across)) {
		background-image: none;
	}
</style>
