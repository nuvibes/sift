<script lang="ts">
	/* A harness for `FileActions`, which hands its two rows to a snippet, and a snippet cannot be
	   given to `mount` from a test file. The same arrangement `NoteProbe` uses.

	   The rows are plain buttons here rather than menu items, and that is the point of the harness:
	   what the test is about is WHEN each row is offered and what pressing it sends, neither of
	   which is a fact about the menu it happens to be drawn in. The real caller puts them in one.

	   WHY NOT SHARED: button: a test-only harness. Plain buttons keep the assertions about WHEN a row is
	   offered, rather than about how the shared button draws its label. */
	import FileActions from './FileActions.svelte';

	interface Props {
		id: string;
		filename: string | null;
		onrenamed?: (filename: string | null) => void;
		held?: boolean;
	}

	let { id, filename, onrenamed, held = false }: Props = $props();
</script>

<FileActions {id} {filename} {onrenamed} {held}>
	{#snippet children({ canOrganize, rename, undo })}
		{#if canOrganize}
			<button type="button" onclick={rename}>Rename</button>
		{/if}
		{#if undo}
			<button type="button" onclick={undo}>Undo move</button>
		{/if}
	{/snippet}
</FileActions>
