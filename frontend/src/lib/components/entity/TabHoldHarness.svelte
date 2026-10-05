<script lang="ts">
	/* A page's tabs inside the hold, with each wall a box saying which tab it is drawn for, and the
	   test holding the press that says a wall has its answer. The shape an entity page hands
	   `TabHold`, minus the walls, which fetch. */
	import TabHold, { wallOfTab } from './TabHold.svelte';
	import TabHeldProbe from './TabHeldProbe.svelte';

	interface Props {
		tab: string;
		/** Handed each wall's `arrived` as it is drawn, by the tab it is drawn for. */
		onwall: (tab: string, arrived: () => void) => void;
	}

	let { tab, onwall }: Props = $props();
</script>

<TabHold {tab} wallOf={wallOfTab}>
	{#snippet surface(drawn, arrived)}
		<div class="wall" data-tab={drawn} {@attach () => onwall(drawn, arrived)}>
			{drawn}<TabHeldProbe />
		</div>
	{/snippet}
</TabHold>
