<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'Toggle',
		category: 'control',
		role: 'something pressed to be one way or the other, that holds which way it is',
		basis: 'bits-ui:Toggle',
		states: ['off', 'on']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* A bare button pressed one way or the other, on the library's Toggle; its look is the caller's.
	 * Not Heart (server-owned) or Switch (a labelled setting). */
	import type { Snippet } from 'svelte';
	import { Toggle } from 'bits-ui';

	interface Props {
		/** Which way it is. Bindable. */
		pressed?: boolean;
		onchange?: (pressed: boolean) => void;
		/** Its accessible name, which may say which way it is: "Show the whole address". */
		label: string;
		class?: string;
		children: Snippet;
	}

	let {
		pressed = $bindable(false),
		onchange,
		label,
		class: caller = '',
		children
	}: Props = $props();
</script>

<Toggle.Root bind:pressed onPressedChange={(next: boolean) => onchange?.(next)} aria-label={label}>
	{#snippet child({ props })}
		<!-- Rendered here rather than by the library, so the caller's anchored rules reach it. -->
		<button {...props} class="toggle {caller}">
			{@render children()}
		</button>
	{/snippet}
</Toggle.Root>

<style>
	/* A button with none of a button's dressing: the look is the caller's, by design. */
	.toggle {
		border: 0;
		padding: 0;
		background: none;
		color: inherit;
		font: inherit;
		cursor: pointer;
	}

	.toggle:focus-visible {
		outline: none;
		box-shadow: var(--focus-ring);
	}
</style>
