<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'PinBox',
		category: 'control',
		role: 'a PIN, one digit per cell',
		basis: 'bits-ui:PinInput',
		states: ['default', 'filled', 'focused', 'invalid', 'disabled']
	} satisfies DesignEntry;

	/** How many digits a PIN is; the server's `PIN_DIGITS` is the same number. */
	export const PIN_DIGITS = 6;
</script>

<script lang="ts">
	/* The PIN as masked digits in cells, on bits-ui's PinInput (cells, paste, caret). Masked, as a
	 * vault opens when others may see the screen; marked no credential. The form's button submits,
	 * so
	 * an older, shorter PIN still opens; no room is made for a password manager's badge. */
	import { PinInput } from 'bits-ui';
	import { NOT_A_CREDENTIAL } from '$lib/forms/not-a-credential';

	interface Props {
		value?: string;
		/** The hidden input, for a screen that puts the keyboard here when it opens. */
		element?: HTMLInputElement | null;
		id?: string;
		describedBy?: string;
		/** Wrong, so far. Draws the cells as refused; the sentence belongs to the caller. */
		invalid?: boolean;
		disabled?: boolean;
		/** Name it when it stands on its own. Inside a Field the label already names it. */
		label?: string;
	}

	let {
		value = $bindable(''),
		element = $bindable(null),
		id,
		describedBy,
		invalid = false,
		disabled = false,
		label
	}: Props = $props();

	/* Spaces and hyphens out of a pasted PIN. */
	function onlyDigits(text: string): string {
		return text.replace(/\D/g, '');
	}
</script>

<PinInput.Root
	{...NOT_A_CREDENTIAL}
	bind:value
	bind:inputRef={element}
	inputId={id}
	maxlength={PIN_DIGITS}
	{disabled}
	pasteTransformer={onlyDigits}
	inputmode="numeric"
	autocomplete="off"
	pushPasswordManagerStrategy="none"
	aria-label={label}
	aria-describedby={describedBy}
	aria-invalid={invalid ? 'true' : undefined}
	class="pin-box {invalid ? 'refused' : ''} {disabled ? 'off' : ''}"
>
	{#snippet children({ cells })}
		{#each cells as cell, at (at)}
			<PinInput.Cell {cell} class="ui-pin-cell">
				<!-- A drawn dot, never the digit or a bullet character, which is text. -->
				{#if cell.char !== null && cell.char !== undefined}
					<span class="dot" aria-hidden="true"></span>
				{/if}
				{#if cell.hasFakeCaret}
					<span class="caret" aria-hidden="true"></span>
				{/if}
			</PinInput.Cell>
		{/each}
	{/snippet}
</PinInput.Root>

<style>
	/* Global, each rule starting at `.pin-box`: the primitive renders both elements. */
	:global(.pin-box) {
		display: inline-flex;
		gap: var(--space-2);
	}

	:global(.pin-box .ui-pin-cell) {
		position: relative;
		display: flex;
		align-items: center;
		justify-content: center;
		/* A square by ratio, one control tall. */
		block-size: var(--control-height);
		aspect-ratio: 1;
		border: 1px solid var(--sift-line-strong);
		border-radius: var(--radius-md);
		background: var(--sift-surface-1);
		color: var(--sift-ink);
		font-variant-numeric: tabular-nums;
		transition: background var(--dur-instant) var(--ease);
	}

	:global(.pin-box .ui-pin-cell:hover) {
		background: var(--sift-surface-2);
	}

	/* A second rule, since a `)` inside `:global(...)` defeats the dressed-class gate. */
	:global(.pin-box.off .ui-pin-cell:hover) {
		background: var(--sift-surface-1);
	}

	/* The active cell wears the focus ring. */
	:global(.pin-box .ui-pin-cell[data-active]) {
		box-shadow: var(--focus-ring);
	}

	/* This component's own state classes: the primitive marks the hidden input, not the root. */
	:global(.pin-box.refused .ui-pin-cell) {
		border-color: var(--sift-bad-text);
	}

	:global(.pin-box.off .ui-pin-cell) {
		opacity: 0.5;
	}

	/* A steady caret bar drawn here, as an edge with no literal size. */
	.caret {
		position: absolute;
		inset-block: var(--space-2);
		border-inline-start: 1px solid var(--sift-ink);
	}

	/* One digit as a round box off the space scale. */
	.dot {
		inline-size: var(--space-2);
		block-size: var(--space-2);
		border-radius: var(--radius-full);
		background: var(--sift-ink);
	}
</style>
