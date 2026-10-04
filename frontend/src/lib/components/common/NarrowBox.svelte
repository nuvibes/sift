<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'NarrowBox',
		category: 'control',
		role: 'the small text box that filters the list beside it',
		basis: 'site:<input>; composes:Pressable',
		states: ['empty', 'typed', 'medium']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/*
	 * WHY NOT BITS-UI: a text box is the site's; there is nothing in the library for one to reach.
	 * What is written once here is its dressing and its one job.
	 *
	 * The box that filters a list as you type: one shape wherever a list is filtered, the facet
	 * panel's long columns (people, tags, folders) and the shared Select's searchable list, so the
	 * two read as one application. The words, the corner and the accent ring are the facet panel's.
	 */
	import type { HTMLInputAttributes } from 'svelte/elements';
	import Icon from '$lib/components/Icon.svelte';
	import Pressable from './Pressable.svelte';

	interface Props extends Omit<HTMLInputAttributes, 'value' | 'type' | 'size'> {
		/** What has been typed. Bindable, so the caller filters on it. */
		value: string;
		/** Names the box for a screen reader: "Filter the People list". */
		label: string;
		/**
		 * How tall the box stands. `small` (28px) is the box inside something else: a facet
		 * column, a menu, a pane's list. `medium` is `--control-height`, for a box in a PAGE HEADER
		 * row: the five entity walls put it beside an Add button, and a 28px box next to a 36px
		 * button is two controls that do not line up. The same two words `Button` uses.
		 */
		size?: 'small' | 'medium';
		/**
		 * Where the words start. `field` is a field's own inset. `row` starts them where the rows
		 * of the list under the box start their words (`--space-3` in, the border included), for
		 * a box heading a list of pressable rows, so the typed words and the rows share one edge.
		 */
		inset?: 'field' | 'row';
	}

	/*
	 * `class` is taken OUT of what is spread and added to this box's own, rather than left in
	 * `...rest`.
	 *
	 * A spread is applied in the order it is written, so a `class` arriving inside `{...rest}`
	 * after the literal `class="narrow"` REPLACES it, and what is left is the browser's own
	 * search box, undressed, in the middle of a dark panel. `caller-class.test.ts` refuses that
	 * shape in every primitive.
	 */
	let {
		value = $bindable(''),
		label,
		size = 'small',
		inset = 'field',
		class: extra = '',
		onkeydown,
		...rest
	}: Props = $props();

	let field = $state<HTMLInputElement | null>(null);

	/*
	 * EMPTYING THE BOX IS TYPING INTO IT, and that is the whole of how the cross works.
	 *
	 * The box has two kinds of caller and the cross has to reach both: one binds the value (`Select`,
	 * the walls, Settings) and one listens for `input` and binds nothing (the facet panel, Downloads,
	 * the gallery). Setting `value` here would reach the first and leave the second's list narrowed
	 * under an empty box. So the element is emptied and an `input` event is sent from it, exactly as
	 * a person deleting the text sends one: the binding hears it and so does every `oninput`, and
	 * a caller that asks the server again on each keystroke (the pickers, the log) asks again here.
	 *
	 * The caret goes back into the box: the cross is pressed by somebody about to type something
	 * else, and a cross that dropped focus on the page would make them reach for the box twice.
	 */
	function empty(): void {
		if (!field) return;
		field.value = '';
		field.dispatchEvent(new Event('input', { bubbles: true }));
		field.focus();
	}

	/*
	 * Escape empties a box that has text in it, and does nothing to one that is empty.
	 *
	 * Answered here, before the caller's own handler, and taken when it empties something. That
	 * gives a box inside a list that closes (`Select`, `PickMenu`) its two presses: the first takes
	 * the words away and the list stays up with every row back; the second finds the box empty,
	 * passes through, and whatever owns Escape above (the list, the flyout, the Settings panel)
	 * closes. An empty box leaves the key to the caller, untouched.
	 */
	function keydown(event: KeyboardEvent & { currentTarget: EventTarget & HTMLInputElement }): void {
		if (event.key === 'Escape' && event.currentTarget.value !== '') {
			event.preventDefault();
			event.stopPropagation();
			empty();
			return;
		}
		onkeydown?.(event);
	}
</script>

<!--
	The cross lives inside this box, so every box in Sift that filters a list has the same one: the
	five entity walls, Downloads, the facet panel's columns, the pickers, the searchable Select and
	Settings. A hand already on the mouse wants the cross. The top bar's search keeps its own
	because pressing it clears the chips as well as the words, a different act; it wears the same
	`.field-clear` look.

	The wrapper is position and nothing else, and it is where a caller's `class` lands: a class is
	about where the box sits and how wide it is (Downloads caps its width), a question about the
	words and the cross together, not the text field inside.
-->
<div class="narrow-box {extra}">
	<input
		class="narrow"
		class:medium={size === 'medium'}
		class:row-inset={inset === 'row'}
		type="search"
		placeholder="Type to filter"
		aria-label={label}
		autocomplete="off"
		bind:this={field}
		bind:value
		{...rest}
		onkeydown={keydown}
	/>
	<!--
		Shown only while there is something to empty; a cross in an empty box does nothing.
		`Pressable`, never `Button`: the shared Button's scoped sizing would make this cross a large
		ghost slab (see `.field-clear` in `app.css`). A real `<button>`, so the keyboard reaches it
		and Enter or Space presses it.
	-->
	{#if value}
		<Pressable
			class="field-clear"
			pad="sm"
			feedback="none"
			radius="sm"
			onclick={empty}
			aria-label="Clear the search"
		>
			<Icon name="close" size={16} />
		</Pressable>
	{/if}
</div>

<style>
	/*
	 * The edge is the strong line, not the quiet one. A box for typing into has to look like one
	 * before anybody has typed; a quiet line on `surface-3` inside a `surface-3` panel leaves only
	 * the placeholder saying it is a box. The hover step matches the dashed Tag button beside it
	 * and focus takes the accent: rest, hover, focus, each one step firmer, the family the app's
	 * other fields are in.
	 */
	.narrow-box {
		position: relative;
		display: flex;
		align-items: center;
	}

	/*
	 * The box fills its column unless the caller says how wide it is, and the caller always wins.
	 *
	 * `:where` gives this rule no weight at all. A caller sizes the box with a class of its own
	 * (`.find` on Downloads and the walls), written as `.row :global(.find)`: two classes, the same
	 * weight as `.narrow-box` plus the scope class this file adds. A tie is settled by which
	 * stylesheet the bundle happens to emit last, which is not a rule anybody can read in either
	 * file. At zero weight there is no tie to settle.
	 */
	:where(.narrow-box) {
		inline-size: 100%;
		min-inline-size: 0;
	}

	.narrow {
		inline-size: 100%;
		block-size: 28px;
		padding-inline: var(--space-2);
		/* Room for the cross at the end, always rather than only while it shows, so the words do not
		   jump sideways the moment the first letter lands. The cross is 24 (a 16px glyph and a
		   `--space-1` either side) and sits `--space-2` in from the edge. */
		padding-inline-end: calc(24px + var(--space-2) * 2);
		border: 1px solid var(--sift-line-strong);
		border-radius: var(--radius-md);
		background: var(--sift-surface-3);
		color: var(--sift-ink);
		font: var(--text-label);
		/* The edge steps rather than snapping. */
		transition: border-color var(--dur-instant) var(--ease);
	}

	/* A finger's height on a phone, where every box is (`--control-height`, which the phone block
	   raises): 28 is a mouse's box, and the one box on a pane a finger could miss. */
	@media (max-width: 767px) {
		.narrow {
			block-size: var(--control-height);
		}
	}

	/* Answering the pointer. */
	.narrow:hover {
		border-color: var(--sift-ink-3);
	}

	.narrow::placeholder {
		color: var(--sift-ink-3);
	}

	/* The accent ring, rounded with the box: it is a field being typed into, not a row. */
	.narrow:focus-visible {
		border-color: var(--sift-accent);
	}

	/* The rows' own inset, less the border the rows do not have. */
	.narrow.row-inset {
		padding-inline-start: calc(var(--space-3) - 1px);
	}

	/* A page header's height, so the box lines up with the Add button beside it. */
	.narrow.medium {
		block-size: var(--control-height);
	}

	/*
	 * Over the field rather than beside it, so the cross is inside the box it belongs to. Only
	 * where it sits is written here; the glyph, padding and hover step are `.field-clear` in
	 * `app.css`. `:global`, because the element is `Pressable`'s own. Centred on the box's height
	 * rather than hung from its top, which would sit a 24 cross 2px high in a 28 box.
	 */
	.narrow-box :global(.field-clear) {
		position: absolute;
		inset-inline-end: var(--space-2);
		inset-block: 0;
		margin-block: auto;
	}
</style>
