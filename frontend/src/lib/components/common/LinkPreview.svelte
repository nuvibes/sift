<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'LinkPreview',
		category: 'composition',
		role: 'a card about the thing under the pointer, shown while a link is hovered',
		basis: 'bits-ui:LinkPreview',
		states: ['closed', 'open']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* What is on the other end of a link: a card that can be walked into, unlike a Tooltip.
	   Portalled into the screen filling the window if there is one, else the end of the document. */
	import type { Snippet } from 'svelte';
	import { LinkPreview } from 'bits-ui';
	import { surface } from '$lib/shell/motion.svelte';

	interface Props {
		/** The trigger, spread onto the caller's own anchor rather than wrapped. */
		trigger: Snippet<[{ props: Record<string, unknown> }]>;
		/** What is drawn in the card. Links and text; it is walk-into-able, so controls are fine. */
		card: Snippet;
		/** Rest time before opening: slower than Tooltip's, since the card asks the server. */
		openDelay?: number;
		/** How long it stays after the pointer leaves, so it can be walked into. */
		closeDelay?: number;
		/** Which side of the trigger to sit on, when there is room. */
		side?: 'top' | 'bottom' | 'left' | 'right';
		/** Told when it opens, for a caller that fetches what the card shows. */
		onOpenChange?: (open: boolean) => void;
	}

	let {
		trigger,
		card,
		openDelay = 350,
		closeDelay = 300,
		side = 'top',
		onOpenChange
	}: Props = $props();

	let open = $state(false);

	/* Sampled when it opens and kept on close, so a leaving card does not jump portals. */
	let door = $state<Element | undefined>(undefined);

	function changed(next: boolean): void {
		if (next && typeof document !== 'undefined') door = document.fullscreenElement ?? undefined;
		open = next;
		onOpenChange?.(next);
	}
</script>

<LinkPreview.Root {open} onOpenChange={changed} {openDelay} {closeDelay}>
	<LinkPreview.Trigger>
		{#snippet child({ props })}
			{@render trigger({ props })}
		{/snippet}
	</LinkPreview.Trigger>

	<LinkPreview.Portal to={door}>
		<!--
		forceMount, so the card can be seen leaving: the {#if} below hands it to a transition.
		-->

		<LinkPreview.Content {side} sideOffset={8} forceMount>
			<!-- The child snippet, so this file's scoped rules reach the card. -->
			{#snippet child({ props, wrapperProps, open: shown })}
				{#if shown}
					<div {...wrapperProps}>
						<!-- `surface` plays in both directions and honours reduced motion. -->

						<div {...props} class="preview" transition:surface>
							{@render card()}
						</div>
					</div>
				{/if}
			{/snippet}
		</LinkPreview.Content>
	</LinkPreview.Portal>
</LinkPreview.Root>

<style>
	/* The floating surface's tokens, not `.ui-menu`, which would bring a menu's grid. */
	.preview {
		z-index: var(--z-menu);
		/* Wide enough for a name and a row of counts. */
		max-inline-size: 44ch;
		padding: var(--space-3);
		border-radius: var(--radius-lg);
		background: var(--sift-surface-3);
		box-shadow: var(--elev-3);
		color: var(--sift-ink);
	}
</style>
