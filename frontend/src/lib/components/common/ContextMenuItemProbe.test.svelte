<script lang="ts">
	/* A harness for a menu row that HOLDS rows, which takes them as children, and a snippet cannot
	   be handed to `mount` from a test file. The same arrangement `NoteProbe` uses.

	   Opened by the three-dot door rather than by a right-click, because the two draw the same rows
	   from the same library internals (see `MenuButton`) and a button is one `click()` away. */
	import MenuButton from './MenuButton.svelte';
	import ContextMenuItem from './ContextMenuItem.svelte';

	/* And a row with a STATE, held here rather than in the row, which is the arrangement the
	   Activity screen's density switch uses. */
	let ticked = $state(false);
</script>

<MenuButton label="More for this file">
	<ContextMenuItem label="Copy link" onselect={() => {}} />
	<ContextMenuItem label="Compact rows" checked={ticked} onselect={() => (ticked = !ticked)} />
	<ContextMenuItem label="Add to">
		<ContextMenuItem label="Collection" onselect={() => {}} />
		<ContextMenuItem label="Photo Set" onselect={() => {}} />
	</ContextMenuItem>
</MenuButton>
