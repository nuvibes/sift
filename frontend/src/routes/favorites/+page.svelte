<script lang="ts">
	/* Favorites: everything with a heart on it. */
	import AssetGrid from '$lib/components/AssetGrid.svelte';
	import { FAVORITES_SOURCE } from '$lib/grid/grid.svelte';
	import { untrack } from 'svelte';
	import { page } from '$app/state';
	import WallControls from '$lib/components/entity/WallControls.svelte';
	import { WallWords, emptyWallSays, wordsIn } from '$lib/components/shell/wall-words';

	/* Constant, and deliberately not derived from the address. */
	const QUERY = { fav: 'yes' };

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
	source={FAVORITES_SOURCE}
	query={QUERY}
	icon="favorite"
	title="Favorites"
	empty={emptyWallSays(
		'favorites',
		typedHere,
		false,
		'Nothing here yet. Press the heart on anything you want to find again.'
	)}
	pinnable
>
	{#snippet tools()}
		<WallControls
			noun="favorite"
			plural="favorites"
			bind:term
			onsettled={(typed) => words.write(page.url, typed)}
		/>
	{/snippet}
</AssetGrid>
