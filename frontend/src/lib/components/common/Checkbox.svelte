<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'Checkbox',
		category: 'control',
		role: 'a yes, a no, or a some-of-them, with its label',
		basis: 'bits-ui:Checkbox',
		states: ['unchecked', 'checked', 'indeterminate', 'partly', 'disabled', 'bare']
	} satisfies DesignEntry;

	/**
	 * What one row's box can say, on the two kinds of list that draw one.
	 *
	 * Three of them belong to a list that can both include and exclude. `off` is not part of the
	 * question; `on` filters to it; `out` filters to everything but it. That third one is why this
	 * is not a plain boolean: "not chosen" and "deliberately refused" are different answers and a
	 * two-state control has nowhere to put the second.
	 *
	 * `partly` is the fourth and it belongs to the other kind: a PICKER, where a row is not a filter
	 * but a thing a selection of files can be on. Forty files where twelve carry a tag have no true
	 * yes and no true no, and the box has to say so.
	 *
	 * It is deliberately NOT `out` wearing a different word. `out` is a refusal and is painted in
	 * the refusal red for that reason; some-of-them is ordinary and unremarkable, and drawn in red
	 * it would read as twelve files having been rejected. `PickMenu` draws its partial rows through
	 * this state rather than a hand-drawn copy of the bar.
	 */
	export type CheckState = 'off' | 'on' | 'out' | 'partly';
</script>

<script lang="ts">
	/*
	 * A box that can be ticked, struck through, or neither.
	 *
	 * The third state is the point. Per value, a facet list answers "these, and not those", the
	 * question people actually have, rather than only "is this value in the filter" (asking for
	 * "not Ada" should not mean excluding the whole Person filter and adding everybody else back).
	 *
	 * The excluded state is a bar, not a red tick. A tick means yes wherever it appears, whatever
	 * its colour, and to anybody who cannot separate the colours a coloured tick means nothing. A
	 * bar is a different shape, legible at a glance, in monochrome, and at the size this is drawn.
	 *
	 * The library calls a box that is neither on nor off "indeterminate", which this state is not,
	 * so the names are kept apart: the library is told the box is indeterminate, and this app calls
	 * it `out`.
	 */
	import { Checkbox } from 'bits-ui';

	interface Props {
		state?: CheckState;
		/**
		 * Given the state it should become. Cycles off, on, out and back to off. The press comes
		 * with it, so a list can read a held Shift and pick every row between the last pick and
		 * this one.
		 */
		onchange?: (next: CheckState, event?: MouseEvent) => void;
		/** Its accessible name, for a box with no visible label of its own beside it. */
		label?: string;
		/** Genuinely unavailable: it cannot be operated at all, and is dimmed to say so. */
		disabled?: boolean;
		/**
		 * A picture of the state, not a control: whatever it sits inside takes the press.
		 *
		 * The facet rows, the pick dialog's list and the chips on the filter bar work this way,
		 * because a 16-pixel box in a list of forty is a thing to aim at. Not `disabled`: that
		 * would dim a live filter as unavailable and announce a disabled button beside a row
		 * carrying the same name. So this renders a span with the same drawing, hidden from a
		 * screen reader, inside whatever really is the button.
		 */
		mark?: boolean;
		/**
		 * The mark WITHOUT its box: a tick, a bar, or nothing. And, like `mark`, a picture of the
		 * state rather than a control.
		 *
		 * For the end of a row that is already something else, where a bordered square reads as a
		 * second control to press: the pick menu's flyout rows say what a selection is already on
		 * this way, rather than with its own copy of this tick and this bar: the same drawing twice,
		 * with its own numbers and its own colours to drift.
		 *
		 * `off` is present and invisible rather than absent, so a column of these holds its place
		 * and the names beside it do not shift as the answers land. The tick is the accent, because
		 * with no ground under it the accent is what "chosen" is; some-of-them is a bar in the quiet
		 * ink, because it describes a part of the set rather than the whole answer.
		 */
		bare?: boolean;
	}

	let {
		state = 'off',
		onchange,
		label,
		disabled = false,
		mark = false,
		bare = false
	}: Props = $props();

	/* Off, then in, then out, then off again, and `partly` goes to `on`, which is the only reading
	   with a sentence behind it: pressing a half tick means "all of them", where the other direction
	   would take twelve files out of a collection and leave twenty-eight in. The same rule `PickMenu`
	   applies to its own rows. A picker draws this as a `mark` and never calls `onchange`, so this
	   arm is the answer to a question nothing asks today, written truthfully rather than left to
	   fall through to `off`, which is the answer that would be wrong. */
	const next = $derived<CheckState>(
		state === 'off' ? 'on' : state === 'on' ? 'out' : state === 'partly' ? 'on' : 'off'
	);
</script>

<!--
	`child` so this file's scoped styles reach the box: rendered by the library it would be a
	stranger's element and every rule below would silently match nothing.

	The state is handed in and reported back rather than held here. What a filter says is in the
	address, and a control keeping its own copy would be a second answer that cannot be corrected
	when the address changes underneath it (a back button, a saved search applied, another control
	clearing the lot).
-->
{#if bare}
	<!-- No box and no control: the mark alone, inside a row that is the control. See `bare`. -->
	<span
		class="bare"
		class:on={state === 'on'}
		class:out={state === 'out'}
		class:partly={state === 'partly'}
		aria-hidden="true"
	>
		<span class="mark"></span>
	</span>
{:else if mark}
	<!-- No control, because there is one already: the row this sits in. See `mark`. -->
	<span
		class="box"
		class:on={state === 'on'}
		class:out={state === 'out'}
		class:partly={state === 'partly'}
		aria-hidden="true"
	>
		<span class="mark"></span>
	</span>
{:else}
	<Checkbox.Root
		checked={state === 'on'}
		indeterminate={state === 'out' || state === 'partly'}
		{disabled}
		aria-label={label}
		onclick={(event: MouseEvent) => {
			// The library toggles between two states; the third is this app's, so the change is reported
			// here and the library's own answer is not used.
			event.preventDefault();
			onchange?.(next, event);
		}}
	>
		{#snippet child({ props })}
			<button
				{...props}
				class="box"
				class:on={state === 'on'}
				class:out={state === 'out'}
				class:partly={state === 'partly'}
			>
				<span class="mark" aria-hidden="true"></span>
			</button>
		{/snippet}
	</Checkbox.Root>
{/if}

<style>
	/* The footprint, shared by the box and the bare mark: a column of either holds the same place. */
	.box,
	.bare {
		display: inline-grid;
		/* Set here rather than left to the element, because the mark form is a span and a span has
		   no cursor of its own to inherit from a button. */
		box-sizing: border-box;
		place-items: center;
		flex: none;
		inline-size: 16px;
		block-size: 16px;
	}

	.box {
		/* What the state layers mix into; each value below names its own. */
		--box-ground: var(--sift-surface-2);
		padding: 0;
		border: 1px solid var(--sift-line-strong);
		border-radius: var(--radius-sm);
		background-color: var(--box-ground);
		cursor: pointer;
		transition:
			background var(--dur-instant) var(--ease),
			border-color var(--dur-instant) var(--ease);
	}

	/* The hover layer on whatever ground the box has, ticked or not, and the edge a step up. */
	.box:hover:not(:disabled) {
		border-color: var(--sift-ink-3);
		background-color: color-mix(in srgb, var(--sift-ink) var(--layer-hover), var(--box-ground));
	}

	.box:active:not(:disabled) {
		background-color: color-mix(in srgb, var(--sift-ink) var(--layer-pressed), var(--box-ground));
	}

	.box:focus-visible {
		outline: none;
		box-shadow: var(--focus-ring);
	}

	/* Dimmed only when it genuinely cannot be operated. A box that is merely a MARK is not dimmed:
	   the thing it describes is perfectly available, and half-opacity says the opposite. */
	.box:disabled {
		cursor: default;
		opacity: var(--disabled-opacity);
	}

	/* Included wears the accent, which is what "chosen" is everywhere else in the app. */
	.box.on {
		--box-ground: var(--sift-accent);
		border-color: var(--sift-accent);
	}

	/*
	 * Excluded wears the refusal colour, and this is the one place that is not "something went
	 * wrong". Red means failure and destruction everywhere else in Sift, so it is used here at its
	 * quiet weight (the background tint rather than the solid) and paired with a shape change
	 * that carries the meaning on its own.
	 */
	.box.out {
		--box-ground: var(--sift-bad-bg);
		border-color: var(--sift-bad);
	}

	/*
	 * Some of them: the accent at its QUIET weight, against the solid accent a full tick wears.
	 *
	 * The pair has to read as two amounts of one answer rather than as two unrelated answers, which
	 * is why it is the same hue a step down and not a third colour. The shape carries the rest of
	 * it: see the mark below, where the bar is the same drawing the excluded state uses.
	 */
	.box.partly {
		--box-ground: var(--sift-accent-bg);
		border-color: var(--sift-accent);
	}

	/*
	 * The tick and the bar are the same element, drawn two ways.
	 *
	 * The tick is a box with two of its four borders, rotated, so it scales with the control and
	 * needs no image, no font and no second file to load. The bar is the same element unrotated with
	 * one border left on it.
	 */
	.mark {
		inline-size: 5px;
		block-size: 9px;
		border: 2px solid var(--sift-accent-text);
		border-block-start: 0;
		border-inline-start: 0;
		rotate: 45deg;
		translate: 0 -1px;
		opacity: 0;
	}

	.on .mark {
		opacity: 1;
	}

	/* The bar, drawn once for the two states that want one: "not this" and "some of them", in the
	   box and bare alike. The numbers are the numbers, so they are written once: what separates
	   the states and the two forms is the COLOUR, which is the only thing said more than once. */
	.out .mark,
	.partly .mark {
		inline-size: 8px;
		block-size: 0;
		border: 0;
		border-block-start: 2px solid var(--sift-bad);
		rotate: none;
		translate: none;
		opacity: 1;
	}

	.box.partly .mark {
		border-block-start-color: var(--sift-accent-text);
	}

	/* Bare, there is no ground under the mark, so it wears the ink the ground would have: the
	   accent for a tick, the quiet ink for some-of-them. Excluded keeps the refusal red above. */
	.bare.on .mark {
		border-color: var(--sift-accent);
	}

	.bare.partly .mark {
		border-block-start-color: var(--sift-ink-3);
	}

	:global(:root[data-motion='reduce']) .box {
		transition: none;
	}
</style>
