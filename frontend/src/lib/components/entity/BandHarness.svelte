<script lang="ts">
	/* An entity header inside the frame it is always drawn in. The shape every entity page takes,
	   minus the page.

	   It exists because the thing worth testing is a JOIN: the header knows which picture, the frame
	   has the room to draw it, and neither half alone proves anything. Rendered on its own, the
	   header says its picture to a context nobody set and there is nothing to look at; rendered on
	   its own, the frame draws no backdrop because nothing has said one. See `shell/backdrop.ts`.

	   Through `BandRelayHarness` rather than straight into a frame, because that is the hop a real
	   page takes and it is the one that could fail: see that file. */
	import BandRelayHarness from './BandRelayHarness.svelte';
	import EntityHeader from './EntityHeader.svelte';

	interface Props {
		name?: string;
		coverAssetId?: string | null;
		coverHref?: string;
		counts?: string;
	}

	let { name = 'Somebody', coverAssetId = null, coverHref, counts }: Props = $props();
</script>

{#snippet identity()}
	<EntityHeader {name} {coverAssetId} {coverHref} {counts} />
{/snippet}

<BandRelayHarness above={identity} />
