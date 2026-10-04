<script lang="ts">
	/*
	 * THE RECAPS THIS ACCOUNT HAS, newest first, each a way to open it.
	 *
	 * Drawn in two places: the Recaps block on Insights, where it names the newest few and a way to
	 * the rest, and the recaps screen, where it names them all. One component, so the two cannot
	 * come to draw a recap differently.
	 *
	 * A row is the title, the days it covers and how many cards it has: the span is what tells one
	 * "Your week" from the next, and the count is the count this reader will be shown now, which the
	 * server works out for the vault as it stands. A recap a locked vault leaves out is simply not in
	 * the list.
	 */
	import { onMount } from 'svelte';

	import { Empty } from '$lib/components/common';
	import { recapShelf } from '$lib/library/recaps.svelte';

	interface Props {
		/** How many to name, newest first; the rest are one link away. Absent: every one. */
		limit?: number;
		/**
		 * Whether the list is the whole screen (the recaps screen) or a band of a fuller one (the
		 * Recaps block on Insights, under its own heading). Its empty state is drawn to match.
		 */
		scope?: 'page' | 'block';
	}

	let { limit, scope = 'block' }: Props = $props();

	onMount(() => void recapShelf.load());

	const shown = $derived(
		limit === undefined ? recapShelf.recaps : recapShelf.recaps.slice(0, limit)
	);
	const more = $derived(recapShelf.recaps.length - shown.length);
</script>

{#if recapShelf.loaded && shown.length === 0}
	<!-- A block draws its sentence alone, so there the sentence carries what the page's title says. -->
	<!-- Two literal scopes rather than one passed in: the scope gate reads the word, and the two
	     draw differently (a page carries the glyph and title, a block its sentence alone). -->
	{#if scope === 'page'}
		<Empty scope="page" icon="event_repeat" title="No recaps yet">
			After a week, a month or a year with enough viewing in it, its recap appears here.
		</Empty>
	{:else}
		<Empty scope="block"
			>No recaps yet. After a week, a month or a year with enough viewing in it, its recap appears
			here.
		</Empty>
	{/if}
{:else if shown.length > 0}
	<ul class="heads">
		{#each shown as head (head.id)}
			<li>
				<a class="head" href="/insights/recaps/{encodeURIComponent(head.id)}">
					<span class="title">{head.title}</span>
					{#if head.span}
						<span class="span">{head.span}</span>
					{/if}
					<span class="cards">{head.cards === 1 ? '1 card' : `${head.cards} cards`}</span>
				</a>
			</li>
		{/each}
	</ul>
	{#if more > 0}
		<a class="every" href="/insights/recaps">All {recapShelf.recaps.length} recaps</a>
	{/if}
{/if}

<style>
	.heads {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	/* A row is the way in: it answers the pointer with the state layer on its surface, not on its
	   ink alone. */
	.head {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: var(--space-1) var(--space-3);
		padding: var(--space-2) var(--space-3);
		border-radius: var(--radius-md);
		color: var(--sift-ink);
		text-decoration: none;
		transition: background-color var(--dur-instant) var(--ease);
	}

	.head:hover,
	.head:focus-visible {
		background-color: color-mix(in srgb, currentColor var(--layer-hover), transparent);
	}

	.title {
		font: var(--text-body);
	}

	.span,
	.cards {
		font: var(--text-label);
		color: var(--sift-ink-3);
	}

	.cards {
		margin-inline-start: auto;
	}

	.every {
		display: inline-block;
		margin-block-start: var(--space-2);
		border-radius: var(--radius-sm);
		font: var(--text-label);
		color: var(--sift-accent-text);
		text-decoration: underline;
		text-decoration-color: transparent;
		text-underline-offset: 3px;
		transition: text-decoration-color var(--dur-instant) var(--ease);
	}

	.every:hover,
	.every:focus-visible {
		text-decoration-color: currentColor;
	}
</style>
