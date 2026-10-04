<script lang="ts">
	/* A wall tile under a portalled menu, the arrangement of the file panel over a wall: the tile is
	   a right-click trigger (which the library keeps pressable while any menu is open) that opens on
	   a click and starts a selection on a press, and the door beside it opens a menu over it. */
	import ContextMenu from './ContextMenu.svelte';
	import ContextMenuItem from './ContextMenuItem.svelte';
	import MenuButton from './MenuButton.svelte';

	interface Props {
		heard: string[];
	}

	let { heard }: Props = $props();
</script>

<ContextMenu>
	<button
		type="button"
		class="probe-tile"
		onpointerdown={() => heard.push('tile pressed')}
		onclick={() => heard.push('tile opened')}>A tile</button
	>
	{#snippet items()}
		<ContextMenuItem label="Open" onselect={() => heard.push('tile menu row')} />
	{/snippet}
</ContextMenu>

<MenuButton label="Add this file to" words="Add to">
	<ContextMenuItem label="Collection" onselect={() => heard.push('picked Collection')} />
</MenuButton>
