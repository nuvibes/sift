<script lang="ts">
	/* DRESSED BY: KeptPill (the pill, the bubble, the three dots and the right-click are all its:
	   this file declares what a saved FILTER's verbs are and what its bubble draws). */

	/*
	 * One kept filter, as a pill.
	 *
	 * How it looks and behaves is `KeptPill`, shared with saved walls, which are the same object: a
	 * named piece of setting-up somebody keeps, presses to put on, and otherwise renames, updates
	 * or deletes.
	 *
	 * What stays here is what is about filters: the four verbs, and a filter's bubble drawing the
	 * bar's own chips, one per value as the bar does, so none is cut short.
	 */
	import KeptPill from '$lib/components/common/KeptPill.svelte';
	import FilterChip from './FilterChip.svelte';
	import { partsOf } from '$lib/search/query-parts';
	import type { Verb } from '$lib/components/common/verbs';
	import type { SavedSearch } from '$lib/search/saved-searches.svelte';

	interface Props {
		kept: SavedSearch;
		onapply: (kept: SavedSearch) => void;
		onupdate?: (kept: SavedSearch) => void;
		onedit?: (kept: SavedSearch) => void;
		onrename?: (kept: SavedSearch) => void;
		onremove?: (kept: SavedSearch) => void;
		/** A write against this one is in flight. See `KeptPill.busy`, which is what draws it. */
		busy?: boolean;
	}

	let { kept, onapply, onupdate, onedit, onrename, onremove, busy = false }: Props = $props();

	const parts = $derived(partsOf(kept.query));

	/*
	 * Whether it was kept on ONE USERNAME's wall (`?username=`). Not a word of the query language, so
	 * `partsOf` does not read it, and a filter holding only a username would otherwise describe
	 * itself as "Everything". Said as "one username" rather than by name: the kept query holds the
	 * id and nothing else, and the wall it opens names the username on its own chip.
	 */
	const onAUsername = $derived(new URLSearchParams(kept.query).has('username'));

	const verbs = $derived.by(() => {
		const rows: Verb[] = [];
		if (onedit) {
			rows.push({
				id: 'edit',
				label: 'Edit',
				icon: 'edit',
				group: 'change',
				singleOnly: true,
				run: () => onedit(kept)
			});
		}
		if (onrename) {
			rows.push({
				id: 'rename',
				label: 'Rename',
				icon: 'edit_square',
				group: 'change',
				singleOnly: true,
				run: () => onrename(kept)
			});
		}
		if (onupdate) {
			rows.push({
				id: 'update',
				label: 'Update from current filters',
				icon: 'sync',
				group: 'change',
				singleOnly: true,
				run: () => onupdate(kept)
			});
		}
		if (onremove) {
			rows.push({
				id: 'remove',
				label: 'Delete',
				icon: 'delete',
				destructive: true,
				singleOnly: true,
				run: () => onremove(kept)
			});
		}
		return rows;
	});
</script>

<!-- The funnel before the name, the mark this application uses for filtering wherever it appears
     (the bar's control, a theater cell's own filter, and the things somebody kept). A row of pills
     reading `r4t`, `gym`, `223` says nothing about what sort of thing they are; one glyph in front
     of each says it for all of them. -->
<KeptPill
	id={kept.id}
	name={kept.name}
	icon="filter_alt"
	{verbs}
	{busy}
	onapply={() => onapply(kept)}
	holds={parts.length > 0 || onAUsername ? chips : undefined}
	nothing="Everything"
/>

{#snippet chips()}
	{#if onAUsername}
		<FilterChip field="username" values={['one username']} />
	{/if}
	{#each parts as part, at (at)}
		{#each part.values as one, nth (nth)}
			<FilterChip
				field={part.field}
				values={[one]}
				of={part.values.length}
				all={part.all}
				excluded={part.excluded}
				matchShown
			/>
		{/each}
	{/each}
{/snippet}
