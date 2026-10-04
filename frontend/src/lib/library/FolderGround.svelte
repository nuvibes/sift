<script lang="ts">
	/* WHY NOT SHARED: button: there is nothing to share with. These are the shared menu rows, the
	   folder's own verbs (`folderVerbs`) and the rule about which of them a guest gets. */
	/*
	 * The menu on the GROUND of the folder view: right-click where there is nothing. Its own file
	 * because the ground is in two places and is one place: the empty space around the folder band
	 * and around the wall are both "in this folder", and written twice the two menus would drift.
	 */
	import ContextMenuGroup from '$lib/components/common/ContextMenuGroup.svelte';
	import ContextMenuItem from '$lib/components/common/ContextMenuItem.svelte';
	import VerbMenuItems from '$lib/components/common/VerbMenuItems.svelte';
	import { folderMenuParts, type FolderMarks } from '$lib/library/folder-verbs';
	import { session } from '$lib/shell/session.svelte';

	interface Props {
		/** Make a folder inside the one being looked at. Absent where there is nowhere to make one. */
		onnew?: () => void;
		/*
		 * Say what the folder being looked at IS, and who can reach it. Both absent at the top of the
		 * tree: "Your folders" is a place in this screen rather than a folder on a disk, and a panel
		 * about it would have nothing true to put in any of its rows.
		 */
		onproperties?: () => void;
		onvisibility?: () => void;
		marks?: FolderMarks;
	}

	let { onnew, onproperties, onvisibility, marks }: Props = $props();

	const parts = $derived(
		folderMenuParts({ isAdmin: session.isAdmin, handlers: { visibility: onvisibility } }, marks)
	);
</script>

<!-- Making a folder is a write into somebody's library, so it is an admin's, and it is withheld
     rather than shown greyed, because a guest has no way to earn it and a permanently dead row
     explains nothing. The server refuses it either way; this is what stops it being offered. -->
<!-- The parts: making something here, what may leave this device, who sees it, what this is. -->
<ContextMenuGroup>
	{#if onnew && session.isAdmin}
		<ContextMenuItem icon="create_new_folder" label="New folder" onselect={onnew} />
	{/if}
</ContextMenuGroup>
{#each parts as part (part[0].id)}
	<ContextMenuGroup>
		<VerbMenuItems verbs={part} ids={[]} />
	</ContextMenuGroup>
{/each}
<ContextMenuGroup>
	{#if onproperties}
		<ContextMenuItem icon="info" label="Properties" onselect={onproperties} />
	{/if}
</ContextMenuGroup>
