<script lang="ts">
	/* Recently viewed: everything this account has opened, newest first. */
	import AssetGrid from '$lib/components/AssetGrid.svelte';
	import { untrack } from 'svelte';
	import { page } from '$app/state';
	import WallControls from '$lib/components/entity/WallControls.svelte';
	import { WallWords, emptyWallSays, wordsIn } from '$lib/components/shell/wall-words';

	/* Constant, and deliberately not read from the address: Browse is the screen whose query is a
	   link, and `/recent?viewed=no` would be an address saying the opposite of the word above it. */
	const QUERY = { viewed: 'yes' };

	/* The page's own search, the box every wall wears (`WallControls`), searching THIS screen. */
	let term = $state(wordsIn(page.url));
	const words = new WallWords();
	const typedHere = $derived(wordsIn(page.url));
	$effect(() => {
		const arrived = typedHere;
		untrack(() => {
			if (words.echoed(arrived)) return;
			term = arrived;
		});
	});
</script>

<AssetGrid
	query={QUERY}
	fixedSort="viewed"
	icon="history"
	title="Recently viewed"
	empty={emptyWallSays(
		'recently viewed files',
		typedHere,
		false,
		'Nothing opened yet. Whatever you play or look at turns up here.'
	)}
>
	{#snippet tools()}
		<WallControls
			noun="file"
			plural="recently viewed"
			bind:term
			onsettled={(typed) => words.write(page.url, typed)}
		/>
	{/snippet}
</AssetGrid>
