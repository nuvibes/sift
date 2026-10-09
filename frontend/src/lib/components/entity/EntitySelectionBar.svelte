<script lang="ts">
	/* The bar over a wall of entities, one for the four walls. Verbs come from `entity/verbs`,
	   shared with the menus; each wall hands a function per action, and the rest are not offered. */
	import { ActionBar, VerbButtons, VerbMore, type Selection } from '$lib/components/common';
	import { barShape } from '$lib/components/common/verbs';
	import { entityVerbs, type EntityVerbHandlers } from '$lib/components/entity/verbs';
	import { session } from '$lib/shell/session.svelte';

	interface Props {
		selection: Selection;
		/** The ids on screen, in the order drawn: what `ordered` is resolved against. */
		order: () => string[];
		/** One row, singular. "person", "site", "collection", "tag". */
		noun: string;
		plural?: string;
		/** Whether the wall is currently showing hidden rows, so the button says Unhide there. */
		showingHidden?: boolean;
		rating?: number | null;
		/** Whether every picked row is already pinned, so the button says Unpin. */
		pinned?: boolean;
		/** Whether every picked row is already a favorite, so the row says Remove from favorites. */
		favorite?: boolean;
		/** One function per action this wall supports. The rest are simply not offered. */
		handlers: EntityVerbHandlers;
		/** The stash-boxes, for Auto-enrich's row per box, as the menus have. */
		enrichBoxes?: readonly { word: string; name: string }[];
	}

	let {
		selection,
		order,
		noun,
		plural,
		showingHidden = false,
		rating = null,
		pinned = false,
		favorite = false,
		handlers,
		enrichBoxes = []
	}: Props = $props();

	/* The named few and the rest, split by `barShape`. */
	const shape = $derived(
		barShape(
			entityVerbs({
				isAdmin: session.isAdmin,
				showingHidden,
				rating,
				pinned,
				favorite,
				handlers,
				enrichBoxes
			})
		)
	);
	/* Resolved once, in the wall's order, so every action runs on what the screen shows. */
	const on = $derived(selection.ordered(order()));
</script>

<ActionBar count={selection.count} {noun} {plural} onclear={() => selection.clear()}>
	{#snippet actions()}
		<VerbButtons ids={on} verbs={shape.named} />
	{/snippet}
	{#snippet overflow()}
		<VerbMore ids={on} verbs={shape.rest} {noun} {plural} />
	{/snippet}
</ActionBar>
