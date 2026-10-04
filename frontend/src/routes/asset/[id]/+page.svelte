<script lang="ts">
	/*
	 * One asset, opened at its own address.
	 *
	 * There is no page form of this. An asset is a panel over the library, and that is true whether
	 * it was clicked in a grid or arrived as a pasted link, so the same address never looks like
	 * two different screens.
	 *
	 * Clicking a tile never runs this route: it pushes the address and the grid stays mounted
	 * underneath. This runs when the address is ARRIVED AT (cold, refreshed, or by following a
	 * plain link to it from inside the app), and all it does is put the panel up at the address
	 * that is already correct.
	 *
	 * Where closing goes is not the same answer for those two arrivals. See `enterAsset`: cold,
	 * there is nothing behind the panel and closing leaves for the library; followed from a screen,
	 * that screen is one step back and closing returns to it. `isFirstScreen` tells them apart: it
	 * answers from the record of every address this document has shown, which the layout keeps (see
	 * `navigation.svelte.ts`).
	 *
	 * ## `onMount`, NOT `afterNavigate`
	 *
	 * `afterNavigate` runs at the END of a navigation, for whatever registered one by then, and
	 * this route does not exist by then on a cold load, because the layout draws its children only
	 * once the session has come back from the server. So a cold or refreshed `/asset/<id>` would
	 * draw the shell and no panel. `onMount` runs whenever the route is drawn, however late that
	 * is, which is the one moment this has to act.
	 *
	 * WHY NOT FRAMED: this route draws nothing at all. The whole of what is on screen is the panel,
	 * put up by the layout, and the frame is the shape of a PAGE: a header, a body that scrolls,
	 * a foot. A frame here would be an empty one behind the popout, wearing the page's inset around
	 * no content.
	 */
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { enterAsset } from '$lib/player/asset-view';
	import { isFirstScreen } from '$lib/shell/navigation.svelte';

	/* A moment out of the address, or nothing.
	 *
	 * Anything that is not a whole number of milliseconds at or after zero is nothing rather than
	 * a guess: a hand-edited or truncated address should open the file at the start, not throw.
	 *
	 * Named, because there are two: `t` is where to open and `until` is where the stretch ends,
	 * which a saved loop carries so that its address opens the LOOP rather than the video it was
	 * cut from. One reader for both, or the second would grow its own idea of what a malformed
	 * number means.
	 */
	function momentFromAddress(query: URLSearchParams, name = 't'): number | null {
		const raw = query.get(name);
		if (raw === null) return null;
		const ms = Number(raw);
		return Number.isFinite(ms) && ms >= 0 ? Math.floor(ms) : null;
	}

	/* No neighbours are set up, deliberately: there was no list behind this address to move through.
	 * A refresh has always lost the sequence, and inventing one from the library would step somebody
	 * through files they were not looking at. */
	onMount(() => {
		enterAsset(
			page.params.id ?? '',
			momentFromAddress(page.url.searchParams) ?? undefined,
			momentFromAddress(page.url.searchParams, 'until') ?? undefined,
			{ cold: isFirstScreen(page.url) }
		);
	});
</script>

<!-- Nothing. What is drawn is the panel, by the layout, once the two steps above have run. -->
