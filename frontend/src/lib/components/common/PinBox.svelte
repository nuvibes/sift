<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'PinBox',
		category: 'control',
		role: 'a PIN, one digit per cell',
		basis: 'bits-ui:PinInput',
		states: ['default', 'filled', 'focused', 'invalid', 'disabled']
	} satisfies DesignEntry;

	/**
	 * How many digits a PIN is. One number, here, rather than a 6 in every screen that sets or
	 * types one; the server's own rule is the same number (`PIN_DIGITS` in the auth tuning).
	 */
	export const PIN_DIGITS = 6;
</script>

<script lang="ts">
	/*
	 * The PIN, as masked digits in cells rather than dots in one password box. Built on bits-ui's
	 * `PinInput`, which owns what a password box cannot express:
	 *
	 * - The cells. How many digits are wanted is a fact about the PIN, and six cells say it without
	 *   a sentence.
	 * - The paste. A PIN from a password manager or a message lands as one string; the primitive
	 *   spreads it across the cells and drops spaces and hyphens through `pasteTransformer`.
	 * - The caret. A hidden input behind painted cells has no caret of its own, so the cell being
	 *   typed into draws one; the primitive says which.
	 *
	 * Masked, like a password field. The PIN is the whole of what keeps the vault shut, and a vault
	 * is opened exactly when somebody else may see the screen; digits at 16 pixels are readable
	 * across a room. The cells keep what matters for typing it: how many are filled is visible at a
	 * glance. `not-a-credential.ts` records the same rule, and `NOT_A_CREDENTIAL` is spread onto
	 * the input so no password manager offers to remember it.
	 *
	 * Six cells, because a PIN is six digits. `onComplete` does not submit, the form's own button
	 * does: a shorter PIN kept from before the six-digit rule still opens Hidden, and it leaves the
	 * last cells empty. A screen that SETS a PIN asks for all six (see `PIN_DIGITS`).
	 *
	 * No room made for a password manager's badge. By default the primitive widens its hidden
	 * input 40px past the cells once it has focus, to leave a manager's icon room; a PIN box ends at
	 * its column's far edge, so that overhang would push past the pane and a double-click in a cell
	 * would shift the whole settings layout. The input is marked as no credential anyway, so no
	 * manager offers itself here.
	 */
	import { PinInput } from 'bits-ui';
	import { NOT_A_CREDENTIAL } from '$lib/forms/not-a-credential';

	interface Props {
		/** The digits typed so far. */
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

	/* Everything that is not a digit, taken out of a paste. A PIN read off a screen or out of a
	   message arrives with spaces or hyphens in it, and a cell that refused the whole paste would
	   read as the paste not working. */
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
				<!--
					A DOT, never the digit. The primitive hands over the character it holds and what is
					drawn for it is this component's decision. See the note above for why it is
					masked. Drawn rather than replaced with a bullet character, because a bullet is
					TEXT: it can be selected, it is copied out as the wrong thing, and it is announced.
					A mark with an empty label says "there is a digit here" and nothing about which.
				-->
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
	/*
	 * The primitive renders both elements, so the classes it is handed have to be reached globally.
	 * Every rule starts at `.pin-box`, which is this component's own root: a bare `:global(...)`
	 * would reach every element in the document with that class, which is the fault `Switch`'s own
	 * note spells out.
	 */
	:global(.pin-box) {
		display: inline-flex;
		gap: var(--space-2);
	}

	:global(.pin-box .ui-pin-cell) {
		position: relative;
		display: flex;
		align-items: center;
		justify-content: center;
		/* A square, said as a ratio rather than as a second number: the cell is one control tall and
		   the width follows, so it cannot drift from the row of controls beside it. */
		block-size: var(--control-height);
		aspect-ratio: 1;
		border: 1px solid var(--sift-line-strong);
		border-radius: var(--radius-md);
		background: var(--sift-surface-1);
		color: var(--sift-ink);
		font-variant-numeric: tabular-nums;
		/* A control with a ground steps its background rather than its ink. */
		transition: background var(--dur-instant) var(--ease);
	}

	:global(.pin-box .ui-pin-cell:hover) {
		background: var(--sift-surface-2);
	}

	/* ...and not while the whole control is refused. Written as a second rule rather than as a
	   `:not()` on the one above, because a `)` inside a `:global(...)` defeats the gate that checks
	   a handed class is dressed globally: it strips the global part with a non-greedy match and
	   the tail comes out looking like a scoped rule. */
	:global(.pin-box.off .ui-pin-cell:hover) {
		background: var(--sift-surface-1);
	}

	/* The cell being typed into. The hidden input holds the real caret, so the active cell wears
	   the focus ring the rest of the app wears. */
	:global(.pin-box .ui-pin-cell[data-active]) {
		box-shadow: var(--focus-ring);
	}

	/*
	 * The two states are this component's own classes: the primitive puts `aria-invalid` and the
	 * disabled state on the hidden input it renders inside the root, not on the root, so
	 * `[aria-invalid='true']` or `[data-disabled]` on the root would never match.
	 */
	:global(.pin-box.refused .ui-pin-cell) {
		border-color: var(--sift-bad-text);
	}

	:global(.pin-box.off .ui-pin-cell) {
		opacity: 0.5;
	}

	/*
	 * Drawn by this component rather than by the primitive: bits-ui says WHICH cell has the caret
	 * and draws nothing, because what a caret looks like is a decision about a design system.
	 *
	 * A STEADY bar, not a blinking one. A blink is a second animation in a system whose motion
	 * budget is a fixed scale of durations, and an infinite one at any of them says nothing the
	 * ring around the same cell has not already said: the cell is where the next digit goes.
	 *
	 * An edge rather than a box: `inset-block` and a border give a hairline with no size on it at
	 * all, which keeps the ratchet that counts literal heights and widths where it is. The ratchet
	 * is the reason the shape is stated this way and the shape is right on its own merits.
	 */
	.caret {
		position: absolute;
		inset-block: var(--space-2);
		border-inline-start: 1px solid var(--sift-ink);
	}

	/*
	 * One digit, said without saying which.
	 *
	 * A round box rather than a bullet character, for the reason beside the markup, and it is
	 * sized as a SQUARE off the space scale, the same way the caret is an edge rather than a box:
	 * neither adds a literal length to the system. `--space-2` is the step that reads as a dot
	 * beside the cell's own `--control-height` without becoming a disc.
	 */
	.dot {
		inline-size: var(--space-2);
		block-size: var(--space-2);
		border-radius: var(--radius-full);
		background: var(--sift-ink);
	}
</style>
