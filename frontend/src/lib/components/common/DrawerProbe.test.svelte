<script lang="ts">
	import Drawer, { type DrawerSide } from './Drawer.svelte';

	interface Props {
		open?: boolean;
		beside?: boolean;
		side?: DrawerSide;
		onclose?: () => void;
	}

	let { open = $bindable(false), beside = false, side = 'right', onclose }: Props = $props();
</script>

<button type="button" class="page-thing">On the page</button>
<Drawer bind:open {beside} {side} {onclose} label="Swap">
	<p class="inside">Picked</p>
	{#snippet footer()}
		<button type="button" class="leave">Leave</button>
	{/snippet}
</Drawer>
