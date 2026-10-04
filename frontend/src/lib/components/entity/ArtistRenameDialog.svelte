<script lang="ts">
	/*
	 * The sheet that renames an artist on every song, drawn once by each screen that offers it.
	 *
	 * The same `RenameDialog` a tag is renamed in, with the artist's own sentence: the rename reaches
	 * every song crediting them, and a name another artist has joins the two. See `$lib/entity/artists`.
	 *
	 * NOT ON THE GALLERY: it is `RenameDialog` over the artist holder, opened by a press; closed it
	 * draws nothing.
	 */
	import RenameDialog from './RenameDialog.svelte';
	import { ARTIST_NAME_LIMIT, artistRename, RENAME_SAYS } from '$lib/entity/artists.svelte';
</script>

<RenameDialog
	bind:open={artistRename.open}
	named={artistRename.artist?.name ?? ''}
	consequence={RENAME_SAYS}
	bind:value={artistRename.typed}
	maxlength={ARTIST_NAME_LIMIT}
	onconfirm={() => void artistRename.save()}
/>
