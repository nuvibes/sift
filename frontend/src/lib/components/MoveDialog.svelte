<script lang="ts">
	import { counted } from '$lib/entity/entity-counts';
	/*
	 * Where to move files, asked before the disk is touched: once for the whole selection, every
	 * time, with the count and a sentence saying it moves the file on your disk.
	 */
	import { ConfirmDialog, Field, Select } from '$lib/components/common';
	import CopiesChoice from '$lib/components/CopiesChoice.svelte';
	import { copiesOf, type FileCopy } from '$lib/organize/copies';
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
		/** The one file being moved, whose copies are offered when it is stored in more than one place. */
		assetId?: string;
		/** The folder, and the copy chosen (null when there was nothing to choose). */
		onconfirm: (folderId: string, locationId: string | null) => void;
	}

	let { open = $bindable(false), count, folders, assetId, onconfirm }: Props = $props();

	/* Only the copies that can be moved are offered; the server refuses the rest anyway. */
	let copies = $state<FileCopy[]>([]);
	let copy = $state<string | null>(null);

	$effect(() => {
		copies = [];
		copy = null;
		if (!open || assetId === undefined) return;
		const asked = assetId;
		void copiesOf(asked)
			.then((found) => {
				if (asked !== assetId) return;
				copies = found.filter((one) => one.reason === null);
				copy = copies.length > 1 ? copies[0].location_id : null;
			})
			.catch(() => {
				// Moved without a copy named: the server's sentence says why it was not.
			});
	});

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
	onconfirm={() => onconfirm(chosen, copy)}
>
	{#snippet extra()}
		<div class="where">
			<CopiesChoice {copies} bind:value={copy} label="Which copy to move" />
			<Field label="Move to">
				{#snippet control({ id, describedBy })}
					<Select {id} bind:value={chosen} {describedBy} {options} placeholder="Choose a folder" />
				{/snippet}
			</Field>
		</div>
	{/snippet}
</ConfirmDialog>

<style>
	/* The copy, when there is one to choose, above where it goes. */
	.where {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
		margin-block-end: var(--space-5);
	}
</style>
