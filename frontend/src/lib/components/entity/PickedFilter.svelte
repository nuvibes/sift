<script lang="ts">
	/*
	 * How many cards are picked, beside an entity page's tab words, and the press that clears them.
	 *
	 * Cards are picked on the tabs (see `picks.ts` and `RelatedWall`), and a pick filters the
	 * page's files the moment it is made. This says what a row of washed cards across three tabs
	 * cannot say for itself: that the Files tab is filtered, by how many, and by what (the
	 * tooltip), with one way to take them all off. On the Files tab the bar's chips take them off
	 * one at a time; this is the clear-all, and the only one a card tab has, because a card tab's
	 * bar draws no chips.
	 *
	 * Beside the tab words rather than at the far end, because the picks are gathered across Seen
	 * with, Tags and Sites and the tab strip is the one thing drawn on all of them. It draws
	 * nothing while nothing is picked.
	 *
	 * Reads the address itself rather than being handed the picks, so the five entity pages mount
	 * one line and cannot each derive the count their own way.
	 */
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import Button from '$lib/components/common/Button.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { clearPicks, picksOf } from '$lib/components/entity/picks';
	import { counted } from '$lib/entity/entity-counts';

	const picks = $derived(picksOf(page.url));
	const count = $derived(picks.length);

	/* The names, in the words the tooltip says, so what is filtering the files is readable without
	   opening the Files tab. */
	const said = $derived(picks.map((one) => one.name).join(', '));

	function clear() {
		// In place, like a pick: a pick is not a place to go Back to, and neither is taking them off.
		void goto(clearPicks(page.url), { replaceState: true, keepFocus: true, noScroll: true });
	}
</script>

{#if count > 0}
	<span class="picked-filter">
		<Tooltip label={`The files are filtered to all of: ${said}`} placement="bottom">
			<Button tone="ghost" size="small" icon="close" onclick={clear}>
				Clear picks <span class="count">{counted(count)}</span>
			</Button>
		</Tooltip>
	</span>
{/if}

<style>
	/* In the tab row, after the words, with the row's own gap before it. */
	.picked-filter {
		display: inline-flex;
		align-items: center;
		margin-inline-start: var(--space-3);
	}

	.count {
		font-variant-numeric: tabular-nums;
	}
</style>
