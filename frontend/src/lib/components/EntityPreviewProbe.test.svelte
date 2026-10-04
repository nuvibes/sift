<script lang="ts">
	/* A harness for the entity card: its trigger is a snippet, and a snippet cannot be handed to
	   `mount` from a test file. The anchor here is the one the file dialog's band draws. */
	import EntityPreview from './EntityPreview.svelte';
	import { pageOf, type EntityKind } from '$lib/entity/related.svelte';

	/* `row`, when given, is read the way a band reads its rows (`row.id`), so a test can hand over
	   a fresh object for the same thing, which is what every re-read of a band does. */
	let {
		kind = 'person',
		id = 'p-1',
		name = 'Marisol Vane',
		row
	}: {
		kind?: EntityKind;
		id?: string;
		name?: string;
		row?: { id: string; name: string };
	} = $props();
</script>

{#if row}
	<!-- A bare member read, as the band's chips write it: an expression that is not one is
	     memoised by the compiler and would hide what a re-read does to the card. -->
	<EntityPreview {kind} id={row.id} name={row.name}>
		{#snippet children({ props })}
			<a {...props} class="person" href={pageOf(kind, row.id)}>{row.name}</a>
		{/snippet}
	</EntityPreview>
{:else}
	<EntityPreview {kind} {id} {name}>
		{#snippet children({ props })}
			<a {...props} class="person" href={pageOf(kind, id)}>{name}</a>
		{/snippet}
	</EntityPreview>
{/if}
