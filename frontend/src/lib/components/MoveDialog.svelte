<script lang="ts">
	import { counted } from '$lib/entity/entity-counts';
	/*
	 * Where to move files to, asked before anything on the disk is touched.
	 *
	 * Moving changes a disk rather than a row, so it is asked the way a delete is: a sheet that
	 * names the count, states the consequence in a sentence, and cannot be answered by a stray
	 * keystroke.
	 *
	 * It asks once for the whole selection, even across several folders: twelve files in five
	 * folders is one intention, and five questions would be five chances to scatter them again. One
	 * question, not two: a move leaves every byte where it can be found, so a second question would
	 * be ceremony.
	 *
	 * Asked every time. "It moves the file on your disk" is the one thing somebody can be wrong
	 * about, so it is said in the sentence under the question on every move, with no way to turn
	 * the asking off: a question that can be turned off is not a protection.
	 */
	import { ConfirmDialog, Field, Select } from '$lib/components/common';
	import { disambiguate } from '$lib/library/folder-names';
	import type { components } from '$lib/api/schema';

	export type MoveTarget = Pick<components['schemas']['FolderView'], 'id' | 'name'> & {
		path?: string;
	};

	interface Props {
		open?: boolean;
		/** How many files this is about. Named in every sentence: "move these?" with no number is
		 * how somebody moves thirty files meaning to move one. */
		count: number;
		folders: readonly MoveTarget[];
		onconfirm: (folderId: string) => void;
	}

	let { open = $bindable(false), count, folders, onconfirm }: Props = $props();

	/* Nothing is chosen when it opens, and the button is refused until something is. A destination
	   filled in for somebody is a destination they did not pick, and this writes to a disk. */
	let chosen = $state('');

	$effect(() => {
		if (open) chosen = '';
	});

	const things = $derived(count === 1 ? 'this file' : `these ${counted(count)} files`);

	/*
	 * The destinations, named and then told apart. In a library where nine folders are called
	 * Images, full paths would be nine long near-identical strings, so `disambiguate` keeps the
	 * name as the label and adds only the part of the path that tells this one apart, drawn quietly
	 * by `Select`.
	 */
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
