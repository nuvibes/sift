<script lang="ts">
	/*
	 * THE CARD AT THE TOP OF INSIGHTS: a recap is ready, and the way to it.
	 *
	 * "Your September", a middle dot and "Ready to read" ("Your week" on a Monday). The whole card
	 * opens the recap, and opening it is what ends the announcement (the server writes that when it
	 * draws the recap); the cross ends it without opening. Either way the recap stays in the Recaps
	 * list below, to be opened whenever.
	 *
	 * Never a modal, never a sound, never a notification outside the window. It is one card on the
	 * page somebody already chose to open, and it is gone a week after the recap was made whether or
	 * not anybody looked: the server stops announcing it then, so nothing here counts days.
	 *
	 * Draws nothing at all while there is nothing to announce, including while the list is being
	 * read and when the read failed: a missing card is the same drawing as no recap, and an error
	 * about a nicety has no business at the top of a page.
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
