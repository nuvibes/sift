<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'ChoiceGroup',
		category: 'control',
		role: 'one of a few choices, picked like radio buttons: the group the cards sit in',
		basis: 'bits-ui:RadioGroup',
		states: ['grid', 'column']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* The group ChoiceCards are picked from: the library's RadioGroup, for one value and arrow keys;
	 * the layout is the group's, never dressed from outside. */
	import type { Snippet } from 'svelte';
	import { RadioGroup } from 'bits-ui';

	interface Props {
		/** Which one is chosen. Bindable. */
		value?: string;
		onchange?: (value: string) => void;
		/** Names the group for a screen reader: "Background", "What to do with these files". */
		label: string;
		/** Cards in a wrapping grid (the default), or stacked in one column. */
		layout?: 'grid' | 'column';
		disabled?: boolean;
		children: Snippet;
	}

	let {
		value = $bindable(''),
		onchange,
		label,
		layout = 'grid',
		disabled = false,
		children
	}: Props = $props();
</script>

<RadioGroup.Root
	bind:value
	onValueChange={(next: string) => onchange?.(next)}
	{disabled}
	aria-label={label}
	orientation={layout === 'column' ? 'vertical' : 'horizontal'}
>
	{#snippet child({ props })}
		<div {...props} class="choices {layout}">
			{@render children()}
		</div>
	{/snippet}
</RadioGroup.Root>

<style>
	.choices {
		display: grid;
		gap: var(--space-3);
	}

	.choices.grid {
		grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
	}

	/* Four cards are two rows of two; global, as the cards are the caller's. */
	.choices.grid:has(> :global(:nth-child(4):last-child)) {
		grid-template-columns: repeat(auto-fit, minmax(max(220px, calc(50% - var(--space-3))), 1fr));
	}
</style>
