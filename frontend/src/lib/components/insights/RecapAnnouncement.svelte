<script lang="ts">
	/*
	 * THE CARD AT THE TOP OF INSIGHTS: a recap is ready, and the way to it.
	 *
	 * "Your September", a middle dot and "Ready to read". Which recap is the server's: the longest
	 * period's first. The whole card opens it, which ends the announcement; the cross ends it
	 * without opening. Either way the recap stays in the Recaps list below.
	 *
	 * Never a modal, never a sound, never a notification outside the window. The server stops
	 * announcing a recap after a week (a day's after one day), so nothing here counts days.
	 *
	 * Draws nothing while there is nothing to announce, while the list is read and when the read
	 * failed: an error about a nicety has no business at the top of a page.
	 */
	import { onMount } from 'svelte';

	import { Button, Panel, Tooltip } from '$lib/components/common';
	import { recapShelf } from '$lib/library/recaps.svelte';

	onMount(() => void recapShelf.load());

	const recap = $derived(recapShelf.announced);
</script>

{#if recap}
	<Panel tone="raised" inset="sm" label="{recap.title}, ready to read">
		<div class="announcement">
			<a class="open" href="/insights/recaps/{encodeURIComponent(recap.id)}">
				<span class="words">
					{recap.title}
					<span class="ready">&middot; Ready to read</span>
				</span>
				{#if recap.span}
					<span class="span">{recap.span}</span>
				{/if}
			</a>
			<Tooltip label="Close">
				<Button
					tone="ghost"
					icon="close"
					aria-label="Close"
					onclick={() => void recapShelf.dismiss(recap.id)}
				/>
			</Tooltip>
		</div>
	</Panel>
{/if}

<style>
	.announcement {
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}

	/* The card is the way in: its whole face answers the pointer with the state layer (its own ink
	   mixed into the card's ground), so the answer is visible to somebody not looking straight at
	   the words, on whatever ground the card stands. */
	.open {
		display: flex;
		flex: 1;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2) var(--space-3);
		padding: var(--space-3) var(--space-4);
		border-radius: var(--radius-md);
		color: var(--sift-ink);
		text-decoration: none;
		transition: background-color var(--dur-instant) var(--ease);
	}

	.open:hover,
	.open:focus-visible {
		background-color: color-mix(in srgb, currentColor var(--layer-hover), transparent);
	}

	.words {
		font: var(--text-h2);
	}

	.ready {
		color: var(--sift-ink-2);
	}

	.span {
		font: var(--text-label);
		color: var(--sift-ink-3);
	}
</style>
