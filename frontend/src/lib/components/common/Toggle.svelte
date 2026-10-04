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
	/*
	 * A control pressed to be one way or the other, which holds which.
	 *
	 * The library's Toggle owns a `pressed` state and hands back the props (`aria-pressed`, the
	 * keyboard, the click); it does not decide what the pressed thing looks like. A run of
	 * monospace text that shows the rest of an address when pressed is not a bordered button, and
	 * the shared `Button` (with `pressed` for icon toggles on a bar) would give it a control's
	 * shape. So this renders a bare button and takes the caller's class, dressed in the caller's
	 * own file through an anchored `:global`.
	 *
	 * Not `Heart` (the server owns whether something is a favourite; a toggle holding its own
	 * answer cannot be corrected) and not `Switch` (a setting with a label, on or off). The fence
	 * keeps the library inside the primitives, so this is the door to it (used by the exit-address
	 * line in Settings).
	 */
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
