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
	   hold, nothing opens and nothing takes focus: the only interactive thing on it is the Undo
	   inside a row, which is Sift's own `Button`. */

	/*
	 * A HISTORY, READ DOWN. The rows are `HistoryRow`; what this owns is the thread between them.
	 *
	 * ## Why the line matters enough to be drawn
	 *
	 * Without it a history is a list, and a list is read as a set of things that are all equally
	 * true now. A history is not that: it is one thing after another, and the whole reason somebody
	 * opens it is to see what followed what. The line is what says the rows are in an order rather
	 * than merely stacked.
	 *
	 * It is drawn BEHIND the marks, from the first to the last, and it stops at both: a thread
	 * running past the end into empty space says the list is cut off when it is not.
	 *
	 * ## Oldest first, and the caller does not get to choose
	 *
	 * The server sends them in order and this draws them in the order it was given. A prop to flip
	 * it would be a second answer to "which way does time run", and two screens would eventually
	 * disagree about it, which is the one thing a history may not be ambiguous about.
	 *
	 * ## It does not fetch, and that is the point of it being here
	 *
	 * A history of a file, of a person and of a queue are three different addresses and one shape.
	 * A component that fetched would have to know which, and could then only ever be used for the
	 * one it knew.
	 */
	import Empty from './Empty.svelte';
	import HistoryRow from './HistoryRow.svelte';
	import type { HistoryEvent } from './history';

	interface Props {
		/** Oldest first. Drawn in the order given; see the header. */
		events: readonly HistoryEvent[];
		/** Passed to each row. Absent, no row draws an Undo. */
		onundo?: (event: HistoryEvent) => void;
		/**
		 * The event whose undo is in flight, by its `undo.id`.
		 *
		 * An id rather than a boolean, because a history is a list and a spinner on every row at
		 * once would say every one of them is being taken back.
		 */
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
	 * The thread. Drawn as a border down a pseudo-element rather than as a background gradient,
	 * because it has to START at the first mark and STOP at the last: a gradient would have to be
	 * told where those are in percentages, which changes with every row added.
	 *
	 * Inset by half a mark plus the list's own padding, so it runs through the middle of the discs
	 * rather than beside them. The mark is `--space-6` across, so half of it is `--space-3`.
	 */
	.history-list {
		position: relative;
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.history-list::before {
		position: absolute;
		/* From the middle of the first mark to the middle of the last. A row is one line of
		   sentence plus one of footnote, and half a mark is what centres the end of the thread on
		   the disc at each end. */
		inset-block: var(--space-3);
		inset-inline-start: var(--space-3);
		border-inline-start: 1px solid var(--sift-line);
		content: '';
	}

	/* Above the thread, so each mark's own ground covers the line behind it rather than the line
	   showing through the disc. */
	.history-list li {
		position: relative;
		padding-block: var(--space-2);
	}
</style>
