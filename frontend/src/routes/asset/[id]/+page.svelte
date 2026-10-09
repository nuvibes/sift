<script lang="ts">
	/*
	 * One asset, opened at its own address.
	 * WHY NOT FRAMED: this route draws nothing at all.
	 */
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { enterAsset } from '$lib/player/asset-view';
	import { isFirstScreen } from '$lib/shell/navigation.svelte';

	/* A moment out of the address, or nothing. */
	function momentFromAddress(query: URLSearchParams, name = 't'): number | null {
		const raw = query.get(name);
		if (raw === null) return null;
		const ms = Number(raw);
		return Number.isFinite(ms) && ms >= 0 ? Math.floor(ms) : null;
	}

	/* No neighbours are set up, deliberately: there was no list behind this address to move
	 * through. */
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
