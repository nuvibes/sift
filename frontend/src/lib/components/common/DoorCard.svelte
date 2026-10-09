<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'DoorCard',
		category: 'surface',
		role: 'a centered box on an otherwise empty page, for a screen that is one step',
		basis: 'own',
		states: ['default']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* NOT ON THE GALLERY: it fills the window. A card that covers the page cannot sit in a row of
	specimens; the gallery shows sign-in, first-run setup and the connect screen instead. */
	/* WHY NOT BITS-UI: bits-ui has no page-level card. This is a centred box on an empty page:
	layout and tokens, with no behaviour of its own at all. */
	/* The card on an empty page: one thing to do before you are inside Sift. Layout only. */
	import type { Snippet } from 'svelte';

	import Logo from '$lib/components/Logo.svelte';

	interface Props {
		/** The one thing this screen is for, as a sentence fragment somebody reads first. */
		heading: string;
		/**
		 * Draw the heading, or name the page for assistive technology only (sign-in's button says
		 * it).
		 */
		drawHeading?: boolean;
		/** A line under the heading, where the heading alone would leave a question. */
		explain?: string | null;
		/** Centre the whole stack, for a card asking one short thing (the lock screen). */
		centred?: boolean;
		children: Snippet;
		/** Wraps the contents. A form when the screen submits one; a plain box when it does not. */
		onsubmit?: (event: SubmitEvent) => void;
		/** The card, for a screen that shakes it from script on a wrong PIN. */
		element?: HTMLElement | null;
	}

	let {
		heading,
		explain,
		children,
		onsubmit,
		drawHeading = true,
		centred = false,
		element = $bindable(null)
	}: Props = $props();
</script>

<main class="page">
	{#if onsubmit}
		<form class="card" class:centred bind:this={element} {onsubmit}>
			{@render inside()}
		</form>
	{:else}
		<div class="card" class:centred bind:this={element}>
			{@render inside()}
		</div>
	{/if}
</main>

{#snippet inside()}
	<Logo variant="lockup" height={32} />

	<h1 class:named-only={!drawHeading}>{heading}</h1>
	{#if explain}
		<p class="explain">{explain}</p>
	{/if}

	{@render children()}
{/snippet}

<style>
	.page {
		display: grid;
		place-items: center;
		/* The window by default; a container that is not the window sets --door-height. */

		min-height: var(--door-height, 100dvh);
		/* The shorthand first, then the window strip cleared, or the shorthand overwrites it. */
		padding: var(--space-4);
		padding-block-start: calc(var(--window-chrome) + var(--space-4));
		background: var(--sift-bg);
	}

	.card {
		display: flex;
		flex-direction: column;
		gap: var(--space-4);
		width: 100%;
		max-width: 380px;
		/* Less room above the logo. */
		padding: var(--space-4) var(--space-8) var(--space-8);
		border-radius: var(--radius-xl);
		background: var(--sift-surface-1);
		border: 1px solid var(--sift-line);
		box-shadow: var(--elev-3);
	}

	/* The logo at its natural size, not stretched by the column. */
	.card :global(.logo) {
		align-self: center;
	}

	/* The one action centred under the fields; direct children only. */
	.card > :global(button) {
		align-self: center;
	}

	h1 {
		margin: 0;
		font: var(--text-h2);
		letter-spacing: var(--tracking-h2);
		color: var(--sift-ink);
		/* A name in a heading can be one unbroken token; it breaks rather than leaving the box. */
		overflow-wrap: anywhere;
	}

	/* Centred by text-align, so the fields keep full width; scoped to `.centred .field`. */
	.centred {
		text-align: center;
	}

	.centred :global(.field .label) {
		/* The label is a block in a grid cell, so its own text needs telling as well. */
		text-align: center;
	}

	/* Named for assistive technology and drawn nowhere. */
	h1.named-only {
		position: absolute;
		inline-size: 1px;
		block-size: 1px;
		margin: -1px;
		padding: 0;
		overflow: hidden;
		clip-path: inset(50%);
		white-space: nowrap;
	}

	.explain {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}
</style>
