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

	/*
	 * One value edited in place: the box, and the two answers inside it.
	 *
	 * A cross that leaves it as it was and a tick that keeps what is typed, drawn INSIDE the field's
	 * own edge rather than as a Cancel and a Save beside it. Beside it, a renamed pill is twice as
	 * wide as its neighbours and a fact in a grid pushes the next column over; inside it, the box is
	 * the whole of the edit and the two marks are what finish it. A mark in the end of a field is a
	 * shape the search boxes already use for their cross.
	 *
	 * A saved filter's rename and one field of a file's record both draw it.
	 *
	 * `wide` is for a control taller than one line (a list of boxes, a paragraph, a row of chips):
	 * the marks cannot sit over the end of something with several ends, so they stand at the
	 * trailing end of the line under it, still inside the edit's own box.
	 *
	 * Enter and Escape are the caller's to wire on its own control, because only it knows whether
	 * Enter is a new line (a paragraph) or the end of the edit (a name).
	 */
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
		/** The control being edited. */
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
	<!-- Cancel then keep, which is where this application puts the thing that finishes a question:
	     the confirming mark is last and on the trailing edge. `Pressable` rather than `Button` for
	     the reason the search box's cross is: a button brings a box, a minimum height and its own
	     padding, and what is wanted here is a mark. -->
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
	/* The two marks sit OVER the field's own end rather than beside it: beside it they would be
	   outside the box they belong to, and the field would be two controls narrower than what
	   somebody is typing into it. */
	.edit-marks {
		position: relative;
		display: flex;
		align-items: center;
		min-inline-size: 0;
	}

	/* Room for both marks, so a long value does not run underneath them. Written as what it is: two
	   controls and the inset either side of the pair. `:global` because the box is `TextInput`'s
	   own element, compiled in that file's scope; the select and the number box are dressed the
	   same way by `app.css`, so they take the same room. */
	.edit-marks:not(.wide) :global(:is(.text-input, input, select)) {
		padding-inline-end: calc(var(--space-8) + var(--space-4));
	}

	/*
	 * Centred on the field's height rather than hung from its top: the pair is shorter than the box
	 * they sit in. The strut under a lone icon is collapsed by `app.css` for every `Pressable`
	 * wearing one, so the glyphs sit on the middle.
	 */
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

	/* The ink steps over `--dur-instant` rather than changing between frames. The resting rule is
	   where the transition goes, so it runs on the way out as well as in. */
	.marks :global(.pressable) {
		transition: color var(--dur-instant) var(--ease);
	}

	/* The mark under the pointer comes up, and the one that FINISHES the question comes up in the
	   accent: the same two registers a Cancel and a Save wear as words. No ground under either:
	   these sit inside a field, and a patch of surface in the corner of a box reads as a second
	   control rather than as a mark on this one. */
	.marks :global(.pressable:hover:not(:disabled)) {
		color: var(--sift-ink);
	}

	.marks :global(.accept:hover:not(:disabled)) {
		color: var(--sift-accent-text);
	}
</style>
