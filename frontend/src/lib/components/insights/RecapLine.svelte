<script lang="ts">
	/*
	 * THE QUIET LINE ON BROWSE'S HEADER: "Your September is ready", and a cross.
	 *
	 * Drawn in the header's own status place, beside the title, where Browse already says why the
	 * wall may not be keeping up and how many files are arriving: the one slot a screen has for a
	 * passing fact. Never a second bar above the wall, never a modal, never a sound.
	 *
	 * The same announcement as the card at the top of Insights, from the same list: opening the
	 * recap or pressing the cross in either place ends it in both. The server picks it and stops
	 * announcing it, so nothing here counts days.
	 *
	 * Draws nothing at all while there is nothing to announce, and nothing when the read failed:
	 * a missing line is the same drawing as no recap.
	 */
	import { onMount } from 'svelte';

	import { Button, Tooltip } from '$lib/components/common';
	import { recapShelf } from '$lib/library/recaps.svelte';

	onMount(() => void recapShelf.load());

	const recap = $derived(recapShelf.announced);
</script>

{#if recap}
	<span class="line">
		<a class="open" href="/insights/recaps/{encodeURIComponent(recap.id)}">{recap.title} is ready</a
		>
		<Tooltip label="Close">
			<Button
				tone="ghost"
				size="small"
				icon="close"
				aria-label="Close"
				onclick={() => void recapShelf.dismiss(recap.id)}
			/>
		</Tooltip>
	</span>
{/if}

<style>
	/* The label face the header's other passing lines wear, so it reads as one of them. */
	.line {
		display: inline-flex;
		align-items: center;
		gap: var(--space-1);
		font: var(--text-label);
		color: var(--sift-ink-2);
	}

	.open {
		border-radius: var(--radius-sm);
		color: var(--sift-accent-text);
		text-decoration: underline;
		text-decoration-color: transparent;
		text-underline-offset: 3px;
		transition:
			color var(--dur-instant) var(--ease),
			text-decoration-color var(--dur-instant) var(--ease);
	}

	.open:hover,
	.open:focus-visible {
		text-decoration-color: currentColor;
	}
</style>
