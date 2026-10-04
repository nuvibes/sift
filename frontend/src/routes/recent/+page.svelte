<script lang="ts">
	/*
	 * Recently viewed: everything this account has opened, newest first.
	 *
	 * The same grid Favorites is, with one filter and one order, so it pages the way every other
	 * wall pages, wears the same tile controls, and its pager sits where every other pager sits.
	 *
	 * `viewed=yes` is the query the server already understands (what the Filters panel sends and
	 * what `viewed:` in the box parses to), so this screen adds no language of its own.
	 *
	 * The order is FIXED rather than offered. Recency is not one way of arranging this screen, it
	 * is what the screen is: a "recently viewed" sorted by name would be a different screen wearing
	 * this one's title.
	 *
	 * Per account, like the heart: two people signed into one Sift do not share this screen.
	 */
	import AssetGrid from '$lib/components/AssetGrid.svelte';
	import { untrack } from 'svelte';
	import { page } from '$app/state';
	import WallControls from '$lib/components/entity/WallControls.svelte';
	import { WallWords, emptyWallSays, wordsIn } from '$lib/components/shell/wall-words';

	/* Constant, and deliberately not read from the address: Browse is the screen whose query is a
	   link, and `/recent?viewed=no` would be an address saying the opposite of the word above it. */
	const QUERY = { viewed: 'yes' };

	/*
	 * The page's own search, the box every wall wears (`WallControls`), searching THIS screen. Its
	 * words ride in `q`, the address's free text, which the grid already reads on top of this
	 * screen's own query; the top bar's box searches the whole library, so without this there would
	 * be no way to look for one thing among everything opened lately. Kept in the address like every
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
