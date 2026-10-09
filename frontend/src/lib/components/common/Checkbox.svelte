<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'Checkbox',
		category: 'control',
		role: 'a yes, a no, or a some-of-them, with its label',
		basis: 'bits-ui:Checkbox',
		states: ['unchecked', 'checked', 'indeterminate', 'partly', 'disabled', 'bare']
	} satisfies DesignEntry;

	/** `off`, `on` (filter to it), `out` (everything but it) and, for a picker, `partly`: some of
	 * the set, drawn quiet rather than in the refusal red. */
	export type CheckState = 'off' | 'on' | 'out' | 'partly';
</script>

<script lang="ts">
	/* A box ticked, struck through or neither, so a facet answers "these, and not those". The struck
	 * state is a bar, a shape and not a colour; the library calls it indeterminate, this app `out`.
	 * */
	import { Checkbox } from 'bits-ui';

	interface Props {
		state?: CheckState;
		/** Given the next state, with the press, so a list can read a held Shift. */
		onchange?: (next: CheckState, event?: MouseEvent) => void;
		/** Its accessible name, for a box with no visible label of its own beside it. */
		label?: string;
		/** Genuinely unavailable: it cannot be operated at all, and is dimmed to say so. */
		disabled?: boolean;
		/**
		 * A picture of the state inside whatever takes the press; not `disabled`, which would dim
		 * it.
		 */
		mark?: boolean;
		/** The mark without its box, for a row that is already a control; `off` holds its place. */
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

	/* `partly` goes to `on`: pressing a half tick means all of them. */
	const next = $derived<CheckState>(
		state === 'off' ? 'on' : state === 'on' ? 'out' : state === 'partly' ? 'on' : 'off'
	);
</script>

<!-- `child`, so scoped styles reach the box. The state is handed in, never held. -->

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
		/* Set here: the mark form is a span with no cursor to inherit. */
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

	/* Dimmed only when it cannot be operated, never as a mark. */
	.box:disabled {
		cursor: default;
		opacity: var(--disabled-opacity);
	}

	/* Included wears the accent, which is what "chosen" is everywhere else in the app. */
	.box.on {
		--box-ground: var(--sift-accent);
		border-color: var(--sift-accent);
	}

	/* Excluded: the refusal colour at its quiet weight, the bar carrying the meaning. */
	.box.out {
		--box-ground: var(--sift-bad-bg);
		border-color: var(--sift-bad);
	}

	/* Some of them: the accent a step quieter, with the bar. */
	.box.partly {
		--box-ground: var(--sift-accent-bg);
		border-color: var(--sift-accent);
	}

	/* The tick is a rotated box with two borders; the bar is the same element with one. */
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

	/* The bar, drawn once for `out` and `partly`; only the colour differs. */
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

	/* Bare, the mark wears the ink a ground would have. */
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
