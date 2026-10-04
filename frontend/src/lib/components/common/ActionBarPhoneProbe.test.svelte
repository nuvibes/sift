<script lang="ts">
	/* A harness for the selection bar with six named verbs, a group among them and a door for the
	   rest, split by the shared rule the way every bar splits its list. Snippets cannot be handed to
	   `mount` from a test file, so the bar is drawn here. */
	import ActionBar from './ActionBar.svelte';
	import VerbButtons from './VerbButtons.svelte';
	import VerbMore from './VerbMore.svelte';
	import { barShape, type Verb } from './verbs';

	const run = () => {};
	const verbs: Verb[] = [
		{
			id: 'add',
			label: 'Add to',
			icon: 'add',
			group: 'file',
			primary: true,
			children: [{ id: 'collection', label: 'Collection', icon: 'folder', run }]
		},
		{ id: 'enrich', label: 'Enrich', icon: 'auto_awesome', group: 'enrich', primary: true, run },
		{ id: 'move', label: 'Move', icon: 'drive_file_move', group: 'change', primary: true, run },
		{ id: 'rename', label: 'Rename', icon: 'edit', group: 'change', primary: true, run },
		{ id: 'share', label: 'Share', icon: 'group', group: 'share', primary: true, run },
		{ id: 'link', label: 'Copy link', icon: 'link', group: 'keep', run },
		{ id: 'remove', label: 'Remove', icon: 'delete', destructive: true, run }
	];
	const shape = $derived(barShape(verbs));
	const ids = ['a', 'b', 'c'];
</script>

<ActionBar count={3} noun="file" onclear={() => {}}>
	{#snippet actions()}
		<VerbButtons {ids} verbs={shape.named} />
	{/snippet}
	{#snippet overflow()}
		<VerbMore {ids} verbs={shape.rest} noun="file" />
	{/snippet}
</ActionBar>
