<script lang="ts">
	/* How many cards are picked, beside an entity page's tab words, and the press that clears them;
	 * it reads the address itself and draws nothing while nothing is picked. */
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import Button from '$lib/components/common/Button.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { clearPicks, picksOf } from '$lib/components/entity/picks';
	import { counted } from '$lib/entity/entity-counts';

	const picks = $derived(picksOf(page.url));
	const count = $derived(picks.length);

	/* The names, for the tooltip. */
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
