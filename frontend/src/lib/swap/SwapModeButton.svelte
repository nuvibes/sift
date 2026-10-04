<script lang="ts">
	/* NOT ON THE GALLERY: a singleton inside the Add button's panel, as the panel itself is. */
	/*
	 * The way into swap mode and out of it.
	 *
	 * In the Add button's panel, at its bottom right, rather than on the top bar: a swap is one more
	 * way things come into the library, and the bar keeps the controls about the whole window. It
	 * is still reachable from every screen, because Add is, and the mode is still the whole
	 * window's: picks are made on every wall and kept while moving between them. Pressed while
	 * the mode is on, in the swap's own arrows, the glyph the Swap page and History wear. An
	 * admin's only, as every swap route is.
	 */
	import { Button } from '$lib/components/common';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { session } from '$lib/shell/session.svelte';
	import { swapMode } from './mode.svelte';

	interface Props {
		/** Called after the press, so the panel it sits in can close and show the drawer. */
		onpressed?: () => void;
	}

	let { onpressed }: Props = $props();

	const label = $derived(
		swapMode.on ? 'Deselect all and leave swap mode' : 'Pick what to offer in a swap'
	);

	function pressed(): void {
		if (swapMode.on) swapMode.leave();
		else swapMode.enter();
		onpressed?.();
	}
</script>

{#if session.isAdmin}
	<Tooltip {label} placement="bottom">
		<Button
			tone="ghost"
			icon="swap_horiz"
			aria-label={label}
			pressed={swapMode.on}
			onclick={pressed}
		/>
	</Tooltip>
{/if}
