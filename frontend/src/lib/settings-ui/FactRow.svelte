<script lang="ts">
	/* A row that states something, rather than one that holds a value or does a thing. */
	import type { Snippet } from 'svelte';

	import LabelledRow from '$lib/components/common/LabelledRow.svelte';

	interface Props {
		label: string;
		help?: string;
		/** The fact, as text. Use the children snippet instead when it needs a link or emphasis. */
		fact?: string;
		/** The fact, as markup. Ignored when absent. */
		children?: Snippet;
		/** The fact under its label, the row's whole width: for a sentence rather than a value. */
		stacked?: boolean;
	}

	let { label, help, fact, children, stacked = false }: Props = $props();
</script>

<LabelledRow {label} {help} looseColumn={!stacked} {stacked}>
	{#if children}{@render children()}{:else}<span class="fact" class:sentence={stacked}>{fact}</span
		>{/if}
</LabelledRow>

<style>
	.fact {
		color: var(--sift-ink-2);
		font: var(--text-body);
	}

	.fact.sentence {
		flex: 1 1 auto;
	}
</style>
