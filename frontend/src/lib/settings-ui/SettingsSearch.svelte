<script lang="ts">
	/*
	 * The box you type into to find a setting. The box, and nothing else.
	 *
	 * ## Why what it FINDS is not drawn here
	 *
	 * Drawn under the box, the results would grow the grid row the box sits in, and the pane on
	 * the right starts on the row BELOW that one, so with ten results up the pane would be pushed
	 * most of the way off the bottom of the panel by a list in the column beside it, and Settings
	 * would fold up while somebody typed.
	 *
	 * The results belong where the section list is, because they are what stands in its place, so
	 * the shell draws them, in the shell's own bounded, scrolling column, dressed as the rows they
	 * replace. This box publishes what was typed and stops there.
	 *
	 * ## Ctrl-F belongs to whoever is on top
	 *
	 * Ctrl-F is the library's search everywhere else in Sift, and while Settings is open that is
	 * the wrong box: it opens a sheet OVER the panel to search files, when the thing in front of
	 * you is a screen full of settings. So this claims the key while it exists, on the DOCUMENT,
	 * which is reached before the window, where the library's sheet listens, and marks the event
	 * answered. That is the mechanism `$lib/shell/layers` already documents for Escape, used for the same
	 * reason.
	 *
	 * On a phone with a section open this box is not drawn, and claiming would focus something
	 * invisible. It cannot arise: the only way to press Ctrl-F is a keyboard, and the layout that
	 * hides this box is the one below 768px.
	 */
	import { NarrowBox } from '$lib/components/common';
	import { matches } from '$lib/shell/shortcuts';

	interface Props {
		/**
		 * What has been typed. Bindable, because the shell is what turns it into results.
		 *
		 * Read upwards rather than the results being drawn in here: see the head of this file for
		 * what would happen if they were.
		 */
		typed?: string;
		/**
		 * The result the arrows have landed on, for the input to announce.
		 *
		 * The results are the shell's (see the head of this file), so the id is handed in rather
		 * than worked out here. Absent while nothing is picked, which is the honest state and is what
		 * `aria-activedescendant` wants: an empty string points at an element with no id.
		 */
		activeId?: string;
		/**
		 * What the box is called, and what it says while empty. "Search settings" in Settings.
		 *
		 * A prop because the box is borrowed: the cookies sheet searches its list of Sites with this
		 * same component rather than a second drawing of it, and a box over a list of Sites that
		 * announced itself as searching settings would be telling a screen reader the wrong thing.
		 */
		label?: string;
		/**
		 * The name of the row a pasted settings path just opened, while the moment lasts.
		 *
		 * A paste IS the choice: the pane moves and the box empties at once, which alone reads as
		 * the paste having been dropped. So for as long as the row's ring lasts the box says where
		 * it went, in its own place (the words it shows while empty) and to a screen reader, and
		 * its edge takes the accent once. The shell decides when; the box only says it.
		 */
		landed?: string;
	}

	let { typed = $bindable(''), activeId, label = 'Search settings', landed }: Props = $props();

	/* "Opened How often": the verb a result's press is, and the row's own name. */
	const said = $derived(landed === undefined ? label : `Opened ${landed}`);

	/*
	 * The field itself, so Ctrl-F can put the caret in it.
	 *
	 * Read out of the box AROUND it rather than bound to it. `NarrowBox` publishes what was typed
	 * and not the element it was typed into, and the one element in here is the one this file has
	 * put there, so there is nothing to be ambiguous about, and nothing is reached into that this
	 * component does not own.
	 */
	let wrap = $state<HTMLElement | null>(null);
	const box = () => wrap?.querySelector('input') ?? null;

	/*
	 * ESCAPE AND THE CROSS ARE `NarrowBox`'s, so every box that narrows a list has them.
	 *
	 * Over a box with text in it the box answers Escape by emptying it and taking the key, so the
	 * one press does not also reach the panel behind and close Settings. Over an EMPTY box it does
	 * nothing and the key goes on to the panel, which closes: Escape's meaning everywhere else in
	 * Sift, and the second press of the two a list inside a menu is given.
	 */

	/*
	 * Ctrl-F, while Settings is up, means THIS box.
	 *
	 * Selected as well as focused, so a second press over a box that already has something in it
	 * replaces rather than appends: the behaviour every find box has.
	 */
	function claimFind(event: KeyboardEvent) {
		if (!matches(event, 'app.search')) return;
		event.preventDefault();
		box()?.focus();
		box()?.select();
	}
</script>

<svelte:document onkeydown={claimFind} />

<!--
	`NarrowBox`, which is the one box in Sift that narrows a list as you type, and that is exactly
	what this does: the shell draws the matches in the section list's place.

	Not a `Field` with its label hidden: `gate:settings-row` counts a stacked `Field` under
	`settings-ui` because a pane is a column of ROWS, and a box that narrows a list is a shape the
	app already has a component for.

	The label stays in the accessible tree (`NarrowBox` puts it on the element), so this is not a
	box a screen reader can only call "edit text".

	The cross inside it and what Escape does are the box's own (see the note above `claimFind`). The
	element around it is only a handle: Ctrl-F reaches the field through it.
-->
<div bind:this={wrap} class:landed={landed !== undefined}>
	<NarrowBox
		{label}
		inset="row"
		aria-activedescendant={activeId}
		placeholder={said}
		title={landed === undefined ? undefined : said}
		spellcheck="false"
		bind:value={typed}
	/>
	<span class="unseen" aria-live="polite">{landed === undefined ? '' : said}</span>
</div>

<style>
	/* The paste landed: the box wears the found row's own mark (`sift-found`, the accent ground
	   that lets go) for one ambient pace, so the box and the rung row read as one answer. The
	   placeholder is the usual quiet ink, so the ground is what catches the eye. */
	.landed :global(.narrow) {
		animation: sift-found var(--dur-ambient) var(--ease);
	}

	/* A row's name can be longer than the box: whole on the box's tooltip for as long as it says
	   it, and cut with an ellipsis once the caret leaves (a browser clips a focused box's words
	   rather than ellipsize them). */
	.landed :global(.narrow) {
		text-overflow: ellipsis;
	}
</style>
