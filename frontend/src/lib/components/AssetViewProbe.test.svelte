<script lang="ts" module>
	/* A harness for `AssetView` whose `id` can MOVE.
	 *
	 * A prop handed to `mount` from a test file is a value, not a binding: nothing in a `.test.ts`
	 * can change it afterwards. What is under test here is precisely what happens when it changes
	 * (stepping to the next file with a pane already open) so the id has to live somewhere the test
	 * can write to and the component can read reactively. This is that place.
	 *
	 * Module-level so a test can reach it without a handle on the instance, and set back in the
	 * test's own setup rather than here: one harness, one file, and the case says which file it is
	 * looking at.
	 */
	export const showing = $state({ id: 'a-1' });

	/*
	 * And what the view reported when its file went. A popout's frame decides whether to step on or
	 * close; this harness is not a frame, so it only records that it was told, the half `AssetView`
	 * owns.
	 */
	export const wentAway = $state({ ids: [] as string[] });
</script>

<script lang="ts">
	import AssetView from './AssetView.svelte';
</script>

<AssetView id={showing.id} ongone={(gone) => wentAway.ids.push(gone)} />
