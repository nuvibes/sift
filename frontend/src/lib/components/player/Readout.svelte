<script lang="ts" module>
	/* No design declaration: `check_design_entries.js` reads `lib/components/common/` alone. */

	export interface Fact {
		name: string;
		value: string;
	}
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: this is a description list. The site's own <dl>, <dt> and <dd> already
	   pair a name with its value for a screen reader, which is the whole of the semantics here:
	   there is no state, no focus and nothing to operate. */

	/*
	 * Machine facts read down: quiet names, loud tabular values aligned to the end. Beside its one
	 * user.
	 */
	interface Props {
		facts: Fact[];
		label?: string;
	}

	let { facts, label }: Props = $props();
</script>

<dl class="readout" aria-label={label}>
	{#each facts as fact (fact.name)}
		<dt>{fact.name}</dt>
		<dd>{fact.value}</dd>
	{/each}
</dl>

<style>
	.readout {
		display: grid;
		grid-template-columns: auto auto;
		gap: 2px var(--space-3);
		margin: 0;
	}

	dt {
		font: var(--text-label);
		color: var(--sift-ink-3);
	}

	dd {
		margin: 0;
		font: var(--text-data);
		font-variant-numeric: tabular-nums;
		text-align: end;
	}
</style>
