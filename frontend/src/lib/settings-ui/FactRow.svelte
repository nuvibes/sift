<script lang="ts">
	/*
	 * A row that states something, rather than one that holds a value or does a thing.
	 *
	 * ## It is `LabelledRow`, like a setting
	 *
	 * The left column is `SettingRow`'s, and so are the two things a hand-drawn grid gets wrong:
	 * the label is semibold (read down a pane, a muted sentence at regular weight looks like a
	 * heading), and the column geometry is shared (every control at a different distance from the
	 * edge makes the pane read as a stack of unrelated things). About and Accounts draw facts
	 * through this, so they get the same row. What is left here is only the one thing a fact needs
	 * that a setting does not.
	 *
	 * ## What that one thing is
	 *
	 * A loose right column. A version number, a licence and a source address are SHORT, and pinning
	 * three of them to the far edge of a fifteen-rem column reads as a table with a hole in the
	 * middle. A fixed column is what makes a stack of CONTROLS line up; a stack of facts wants to
	 * sit beside what it is a fact about.
	 */
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
