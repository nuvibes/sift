<script lang="ts">
	/* A ranked list of named things and their figures, each row as the server sent it; a thing the
	 * reader may not see is left out on the server, before ranking. */
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

	// Covers that failed to load, by address: the row then draws the letter. Keyed by address, as
	// rows are kept by place and a reused `Avatar` never re-checks a loaded address.
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
