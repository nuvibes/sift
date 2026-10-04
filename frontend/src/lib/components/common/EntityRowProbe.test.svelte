<script lang="ts">
	/* A harness for the row, because a snippet cannot be handed to `mount` from a test file: the
	   same arrangement `PickMenuInlineProbe` and `ContextMenuItemProbe` use.

	   The chips are plain spans rather than `Chip`, deliberately: what is being tested is the ROW
	   (the glyph at its head, whether anything is hidden, and the way out) and a real chip would
	   bring an Avatar fetching pictures into a test about widths. */
	import EntityRow from './EntityRow.svelte';
	import type { IconName } from '$lib/design/icons';

	interface Props {
		icon?: IconName;
		label?: string;
		words: string[];
		/** Whether the row is handed something to put on its end. */
		adding?: boolean;
	}

	let { icon = 'person', label = 'People', words, adding = false }: Props = $props();
</script>

{#snippet adder()}
	<button type="button" class="probe-adder">Add</button>
{/snippet}

<EntityRow {icon} {label} adder={adding ? adder : undefined}>
	{#each words as word (word)}
		<span class="probe-chip">{word}</span>
	{/each}
</EntityRow>
