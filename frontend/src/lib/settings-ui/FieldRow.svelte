<script lang="ts">
	/* DRESSED BY: .name (LabelledRow styles the label its caller writes, as it does for SettingRow). */

	/*
	 * A field to fill in, drawn as a settings row: its name and help on the left, the box on the
	 * right, and the press that saves it beside the box.
	 *
	 * The field form of `SettingRow`. A form on a settings pane (a new password, a PIN, a guest's
	 * name, a stash-box's key) is the pane's own rows, so every field is a `LabelledRow`: the same
	 * name column, the same packing to the pane's right edge, the same line between two rows and
	 * the same "Copy settings path" beside its name that every other row has. A bare `Field` in a
	 * pane would stack its label over its box and put the save under the box, on a line of its
	 * own; `check_settings_fields.js` refuses one.
	 *
	 * The press stands BESIDE the box, level with it, on the row of the field it saves (the last
	 * field of a form). What the box has to say about itself (the error, a strength meter) stands
	 * under it, in the control column.
	 *
	 * A text area is pasted into and is long, so `stacked` puts it under its name, the row's whole
	 * width.
	 *
	 * The wiring is `Field`'s: the control snippet is handed its id, what describes it and whether
	 * it is refused, so a caller moving from `Field` changes the tag and nothing inside it.
	 */
	import type { Snippet } from 'svelte';
	import LabelledRow from '$lib/components/common/LabelledRow.svelte';

	interface Props {
		/** What the field is. The row's name, and the label the box is named by. */
		label: string;
		/** What a person needs to know to fill it in. */
		help?: string;
		/** What to do about it, set on submit. Under the box, announced when it appears. */
		error?: string;
		/** An address for the row, where something links to it. */
		id?: string;
		/** The box. Handed the wiring it has to carry. */
		control: Snippet<[{ id: string; describedBy: string | undefined; invalid: boolean }]>;
		/** The press beside the box: the form's submit, on the field it saves. */
		press?: Snippet;
		/** Under the box, in the control column: a strength meter reading it. */
		under?: Snippet;
		/** The box under the name, the row's whole width: a text area something is pasted into. */
		stacked?: boolean;
	}

	let { label, help, error, id, control, press, under, stacked = false }: Props = $props();

	const uid = $props.id();
	const boxId = `field-row-${uid}`;
	const helpId = `${boxId}-help`;
	const errorId = `${boxId}-error`;

	const describedBy = $derived(
		[help ? helpId : null, error ? errorId : null].filter(Boolean).join(' ') || undefined
	);
</script>

{#snippet named()}
	<label class="name" for={boxId}>{label}</label>
{/snippet}

<LabelledRow {id} name={named} {help} {helpId} wide besideField={press !== undefined} {stacked}>
	<div class="field-row" class:stacked>
		<div class="line">
			<div class="box">
				{@render control({ id: boxId, describedBy, invalid: Boolean(error) })}
			</div>
			{#if press}{@render press()}{/if}
		</div>
		{#if under}{@render under()}{/if}
		<!-- Announced when it appears, because the person who needs it may have already moved on. -->
		{#if error}<p class="error" id={errorId} role="alert">{error}</p>{/if}
	</div>
</LabelledRow>

<style>
	/* The control column's whole width: the box and its press on one line, what reads the box
	   under it. */
	.field-row {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		inline-size: 100%;
		min-inline-size: 0;
	}

	.line {
		display: flex;
		align-items: center;
		justify-content: var(--row-pack, flex-end);
		gap: var(--space-2);
		min-inline-size: 0;
	}

	/* A text area is several lines tall: its press stands at its top, level with its first line. */
	.stacked .line {
		align-items: flex-start;
	}

	/* The box takes what the press leaves. A control with a width of its own (the PIN's six cells)
	   keeps it and ends at the column's edge, where every control ends. */
	.box {
		display: flex;
		justify-content: var(--row-pack, flex-end);
		flex: 1 1 0;
		min-inline-size: 0;
	}

	/* DRESSED BY: the box primitives. Only the width is this file's: a text box fills what the
	   press leaves, as a field's box fills its column. */
	.box > :global(:is(.text-input, .text-area, .password, .paste)) {
		inline-size: 100%;
	}

	.box :global(.password input) {
		inline-size: 100%;
	}

	/* On a phone a box that cannot shrink (the PIN's six squares) keeps its width and the press
	   goes under it: at a phone's width the squares and "Save PIN" do not fit one line, and the
	   press would be drawn over the sixth square. */
	@media (max-width: 767px) {
		.line {
			flex-wrap: wrap;
		}

		.box {
			min-inline-size: min-content;
		}
	}

	.error {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-bad-text);
	}
</style>
