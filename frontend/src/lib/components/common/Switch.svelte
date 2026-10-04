<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'Switch',
		category: 'control',
		role: 'on or off, with its label',
		basis: 'bits-ui:Switch',
		states: ['off', 'on', 'disabled']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	import { Switch } from 'bits-ui';

	interface Props {
		checked?: boolean;
		id?: string;
		describedBy?: string;
		disabled?: boolean;
		/** Name it when it stands on its own. Inside a Field the label already names it. */
		label?: string;
		/**
		 * Told what it was flipped to. For a switch whose new value has to be sent somewhere (a
		 * setting that saves as you touch it), where binding alone would leave the caller watching
		 * its own state for a change it caused and could not tell apart from one it did not.
		 */
		onCheckedChange?: (checked: boolean) => void;
	}

	let {
		checked = $bindable(false),
		id,
		describedBy,
		disabled = false,
		label,
		onCheckedChange
	}: Props = $props();
</script>

<Switch.Root
	bind:checked
	{id}
	{disabled}
	{onCheckedChange}
	aria-label={label}
	aria-describedby={describedBy}
	class="switch"
>
	<Switch.Thumb class="ui-switch-thumb" />
</Switch.Root>

<style>
	/*
	 * The primitive renders these elements, so the classes it is given have to be reached globally.
	 *
	 * The reach is NOT only as wide as it looks. That is true of `.switch .thumb`, but a rule that
	 * STARTS with `:global`, a bare `:global(.thumb)`, matches every element in the document called
	 * `thumb`, wherever it is drawn and whoever drew it. The knob is 16px square, round, and filled
	 * with the foreground ink, so anything else that reasonably called itself a thumbnail would
	 * become a small white dot, even from a component nowhere on the screen, because a stylesheet
	 * loaded once is loaded for the session.
	 *
	 * So the knob is namespaced, the way `ui-select-*` already is. `.switch` below is still global
	 * and is left as it is on purpose: `Field` reads it to lay a switch row out, and it is a word
	 * nothing else in this app would call a box. `thumb` is not.
	 */
	:global(.switch) {
		inline-size: 36px;
		block-size: 20px;
		padding: 2px;
		border: 0;
		border-radius: var(--radius-full);
		background-color: var(--sift-surface-4);
		cursor: pointer;
		transition: background-color var(--dur-fast) var(--ease);
	}

	:global(.switch[data-state='checked']) {
		background-color: var(--sift-accent);
	}

	/* The state layers on the track, in the knob's ink, over whichever ground the track has: off
	   or on, pointing at it and pressing it answer the same way (see `--layer-hover`). */
	:global(.switch:hover:not(:disabled)) {
		background-color: color-mix(in srgb, var(--sift-ink) var(--layer-hover), var(--sift-surface-4));
	}

	:global(.switch[data-state='checked']:hover:not(:disabled)) {
		background-color: color-mix(in srgb, var(--sift-ink) var(--layer-hover), var(--sift-accent));
	}

	:global(.switch:active:not(:disabled)) {
		background-color: color-mix(
			in srgb,
			var(--sift-ink) var(--layer-pressed),
			var(--sift-surface-4)
		);
	}

	:global(.switch[data-state='checked']:active:not(:disabled)) {
		background-color: color-mix(in srgb, var(--sift-ink) var(--layer-pressed), var(--sift-accent));
	}

	:global(.switch:disabled) {
		cursor: not-allowed;
		opacity: var(--disabled-opacity);
	}

	:global(.switch:focus-visible) {
		outline: none;
		box-shadow: var(--focus-ring);
	}

	:global(.ui-switch-thumb) {
		display: block;
		inline-size: 16px;
		block-size: 16px;
		border-radius: var(--radius-full);
		background: var(--sift-ink);
		transition: translate var(--dur-fast) var(--ease);
	}

	:global(.switch[data-state='checked'] .ui-switch-thumb) {
		translate: 16px 0;
	}

	/*
	 * A finger's switch on a phone. The desktop's 36 by 20 is a mouse's target: a thumb pressing a
	 * column of them lands on the row above as often as on the one it meant. The track grows to
	 * the size a phone's own switch is drawn at, and the press reaches a finger's height past the
	 * track (the ring below is invisible and belongs to the button, so a press on it IS a press on
	 * the switch) without the row growing for a ring nobody sees.
	 */
	@media (max-width: 767px) {
		/* Measured off the finger's own size: the track a step less tall, the knob the track less
		   its 2px inset either side, and a travel of `--space-5`, which the width is built from. */
		:global(.switch) {
			--switch-track: calc(var(--touch-target) - var(--space-3));
			--switch-knob: calc(var(--switch-track) - var(--space-1));
			position: relative;
			inline-size: calc(var(--switch-knob) + var(--space-5) + var(--space-1));
			block-size: var(--switch-track);
		}

		:global(.switch)::before {
			content: '';
			position: absolute;
			inset-block: min(0px, calc((100% - var(--touch-target)) / 2));
			inset-inline: 0;
		}

		:global(.ui-switch-thumb) {
			inline-size: var(--switch-knob);
			block-size: var(--switch-knob);
		}

		:global(.switch[data-state='checked'] .ui-switch-thumb) {
			translate: var(--space-5) 0;
		}
	}
</style>
