<script lang="ts">
	/*
	 * The bar over a wall of entities, and the actions on it.
	 *
	 * ## Why this is a component and not four copies
	 *
	 * People, Sites, Collections and Tags are four walls of the same shape, and every one of
	 * them can already share, hide and delete ONE of its rows, from its own context menu, written
	 * separately, four times. Giving each of them a selection bar by hand would be the fifth, sixth,
	 * seventh and eighth copy of the same three sentences, and the four would drift: one would get
	 * an Undo, one would forget to clear the selection after, one would say "3 items" where the
	 * others said "3 people".
	 *
	 * So the bar is one thing. What differs between the walls is which actions apply and what one
	 * row is called, and those are props.
	 *
	 * ## The buttons are not written here either
	 *
	 * They are declared in `$lib/components/entity/verbs` and rendered by the same component the
	 * right-click menus render through. A bar and menus written separately drift apart (a menu
	 * offering a verb the bar does not), and the only durable answer to that is for both to draw
	 * one list.
	 *
	 * ## What it does NOT own
	 *
	 * The writes. Hiding a person and hiding a collection are different endpoints with different
	 * rules, and a component that knew all four would be a switch statement pretending to be a
	 * design. Each wall hands over a function per action it supports; an action with no function is
	 * not offered, which is how Tags gets a short bar and nothing has to say so.
	 */
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
		/** Its plural, where an "s" will not do. */
		plural?: string;
		/** Whether the wall is currently showing hidden rows, so the button says Unhide there. */
		showingHidden?: boolean;
		/** The rating every picked row shares, when they share one. */
		rating?: number | null;
		/** Whether every picked row is already pinned, so the button says Unpin. */
		pinned?: boolean;
		/** Whether every picked row is already a favorite, so the row says Remove from favorites. */
		favorite?: boolean;
		/** One function per action this wall supports. The rest are simply not offered. */
		handlers: EntityVerbHandlers;
		/**
		 * The stash-boxes configured here, for Auto-enrich's row per box: the same list the menus
		 * are handed, so the bar that addresses a selection offers the same flyout. Absent leaves
		 * Auto-enrich a plain press, which asks what Settings says.
		 */
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

	/* The few the bar names and everything else, split by the rule rather than by this file: Add
	   to, the stars and the one that deletes stay worded, and the rest are behind the door at the
	   end. See `barShape`. */
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
	/* Resolved once, here, so every button and every row acts on the same set in the same order.
	   Read from the wall's current order rather than from the order things were clicked in: a bar
	   that says "3 selected" and an action that runs over them should agree with the screen. */
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
