<script lang="ts">
	/*
	 * Renaming a named thing: a confirmation with a box, since a name is shared vocabulary.
	 * NOT ON THE GALLERY: it is `ConfirmDialog` with one `Field` in it, both of which the gallery
	 * draws.
	 */
	import { ConfirmDialog, Field, TextInput } from '$lib/components/common';

	interface Props {
		open: boolean;
		/** The name as it is now, for the title. */
		named: string;
		/** What renaming it does, said under the title. */
		consequence: string;
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
