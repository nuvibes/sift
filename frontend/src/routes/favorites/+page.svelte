<script lang="ts">
	/*
	 * Favorites: everything with a heart on it.
	 *
	 * The same grid as Browse with one filter applied, and the filter is the query the server already
	 * understands: `fav=yes` is what the Filters modal sends and what `fav:yes` typed into the box
	 * parses to. So this screen adds no language of its own and there is nothing here that can drift
	 * away from what the search box means by the same word.
	 *
	 * Per account, not per install: a heart belongs to whoever pressed it, and the server scopes the
	 * answer to whoever is asking. Two people signed into one Sift do not share this screen.
	 */
	import AssetGrid from '$lib/components/AssetGrid.svelte';
	import { FAVORITES_SOURCE } from '$lib/grid/grid.svelte';
	import { untrack } from 'svelte';
	import { page } from '$app/state';
	import WallControls from '$lib/components/entity/WallControls.svelte';
	import { WallWords, emptyWallSays, wordsIn } from '$lib/components/shell/wall-words';

	/* Constant, and deliberately not derived from the address. Browse is the screen whose query is a
	 * link; this one IS the query, and taking parameters here would make `/favorites?fav=no` a real
	 * address that says the opposite of the word above it. */
	const QUERY = { fav: 'yes' };

	/*
	 * The page's own search, the box every wall wears (`WallControls`), searching THIS screen. Its
	 * words ride in `q`, the address's free text, which the grid already reads on top of this
	 * screen's own query; the top bar's box searches the whole library, so without this there would
	 * be no way to look for one thing among everything with a heart on it. Kept in the address like every
	 * wall's words (`WallWords`), so Back and a link carry them and the bar's chip can take them off.
	 */
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
