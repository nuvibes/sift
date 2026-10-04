<script lang="ts">
	/*
	 * A harness for the surface a menu tells its rows: the flyout a row opens must come out as the
	 * same `ui-menu` the menu is, portalled where the menu said. There is one dressing, so what is
	 * proved is that the flyout wears it.
	 */
	import ContextMenu from './ContextMenu.svelte';
	import ContextMenuItem from './ContextMenuItem.svelte';
</script>

<ContextMenu label="Actions">
	{#snippet items()}
		<ContextMenuItem label="Copy link" onselect={() => {}} />
		<ContextMenuItem label="Add to">
			<ContextMenuItem label="Collection" onselect={() => {}} />
		</ContextMenuItem>
	{/snippet}
	<div>A cell</div>
</ContextMenu>
