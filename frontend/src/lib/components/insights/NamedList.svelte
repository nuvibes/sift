<script lang="ts">
	/*
	 * A short list of named things with their figures, ranked: the people, Sites, tags,
	 * Collections, Photo Sets and files viewed most, the walls, the tasks.
	 *
	 * Each row is the server's: the thing as a piece carrying its address (so the name is the way to
	 * its page, or opens the file over the screen), its figure, and its picture's address: the one
	 * its own wall draws it from. A picture that does not load is the letter `Avatar` draws for
	 * anything without one; a row with no picture at all (a saved wall, a task) draws none. A thing
	 * this reader may not see never reaches the list: the server leaves it out before ranking.
	 */
	import { SvelteSet } from 'svelte/reactivity';

	import type { components } from '$lib/api/schema';
	import RankedList from '$lib/components/charts/RankedList.svelte';
	import { Avatar } from '$lib/components/common';
	import HistorySentence from '$lib/components/common/HistorySentence.svelte';

	import { saidOf } from '$lib/components/insights/figures';

	type NamedList = components['schemas']['NamedList'];
	type NamedRow = components['schemas']['NamedRow'];

	interface Props {
		list: NamedList;
		/** Lead with the first row, drawn large with its picture (`RankedList`'s `lead`). */
		lead?: boolean;
	}

	let { list, lead = false }: Props = $props();

	const rows = $derived(
		list.rows.map((row) => ({ ...row, said: saidOf(row.said, row.value, row.unit) }))
	);
	const pictured = $derived(list.rows.some((row) => row.cover !== null));

	/*
	 * The covers the browser could not draw. `Avatar` falls back to the letter when its own check of
	 * the address fails, but the picture it then puts on screen can still fail to load, and that
	 * one draws the browser's broken-picture glyph. An error from inside the cover is caught here
	 * and the row is drawn with no address, which is the letter.
	 *
	 * Each cover is keyed by its address. The list keeps its rows by place, so a new period hands a
	 * row's `Avatar` another thing's cover, and the picture library behind it never checks a second
	 * address once the first one loaded: a missing cover would then draw neither picture nor letter.
	 */
	const refused = new SvelteSet<string>();
</script>

{#snippet name(row: NamedRow)}
	<HistorySentence pieces={[row.piece]} />
{/snippet}

{#snippet cover(row: NamedRow)}
	{#if row.cover !== null}
		{@const address = row.cover}
		<span class="picture" onerrorcapture={() => refused.add(address)}>
			<Avatar src={refused.has(address) ? null : address} name={row.piece.text} decorative lazy />
		</span>
	{/if}
{/snippet}

<RankedList title={list.title} {rows} {name} cover={pictured ? cover : undefined} {lead} />

<style>
	/* The cover's own box, so the caught error has somewhere to be heard; it takes no room. */
	.picture {
		display: contents;
	}
</style>
