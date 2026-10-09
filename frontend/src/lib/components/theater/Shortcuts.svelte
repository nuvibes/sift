<script lang="ts">
	/*
	 * What the keys do here: Theater takes the number keys for cells. Generated from the
	 * declarations.
	 */
	import KeyRows from '$lib/components/KeyRows.svelte';
	import Panel from '$lib/components/common/Panel.svelte';
	import Scroller from '$lib/components/common/Scroller.svelte';
	import { shortcutsIn } from '$lib/shell/shortcuts';

	/* Theater's keys, then "Anywhere"'s. */
	const KEYS = [...shortcutsIn('Theater'), ...shortcutsIn('Anywhere')];

	/* Measured: the bar's height varies. */
	let box: HTMLElement | undefined = $state();
	let top = $state(0);

	$effect(() => {
		const panel = box;
		if (!panel) return;
		const measure = () => {
			top = Math.max(0, panel.getBoundingClientRect().top);
		};
		measure();
		window.addEventListener('resize', measure);
		return () => window.removeEventListener('resize', measure);
	});
</script>

<div class="key-panel" bind:this={box} style:--key-panel-top="{top}px">
	<Panel inset="md" corner="md" floating>
		<Scroller>
			<KeyRows shortcuts={KEYS} />
		</Scroller>
	</Panel>
</div>

<style>
	/* One `minmax(0, 1fr)` row at each level, so the scroller can be shorter than its rows. */
	.key-panel {
		display: grid;
		grid-template-rows: minmax(0, 1fr);
		max-block-size: calc(100dvh - var(--key-panel-top, 0px) - var(--space-4));
		margin-inline: var(--space-4);
		margin-block-end: var(--space-2);
	}

	.key-panel > :global(.panel) {
		grid-template-rows: minmax(0, 1fr);
		min-block-size: 0;
	}
</style>
