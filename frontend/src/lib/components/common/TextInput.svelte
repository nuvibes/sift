<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'TextInput',
		category: 'control',
		role: 'a single line of text typed into a box: a name, an address, a search',
		basis: 'site:<input>',
		states: ['empty', 'typed', 'invalid', 'disabled', 'readonly']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: a text box is the site's. There is no text input in the library because
	   there is nothing for one to add: typing, selection, the caret, autofill and the keyboard are
	   the browser's, and `app.css` dresses the element once for the whole app. */

	/*
	 * The one text box.
	 *
	 * ## Why a component for an element the stylesheet already dresses
	 *
	 * A bare `<input>` inside a `Field`'s control snippet copies out the same four attributes (the
	 * id, the description, the invalid mark, the autocomplete) and is tempted to dress the box
	 * again in its own stylesheet. The look is not the problem; the stylesheet has that. The
	 * problem is that a bare element is a thing every file writes slightly differently, and a gate
	 * cannot tell a text box from a slider from a file picker by the tag alone. Naming it makes it
	 * one thing: the ratchet counts a bare `<input>` outside the primitives.
	 *
	 * ## What it forwards
	 *
	 * Everything. The value and the element are bindable; every other attribute and handler an
	 * `<input>` takes goes straight through, so a caller loses nothing by naming the box. `class` is
	 * taken out of the spread and added to the box's own: a spread applied after the literal
	 * `class` would replace it; `caller-class.test.ts` refuses it.
	 */
	import type { HTMLInputAttributes } from 'svelte/elements';

	interface Props extends Omit<HTMLInputAttributes, 'value' | 'type'> {
		/** What has been typed. Bindable. */
		value?: string;
		/** The element, for a caller that has to focus or measure it. Bindable. */
		element?: HTMLInputElement | null;
		type?: 'text' | 'search' | 'password' | 'email' | 'url' | 'number' | 'tel';
		/** From a `Field`'s control snippet: what describes this box, and whether it is wrong. */
		describedBy?: string;
		invalid?: boolean;
	}

	let {
		value = $bindable(''),
		element = $bindable(null),
		type = 'text',
		describedBy,
		invalid = false,
		class: extra = '',
		...rest
	}: Props = $props();
</script>

<input
	class="text-input {extra}"
	{type}
	aria-describedby={describedBy}
	aria-invalid={invalid ? 'true' : undefined}
	bind:value
	bind:this={element}
	{...rest}
/>

<style>
	/* The look is app.css's: height, inset, edge, corner, ground and face, once for every box. What
	   is here is the one thing the stylesheet cannot say for a box in a column: fill the column. */
	.text-input {
		inline-size: 100%;
		min-inline-size: 0;
	}
</style>
