<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'Field',
		category: 'control',
		role: 'a label, its control, and the help or the error under it, laid out the one way',
		basis: 'own',
		states: ['default', 'with help', 'with an error', 'disabled']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: a label, its control and its help text, laid out. bits-ui's Label is a passthrough to the
	   site <label>, which already ties itself to the control it names. */
	import type { Snippet } from 'svelte';

	interface Props {
		label: string;
		/**
		 * What a person needs to know to fill this in. It sits under the control, always visible.
		 * Help behind a tooltip is help you have to already suspect exists before you can read it.
		 */
		help?: string;
		/**
		 * What to do about it, not what went wrong. "Pick a folder Sift can write to" tells someone
		 * their next move; "Invalid path" tells them the computer is unhappy and leaves them there.
		 *
		 * Set this on blur or on submit. Validating every keystroke means shouting at someone for
		 * a half-typed word they were still typing.
		 */
		error?: string;
		/**
		 * Take the label off the screen without taking it out of the accessible tree.
		 *
		 * For a control whose purpose is already obvious from where it is: a search box in a
		 * toolbar, beside a Find button. `display: none` is NOT the way to do that: it removes the
		 * label from the accessible tree too, and the control is then a box a screen reader can only
		 * call "edit text". The rule below moves it out of view and leaves it readable.
		 */
		hideLabel?: boolean;
		/** The input, select, textarea or switch. It is handed the wiring it has to carry. */
		control: Snippet<[{ id: string; describedBy: string | undefined; invalid: boolean }]>;
	}

	let { label, help, error, hideLabel = false, control }: Props = $props();

	const uid = $props.id();
	const id = `field-${uid}`;
	const helpId = `${id}-help`;
	const errorId = `${id}-error`;

	// A screen reader reads what the control points at. Both, when both are there: the error says
	// what to do now and the help still says what the field is for.
	const describedBy = $derived(
		[help ? helpId : null, error ? errorId : null].filter(Boolean).join(' ') || undefined
	);
</script>

<div class="field" class:bare={hideLabel}>
	<label class="label" for={id}>{label}</label>

	<div class="control">
		{@render control({ id, describedBy, invalid: Boolean(error) })}
	</div>

	{#if help}
		<p class="help" id={helpId}>{help}</p>
	{/if}

	<!-- Announced when it appears, because the person who needs it may have already moved on. -->
	{#if error}
		<p class="error" id={errorId} role="alert">{error}</p>
	{/if}
</div>

<style>
	.field {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	/*
	 * A switch sits at the right-hand end of its label, always.
	 *
	 * Every other control here fills the column (a text box, a select and a text area belong under
	 * the words naming them). A switch is 36 pixels of control against a sentence, and stacked it
	 * reads as a stray toggle under a heading.
	 *
	 * Done here with `:has` rather than as a prop each pane passes, so it is laid out from what the
	 * field contains and nothing has to be remembered by a pane added later.
	 *
	 * The `:global` around the inner selector is load-bearing: the switch is rendered by the
	 * primitive inside the caller's snippet, so it carries no scoping class of this component's,
	 * and an unscoped `.switch` would silently match nothing.
	 */
	.field:has(.control :global(.switch)) {
		display: grid;
		grid-template-columns: 1fr auto;
		align-items: center;
		column-gap: var(--space-4);
		row-gap: var(--space-1);
	}

	/* The label keeps the first column, the switch takes the second, and the help runs full width
	   underneath both: it is a sentence and it needs the room. */
	.field:has(.control :global(.switch)) .label {
		grid-column: 1;
	}

	.field:has(.control :global(.switch)) .control {
		grid-column: 2;
		grid-row: 1;
		justify-self: end;
	}

	.field:has(.control :global(.switch)) .help,
	.field:has(.control :global(.switch)) .error {
		grid-column: 1 / -1;
	}

	.label {
		font: var(--text-label);
		color: var(--sift-ink-2);
	}

	/* Off the screen, still in the accessible tree. Clipped to nothing rather than hidden, because
	   `display: none` and `visibility: hidden` both take it out of the tree and leave the control
	   unnamed. */
	.bare .label {
		position: absolute;
		width: 1px;
		height: 1px;
		clip-path: inset(50%);
		overflow: hidden;
		white-space: nowrap;
	}

	.bare {
		gap: 0;
	}

	.help {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	.error {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-bad-text);
	}

	/* WIDTH, and only width.
	 *
	 * What a text box LOOKS like (its height, its border, its ground, its face) is in the app's
	 * stylesheet, on the elements themselves, so a control gets it whether or not it is in one of
	 * these. Scoped to this component's own control box, an `<input>` written anywhere else would
	 * come out white on a dark screen.
	 *
	 * What is left is the one thing that really is this component's business: a control inside a
	 * field fills the field's column. Everywhere else the same control is its intrinsic width, and
	 * that is right: a box in a toolbar should not be as wide as the toolbar.
	 *
	 * `:global` because the control is passed in as a snippet rather than rendered here, so it
	 * carries no scoping class of this file's and a plain selector would match nothing at all.
	 *
	 * The app's own select is the exception: it is as wide as its widest answer and the chevron,
	 * in a field as everywhere, so a short list never draws a box that is mostly empty. Its own
	 * `auto` would fill a block column, so the field names the content width. */
	.control :global(input:not([type='checkbox'], [type='radio'])),
	.control :global(select),
	.control :global(textarea) {
		inline-size: 100%;
	}

	.control :global(.ui-select) {
		inline-size: fit-content;
	}
</style>
