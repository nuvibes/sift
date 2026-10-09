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
		/** What a person needs to fill this in, always visible under the control. */
		help?: string;
		/** What to do about it, set on blur or submit, never per keystroke. */
		error?: string;
		/** Take the label off the screen but not out of the accessible tree. */
		hideLabel?: boolean;
		/** The input, select, textarea or switch. It is handed the wiring it has to carry. */
		control: Snippet<[{ id: string; describedBy: string | undefined; invalid: boolean }]>;
	}

	let { label, help, error, hideLabel = false, control }: Props = $props();

	const uid = $props.id();
	const id = `field-${uid}`;
	const helpId = `${id}-help`;
	const errorId = `${id}-error`;

	// Both the error and the help, when both are there.
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

	/* A switch sits at its label's right end, by :has, so no pane must remember it. */
	.field:has(.control :global(.switch)) {
		display: grid;
		grid-template-columns: 1fr auto;
		align-items: center;
		column-gap: var(--space-4);
		row-gap: var(--space-1);
	}

	/* The help runs full width under both. */
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

	/* Clipped, not hidden, so the control keeps its name. */
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

	/* Width only: a control in a field fills the column (the look is app.css's); the app's select
	   is as wide as its widest answer. Global, as the control is the caller's snippet. */
	.control :global(input:not([type='checkbox'], [type='radio'])),
	.control :global(select),
	.control :global(textarea) {
		inline-size: 100%;
	}

	.control :global(.ui-select) {
		inline-size: fit-content;
	}
</style>
