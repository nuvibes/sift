<script lang="ts">
	import { counted } from '$lib/entity/entity-counts';
	/*
	 * Where to move files, asked before the disk is touched: once for the whole selection, every
	 * time, with the count and a sentence saying it moves the file on your disk.
	 */
	import { ConfirmDialog, Field, Select } from '$lib/components/common';
	import { disambiguate } from '$lib/library/folder-names';
	import type { components } from '$lib/api/schema';

	export type MoveTarget = Pick<components['schemas']['FolderView'], 'id' | 'name'> & {
		path?: string;
	};

	interface Props {
		open?: boolean;
		/** Named in every sentence. */
		count: number;
		folders: readonly MoveTarget[];
		onconfirm: (folderId: string) => void;
	}

	let { open = $bindable(false), count, folders, onconfirm }: Props = $props();

	/* Nothing chosen on open: a destination filled in for somebody is not one they picked. */
	let chosen = $state('');

	$effect(() => {
		if (open) chosen = '';
	});

	const things = $derived(count === 1 ? 'this file' : `these ${counted(count)} files`);

	/* Same-named folders are told apart by `disambiguate`. */
	const options = $derived(
		disambiguate(
			folders.map((folder) => ({
				value: folder.id,
				label: folder.name,
				path: folder.path || folder.name
			}))
		)
	);
</script>

<ConfirmDialog
	bind:open
	title={count === 1 ? 'Move this file?' : `Move ${counted(count)} files?`}
	consequence={`This moves ${things} on your disk. Sift keeps track of where ${count === 1 ? 'it goes' : 'they go'}, and the move can be undone from the file's own screen.`}
	confirmLabel="Move"
	destructive={false}
	confirmDisabled={chosen === ''}
	onconfirm={() => onconfirm(chosen)}
>
	{#snippet extra()}
		<div class="where">
			<Field label="Move to">
				{#snippet control({ id, describedBy })}
					<Select {id} bind:value={chosen} {describedBy} {options} placeholder="Choose a folder" />
				{/snippet}
			</Field>
		</div>
	{/snippet}
</ConfirmDialog>

<style>
	.where {
		margin-block-end: var(--space-5);
	}
</style>
