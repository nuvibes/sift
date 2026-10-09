<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'EditMarks',
		category: 'composition',
		role: 'one value edited where it stands, with a cross and a tick inside its box to cancel or keep it',
		basis: 'composes:Pressable,Icon',
		states: ['editing', 'keeping', 'a list or a paragraph']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: bits-ui has no such primitive: this is two presses laid inside a field's
	   edge, shape and position only. */

	/* One value edited in place: a cross and a tick inside the field's edge, not a Cancel and Save
	 * beside it; `wide` puts them under a taller control. Enter and Escape are the caller's. */
	import type { Snippet } from 'svelte';

	import Icon from '$lib/components/Icon.svelte';
	import Pressable from './Pressable.svelte';

	interface Props {
		/** Leave the value as it was. */
		oncancel: () => void;
		/** Keep what is typed. */
		onkeep: () => void;
		/** What the cross says to a screen reader: "Keep the name Summer". */
		cancelLabel: string;
		/** What the tick says: "Rename it to what is typed". */
		keepLabel: string;
		/** A control taller than one line. See above. */
		wide?: boolean;
		/** While the keeping is in flight: neither mark can be pressed twice. */
		busy?: boolean;
		children: Snippet;
	}

	let {
		oncancel,
		onkeep,
		cancelLabel,
		keepLabel,
		wide = false,
		busy = false,
		children
	}: Props = $props();
</script>

<div class="edit-marks" class:wide data-unfinished>
	{@render children()}
	<!--
	Cancel then keep, the confirming mark last; Pressable, since a mark wants no button box.
	-->
	<span class="marks">
		<Pressable
			pad="sm"
			feedback="none"
			radius="sm"
			disabled={busy}
			onclick={oncancel}
			aria-label={cancelLabel}
		>
			<Icon name="close" size={16} />
		</Pressable>
		<Pressable
			class="accept"
			pad="sm"
			feedback="none"
			radius="sm"
			disabled={busy}
			onclick={onkeep}
			aria-label={keepLabel}
		>
			<Icon name="check" size={16} />
		</Pressable>
	</span>
</div>

<style>
	/* Over the field's own end, inside the box. */
	.edit-marks {
		position: relative;
		display: flex;
		align-items: center;
		min-inline-size: 0;
	}

	/* Room for both marks; global, as the box is TextInput's element. */
	.edit-marks:not(.wide) :global(:is(.text-input, input, select)) {
		padding-inline-end: calc(var(--space-8) + var(--space-4));
	}

	/* Centred on the field's height. */
	.marks {
		position: absolute;
		inset-inline-end: var(--space-1);
		inset-block: 0;
		display: flex;
		align-items: center;
		margin-block: auto;
		color: var(--sift-ink-2);
	}

	/* Several lines: the control first, the marks at the trailing end of the line under it. */
	.edit-marks.wide {
		flex-direction: column;
		align-items: stretch;
	}

	.wide .marks {
		position: static;
		justify-content: flex-end;
	}

	/* The ink steps, both ways. */
	.marks :global(.pressable) {
		transition: color var(--dur-instant) var(--ease);
	}

	/* The hovered mark comes up, the finishing one in the accent; no ground inside a field. */
	.marks :global(.pressable:hover:not(:disabled)) {
		color: var(--sift-ink);
	}

	.marks :global(.accept:hover:not(:disabled)) {
		color: var(--sift-accent-text);
	}
</style>
