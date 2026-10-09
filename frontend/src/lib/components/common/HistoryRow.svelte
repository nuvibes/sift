<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';
	import { exactly } from '$lib/shell/when';

	export const design = {
		name: 'HistoryRow',
		category: 'composition',
		role: 'one thing that happened to something, as a mark, a sentence, a time and a way back',
		basis: 'composes:Button,HistorySentence',
		states: [
			'yours',
			"somebody else's",
			'automatic',
			'undoable',
			'undone',
			'unrecorded time',
			'names something',
			'stands for a whole press'
		]
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: there is nothing here for a component library to own. It is a glyph, two
	lines of text and one button, Sift's own Button. */

	/* One event, one row, the only one in the app: the sentence is the server's, drawn by
	 * HistorySentence; a ring marks this account's own act; Undo shows only where the server offers
	 * it. */
	import Button from './Button.svelte';
	import HistorySentence from './HistorySentence.svelte';
	import Tooltip from './Tooltip.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { BY_YOU, markOf, markWords, sinceOf, spanText, type HistoryEvent } from './history';

	interface Props {
		event: HistoryEvent;
		/** What Undo does; absent, no Undo. The caller wires the request (`undo.kind`). */
		onundo?: (event: HistoryEvent) => void;
		/** Waiting on the undo this row asked for. Stops a second press while the first is in flight. */
		undoing?: boolean;
	}

	let { event, onundo, undoing = false }: Props = $props();

	/* Offered, not taken, and somebody listening: any one alone is a button doing nothing. */
	const canUndo = $derived(event.undo !== null && !event.reversed && onundo !== undefined);

	/* What the mark means, in words. `markOf` and this answer the same question. See there. */
	const means = $derived(markWords(event));

	/* What else a decision wrote, while it stands. */
	const more = $derived(event.reversed ? '' : (event.more ?? ''));
</script>

<div class="history-row">
	<!-- The mark, named by what it means (the server's words), hovered or focused. -->

	<Tooltip label={means}>
		<span class="mark" class:mine={event.actor === BY_YOU} role="img" aria-label={means}>
			<Icon name={markOf(event.kind, event.via, event.how)} size={14} />
		</span>
	</Tooltip>

	<div class="said">
		<!-- The line from the server's pieces, as every History surface draws it. -->
		<div class="what" class:taken-back={event.reversed}>
			<HistorySentence
				pieces={event.pieces}
				what={event.what}
				detail={event.detail}
				quiet={event.reversed}
				away={event.away ?? null}
			/>
		</div>
		{#if more}
			<p class="more">{more}</p>
		{/if}
		<p class="when">
			<!-- A span where the line stands for a run (a day's downloads), one moment otherwise. -->
			{#if event.at !== null}
				<Tooltip label={exactly(event.at)}>
					<span class="moment">{spanText(event.at, sinceOf(event))}</span>
				</Tooltip>
			{:else}
				<span class="moment unrecorded">{spanText(event.at, sinceOf(event))}</span>
			{/if}
			{#if event.reversed}
				<span class="reversed">Undone</span>
			{/if}
		</p>
	</div>

	{#if canUndo}
		<Button icon="undo" tone="ghost" size="small" busy={undoing} onclick={() => onundo?.(event)}
			>Undo</Button
		>
	{/if}
</div>

<style>
	/* Aligned to the start, so the mark stays by a wrapped sentence's first line. */
	.history-row {
		display: flex;
		align-items: flex-start;
		gap: var(--space-3);
	}

	/* A round disc on the list's thread, never squeezed. */
	.mark {
		display: flex;
		flex: none;
		align-items: center;
		justify-content: center;
		inline-size: var(--space-6);
		block-size: var(--space-6);
		border: 1px solid var(--sift-line);
		border-radius: var(--radius-full);
		background: var(--sift-surface-2);
		color: var(--sift-ink-3);
	}

	/* This account's own act, in the accent's text colour (the fill fails 3:1 on its tint). */
	.mark.mine {
		border-color: var(--sift-accent);
		background: var(--sift-accent-bg);
		color: var(--sift-accent-text);
	}

	/* min 0, so a long sentence wraps rather than pushing Undo off. */
	.said {
		flex: 1 1 auto;
		min-inline-size: 0;
	}

	.what {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink);
	}

	/* Undone is still something that happened: quieter, never struck through. */
	.what.taken-back {
		color: var(--sift-ink-3);
	}

	/* The line's own size in caption ink, part of the line. */
	.more {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	/* The footnote under it: when, and whether it still stands. Who did it is the line's first word. */
	.when {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-2);
		margin: 0;
		font: var(--text-label);
		color: var(--sift-ink-3);
	}

	/* An unrecorded time in italic, a phrase where others have a date. */
	.moment.unrecorded {
		font-style: italic;
	}

	/* Caption ink, not the decoration grey: "Undone" is a word somebody reads. */
	.reversed {
		color: var(--sift-ink-3);
	}
</style>
