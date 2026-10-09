<script lang="ts">
	/*
	 * The quiet mark in a picture's corner and what it says when reached, for the item detail and a
	 * Theater cell. Inside the picture, since fullscreen paints nothing portalled. Never a warning
	 * colour.
	 */
	import type { Snippet } from 'svelte';

	import Button from '$lib/components/common/Button.svelte';

	interface Props {
		label: string;
		/** Arbitrary markup, so a caller can link to the setting. */
		children: Snippet;
	}

	let { label, children }: Props = $props();
</script>

<div class="notice">
	<Button tone="ghost" size="small" shape="circle" class="mark" icon="info" aria-label={label} />
	<p class="words">{@render children()}</p>
</div>

<style>
	/* `row-reverse`, so the words open INWARDS. */
	.notice {
		display: flex;
		flex-direction: row-reverse;
		align-items: flex-start;
		gap: var(--space-2);
	}

	/* A box for the glyph, the small control's height; on a phone the reach grows, not the size. */
	.notice :global(.mark) {
		flex: none;
		transition:
			background var(--dur-fast) var(--ease),
			color var(--dur-fast) var(--ease);
		inline-size: var(--control-height-sm);
		block-size: var(--control-height-sm);
		line-height: 1;
		border-radius: var(--radius-full);
		background: var(--sift-scrim);
		color: var(--sift-ink);
	}

	.words {
		margin: 0;
		max-inline-size: 34ch;
		padding: var(--space-2) var(--space-3);
		border-radius: var(--radius-sm);
		background: var(--sift-scrim-strong);
		color: var(--sift-ink);
		font: var(--text-body-sm);
		opacity: 0;
		/* Not a hover target while invisible, or its width opens the panel by itself. */
		pointer-events: none;
		transform: translateY(calc(var(--space-1) * -1));
		transition:
			opacity var(--dur-fast) var(--ease),
			transform var(--dur-fast) var(--ease);
	}

	.notice :global(.mark:hover),
	.notice :global(.mark:focus-visible) {
		background: var(--sift-scrim-strong);
		color: var(--sift-ink);
	}

	.notice:hover .words,
	.notice:focus-within .words {
		opacity: 1;
		pointer-events: auto;
		transform: none;
	}
</style>
