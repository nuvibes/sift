<script lang="ts">
	/*
	 * Folders read for the people in them.
	 *
	 * Answering one names the faces and files every video and picture in the folder in one go,
	 * which is the reason a folder is worth reading at all.
	 */
	import FolderSuggestions from '$lib/components/suggestions/FolderSuggestions.svelte';
	import type { OnPaging } from '$lib/components/common/Pager.svelte';

	/*
	 * Handed straight through: the pager goes to the frame's foot, drawn by the route. The screen
	 * has no tab-line control; see `FolderSuggestions`.
	 */
	let { onpaging }: { onpaging?: OnPaging } = $props();
</script>

<FolderSuggestions {onpaging} />
