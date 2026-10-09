<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'HistoryList',
		category: 'composition',
		role: 'a run of events read down as one thread, oldest at the top',
		basis: 'composes:HistoryRow,Empty',
		states: ['a few', 'one', 'nothing yet']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: a list of rows with a line drawn behind them is layout. There is no state to
	hold; the one interactive thing is a row's Undo, Sift's own Button. */

	/* A history read down, oldest first as given; the thread between the marks says the rows are
	   in order. It does not fetch, so every history address can use it. */
	import Empty from './Empty.svelte';
	import HistoryRow from './HistoryRow.svelte';
	import type { HistoryEvent } from './history';

	interface Props {
		/** Oldest first. Drawn in the order given; see the header. */
		events: readonly HistoryEvent[];
		/** Passed to each row. Absent, no row draws an Undo. */
		onundo?: (event: HistoryEvent) => void;
		/** The event whose undo is in flight, so only its row spins. */
		undoing?: string | null;
		/** What to say when there is nothing at all. The caller's words: it knows what this is of. */
		emptyText?: string;
	}

	let {
		events,
		onundo,
		undoing = null,
		emptyText = 'Nothing has been recorded about this yet.'
	}: Props = $props();
</script>

{#if events.length === 0}
	<Empty icon="history" scope="block">{emptyText}</Empty>
{:else}
	<ol class="history-list">
		{#each events as event, index (`${event.kind}-${event.at}-${index}`)}
			<li>
				<HistoryRow {event} {onundo} undoing={undoing !== null && event.undo?.id === undoing} />
			</li>
		{/each}
	</ol>
{/if}

<style>
	/*
	 * The thread: a border on a pseudo-element from the first mark to the last, through their
	 * middles.
	 */
	.history-list {
		position: relative;
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.history-list::before {
		position: absolute;
		/* Half a mark at each end. */
		inset-block: var(--space-3);
		inset-inline-start: var(--space-3);
		border-inline-start: 1px solid var(--sift-line);
		content: '';
	}

	/* Above the thread, so each mark covers the line. */
	.history-list li {
		position: relative;
		padding-block: var(--space-2);
	}
</style>
