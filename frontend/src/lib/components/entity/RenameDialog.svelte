<script lang="ts">
	/*
	 * Renaming a named thing: a confirmation with a box in it rather than a sheet of its own.
	 *
	 * A name here is shared vocabulary (it changes what everybody's screens and searches say), so
	 * the sentence that says so is the same thing `ConfirmDialog` exists to make unskippable. Every
	 * wall renames its rows here (`EntityWallFlows`), and so does an artist, whose rename reaches
	 * every song crediting them. One dialog, so the two read and behave as one act; what differs is
	 * the sentence under the title, which is the caller's to say.
	 *
	 * NOT ON THE GALLERY: it is `ConfirmDialog` with one `Field` in it, both of which the gallery
	 * draws; it adds a box and a refusal of an empty name, and nothing to look at.
	 */
	import { ConfirmDialog, Field, TextInput } from '$lib/components/common';

	interface Props {
		open: boolean;
		/** The name as it is now, for the title. */
		named: string;
		/** What renaming it does, said under the title. */
		consequence: string;
		/** The name being typed. */
		value: string;
		/** The longest name the route takes, so the box stops where the server does. */
		maxlength: number;
		onconfirm: () => void;
	}

	let {
		open = $bindable(false),
		named,
		consequence,
		value = $bindable(''),
		maxlength,
		onconfirm
	}: Props = $props();
</script>

<ConfirmDialog
	bind:open
	title={named ? `Rename "${named}"?` : 'Rename'}
	{consequence}
	confirmLabel="Save"
	destructive={false}
	confirmDisabled={value.trim().length === 0}
	{onconfirm}
>
	{#snippet extra()}
		<Field label="Name">
			{#snippet control({ id, describedBy })}
				<TextInput {id} bind:value {maxlength} autocomplete="off" {describedBy} />
			{/snippet}
		</Field>
	{/snippet}
</ConfirmDialog>
