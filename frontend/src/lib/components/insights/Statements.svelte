<script lang="ts">
	/*
	 * A block's statements, each a sentence the server built as pieces and `HistorySentence` draws.
	 *
	 * The client composes no sentence: no word here is joined to a figure, no name is looked for
	 * inside a line, and a statement the server did not send is not written. A named thing arrives
	 * as a piece with its address, so a person, a Site or a file in a sentence is the way to it.
	 */
	import HistorySentence from '$lib/components/common/HistorySentence.svelte';
	import type { HistoryPiece } from '$lib/components/common/history';

	interface Props {
		lines: readonly (readonly HistoryPiece[])[];
		/** The page's first sentences: set larger, as the summary the page opens on. */
		lead?: boolean;
		/** A block's notes (still counting, some hidden): set small and quiet. */
		quiet?: boolean;
	}

	let { lines, lead = false, quiet = false }: Props = $props();
</script>

{#if lines.length > 0}
	<div class="statements" class:lead class:notes={quiet}>
		{#each lines as line, index (index)}
			<p><HistorySentence pieces={line} /></p>
		{/each}
	</div>
{/if}

<style>
	.statements {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		max-inline-size: 72ch;
	}

	p {
		margin: 0;
		font: var(--text-body);
		color: var(--sift-ink);
	}

	.lead p {
		font: var(--text-h2);
	}

	/* Its own class, not the app-wide `.quiet`: that one would dress the list, not its lines. */
	.notes p {
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}
</style>
