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
	/*
	 * The group a set of `ChoiceCard`s is picked from.
	 *
	 * ## Why the group is the library's
	 *
	 * A `<div role="radiogroup">` around cards that each carry their own click handler has none of
	 * the group's behaviour: the arrow keys do not move between them, the group has no single
	 * value, and a card knows whether it is chosen only because its host compares two strings for
	 * it. The library's RadioGroup is exactly that behaviour: one value, arrow keys between the
	 * items, focus on the chosen one, and the roles written correctly.
	 *
	 * ## What the caller says
	 *
	 * The value, and what to do when it changes. The layout (a grid of cards or a column of them)
	 * is the group's, so a host does not hand a class in and dress the group from outside.
	 */
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

	/* Four cards are two rows of two, never three and one left over: a track never narrower than
	   half the row, so a third never fits beside two. A column of one still takes over where two
	   do not fit. */
	/* The cards are the caller's, so the compiler cannot see them: the count is matched globally. */
	.choices.grid:has(> :global(:nth-child(4):last-child)) {
		grid-template-columns: repeat(auto-fit, minmax(max(220px, calc(50% - var(--space-3))), 1fr));
	}
</style>
