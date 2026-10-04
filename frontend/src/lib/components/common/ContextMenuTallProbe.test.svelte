<script lang="ts">
	/* A harness for where a long menu may open: thirty rows, more than the shared ceiling holds. */
	import ContextMenu from './ContextMenu.svelte';
	import ContextMenuItem from './ContextMenuItem.svelte';

	const rows = Array.from({ length: 30 }, (_, at) => `Row ${at + 1}`);
</script>

<ContextMenu label="Actions">
	{#snippet items()}
		{#each rows as row (row)}
			<ContextMenuItem label={row} onselect={() => {}} />
		{/each}
	{/snippet}
	<div>A cell near the bottom of the window</div>
</ContextMenu>
