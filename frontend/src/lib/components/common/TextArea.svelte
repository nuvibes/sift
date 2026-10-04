<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'TextArea',
		category: 'control',
		role: 'several lines of text typed into a box that grows: notes, a list of names, a template',
		basis: 'site:<textarea>',
		states: ['empty', 'typed', 'invalid', 'disabled']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: a text area is the site's, for the reason `TextInput` gives: the library
	   has nothing to add to typing several lines, and `app.css` dresses the element once. */

	/*
	 * The one box for several lines.
	 *
	 * `inline-size: 100%` is load-bearing for every `<textarea>`: a textarea sizes itself by
	 * `cols`, not by its column, so an undressed one stops short of the box it sits in. Written
	 * here once rather than on every screen that wants several lines.
	 */
	import type { HTMLTextareaAttributes } from 'svelte/elements';

	interface Props extends Omit<HTMLTextareaAttributes, 'value'> {
		/** What has been typed. Bindable. */
		value?: string;
		/** The element, for a caller that has to focus it. Bindable. */
		element?: HTMLTextAreaElement | null;
		/** From a `Field`'s control snippet: what describes this box, and whether it is wrong. */
		describedBy?: string;
		invalid?: boolean;
	}

	let {
		value = $bindable(''),
		element = $bindable(null),
		describedBy,
		invalid = false,
		class: extra = '',
		...rest
	}: Props = $props();
</script>

<textarea
	class="text-area {extra}"
	aria-describedby={describedBy}
	aria-invalid={invalid ? 'true' : undefined}
	bind:value
	bind:this={element}
	{...rest}></textarea>

<style>
	/* `inline-size: 100%` is load-bearing, not tidy: a textarea sizes itself by `cols` and not by
	   the column it sits in, so without this it stops short of the box around it. The rest of the
	   look (the minimum height, the vertical resize, the inset) is app.css's. */
	.text-area {
		inline-size: 100%;
		min-inline-size: 0;
	}
</style>
