<script lang="ts" module>
	/* No design declaration: a component beside its one user is not a primitive of the gallery
	   (`scripts/check_design_entries.js` reads `lib/components/common/` alone). See the note below
	   on where it lives. */

	/** One fact: what it is called, and what it says. */
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
	 * A column of machine facts, laid out so they can be read down.
	 *
	 * In `player/`, beside its one user: a component with one user lives next to that user, and
	 * moves to `common/` the day a second screen of facts wants it.
	 *
	 * A component rather than a `<dl>` per screen, because the shape has three decisions that are
	 * easy to get half right: the names are quiet and the values loud; the values are right-aligned
	 * so the column of numbers lines up; and the figures are tabular, since a proportional set
	 * makes the column jitter every time a digit ticks over, which on a panel updating once a
	 * second is the difference between a readout and a fidget.
	 *
	 * The order is the caller's, and should nearly always be alphabetical, as the player's is:
	 * grouped by meaning, a reference panel reads as arbitrary to everybody but whoever grouped it,
	 * and somebody opens one looking for a single line.
	 */
	interface Props {
		facts: Fact[];
		/** Names the block for a screen reader, where it is not already under a heading. */
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

	/*
	 * Tabular, and aligned to the end.
	 *
	 * Both are about the same thing: the values form a column, and a column whose digits are
	 * different widths and whose right edge is ragged is a list rather than a readout.
	 */
	dd {
		margin: 0;
		font: var(--text-data);
		font-variant-numeric: tabular-nums;
		text-align: end;
	}
</style>
