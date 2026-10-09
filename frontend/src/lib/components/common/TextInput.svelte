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
	there is nothing for one to add, and app.css dresses the element once. */

	/* The one text box, named so a gate can count bare inputs. Everything is forwarded; `class` is
	 * merged, never spread over (caller-class.test.ts). */
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
	/* The look is app.css's; here the box fills its column. */
	.text-input {
		inline-size: 100%;
		min-inline-size: 0;
	}
</style>
