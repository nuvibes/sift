<script lang="ts">
	/* Which copy of a file stored in more than one place a verb acts on: its file name, then its
	   folder, one chosen. Drawn only where there is a choice to make. */
	import { ChoiceCard, ChoiceGroup } from '$lib/components/common';
	import { partsOf, type FileCopy } from '$lib/organize/copies';

	interface Props {
		copies: FileCopy[];
		/** The chosen copy's `location_id`. Bindable. */
		value: string | null;
		/** The question, in the words "Which copy to keep" set: "Which copy to move". */
		label: string;
	}

	let { copies, value = $bindable(), label }: Props = $props();
</script>

{#if copies.length > 1}
	<ChoiceGroup {label} layout="column" value={value ?? ''} onchange={(next) => (value = next)}>
		{#each copies as copy (copy.location_id)}
			{@const parts = partsOf(copy.path)}
			<ChoiceCard value={copy.location_id} name={parts.name} note={parts.folder} />
		{/each}
	</ChoiceGroup>
{/if}
