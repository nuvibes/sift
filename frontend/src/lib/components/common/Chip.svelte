<script lang="ts" module>
	/* WHY NOT BITS-UI: bits-ui has no chip. A chip is shape, colour and one optional dismiss button; the button
	   inside it is a real <button> for the same reason Button is. */
	import type { DesignEntry } from '$lib/design/entry';
	import type { ChoicePicture } from './verbs';
	import type { IconName as GlyphName } from '$lib/design/icons';

	export const design = {
		name: 'Chip',
		category: 'primitive',
		role: 'a small labeled pill or square: a filter token, a tag, a count, a mark',
		basis: 'own',
		states: [
			'neutral',
			'quiet',
			'outline',
			'accent',
			'selected',
			'disabled',
			'removable',
			'the cross at rest',
			'the cross on hover',
			'picture',
			'sm',
			'md',
			'lg'
		]
	} satisfies DesignEntry;

	/** `sm` inside something else, `md` the default, `lg` a chip that is really a control. */
	export type ChipSize = 'sm' | 'md' | 'lg';

	/** A pill is a label somebody applied, a square a fact about the file or the machine. */
	export type ChipShape = 'pill' | 'square';

	/** `neutral` filled, `quiet` outlined, `accent` chosen, `outline` dashed (where a thing goes),
	 * `state` the colour of the work state around it. */
	export type ChipTone = 'neutral' | 'quiet' | 'outline' | 'accent' | 'state';

	/** A picture at the chip's head at its inner height; adds the `name` Avatar needs. */
	export interface ChipPicture extends ChoicePicture {
		/** Whose it is. The fallback letter and its tint come from this. See `Avatar.name`. */
		name: string;
		/** A glyph in the letter's place when nothing loads. */
		glyph?: GlyphName;
	}
</script>

<script lang="ts">
	/* A chip, sometimes pressable or removable, one object so chips agree in height; no vertical
	 * padding of its own, centred with the text beside it. */
	import type { Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Avatar from './Avatar.svelte';
	import Checkbox from './Checkbox.svelte';
	import ConfirmDialog from './ConfirmDialog.svelte';
	import Pressable from './Pressable.svelte';
	import {
		chipRemoveSkipped,
		recallInterfaceState,
		skipChipRemoveConfirm
	} from '$lib/shell/interface-state.svelte';
	import type { IconName } from '$lib/design/icons';

	interface Props {
		size?: ChipSize;
		shape?: ChipShape;
		tone?: ChipTone;
		/** Chosen, picked, currently filtering. Wins over `tone`. */
		selected?: boolean;
		/**
		 * Refused, the third position of a three-way pick: the refusal colour at its quiet weight.
		 */
		refused?: boolean;
		/** A glyph before the label. */
		icon?: IconName;
		/** A picture before the label, drawn at the chip's inner height. */
		picture?: ChipPicture;
		/** Caller markup before the label that is not an icon. */
		lead?: Snippet;
		/** After the label inside the pressable body (a count); never a control. */
		trail?: Snippet;
		/** After the label outside the body, where a control goes (a button cannot nest). */
		aside?: Snippet;
		/** Makes the chip pressable. It becomes a real `<button>`. */
		onselect?: () => void;
		/** Somebody else's wiring for the body (a menu door); `onselect` beside it is refused. */
		trigger?: Record<string, unknown>;
		/**
		 * Makes the chip a real `<a>`, keeping middle-click; with `onselect` it is a type error.
		 */
		href?: string;
		/** A link leading nowhere yet: quiet and unpressable, not removed. */
		inert?: boolean;
		/** A remove cross, independent of `onselect`. */
		onremove?: () => void;
		/** What the remove button is called, for anybody who cannot see the cross. */
		removeLabel?: string;
		/**
		 * Ask before removing, naming the thing; the sheet draws the chip itself. Absent removes
		 * now.
		 */
		confirm?: { what: string; where?: string };
		/** Drawn as the drop target it currently is. Set by whoever owns the drag. */
		dropping?: boolean;
		disabled?: boolean;
		/** Extra classes from the caller, for position only, never for the chip's own look. */
		class?: string;
		/** Its name where its contents do not say it. */
		label?: string;
		children: Snippet;
	}

	let {
		size = 'md',
		shape = 'pill',
		tone = 'neutral',
		selected = false,
		refused = false,
		icon,
		picture,
		lead,
		trail,
		aside,
		onselect,
		trigger,
		href,
		inert = false,
		onremove,
		removeLabel = 'Remove',
		confirm,
		dropping = false,
		disabled = false,
		class: extra = '',
		label,
		children
	}: Props = $props();

	/* Both would be two controls in one target; checked in an effect. */
	$effect(() => {
		if (href !== undefined && onselect !== undefined) {
			throw new Error('A chip goes somewhere or does something, not both. See `href`.');
		}
		if (trigger !== undefined && (onselect !== undefined || href !== undefined)) {
			throw new Error('A chip that opens a menu does only that. See `trigger`.');
		}
	});

	/* One icon size at every chip size, the smallest the set has. */
	const ICON = 16;

	function press(event: MouseEvent) {
		// Choosing the chip is not choosing what it sits on.
		event.stopPropagation();
		onselect?.();
	}

	/* The account's answer to the question, read when a chip that could ask is drawn. */
	let asking = $state(false);
	let stopAsking = $state(false);

	$effect(() => {
		if (confirm) void recallInterfaceState();
	});

	function remove(event: MouseEvent) {
		event.stopPropagation();
		if (confirm && !chipRemoveSkipped()) {
			stopAsking = false;
			asking = true;
			return;
		}
		onremove?.();
	}

	/* Written only after the button is pressed; a cancelled tick agrees to nothing. */
	function confirmed() {
		if (stopAsking) skipChipRemoveConfirm();
		onremove?.();
	}
</script>

<!-- What is INSIDE the pressable body, written once for the button, the link and the label alike. -->
{#snippet inside()}
	{#if picture}
		<!-- The chip's own box, filled by the shared Avatar as a circle; its letter behind a slow cover. -->
		<span class="shot">
			<Avatar
				src={picture.src}
				instead={picture.instead}
				name={picture.name}
				shape="face"
				mark={picture.mark ?? false}
				glyph={picture.glyph}
			/>
		</span>
	{/if}
	{#if icon}<Icon name={icon} size={ICON} />{/if}
	{@render lead?.()}
	<span class="label">{@render children()}</span>
	{@render trail?.()}
{/snippet}

<span
	class="chip {size} {shape} {tone} {extra}"
	class:selected
	class:refused
	class:dropping
	class:disabled
	class:inert
	class:pressable={Boolean(onselect) || Boolean(href) || Boolean(trigger)}
>
	{#if trigger}
		<!-- A DOOR: the menu library's press and aria, on the chip's own body (`trigger`). -->
		<button type="button" class="body" {disabled} aria-label={label} {...trigger}>
			{@render inside()}
		</button>
	{:else if onselect}
		<button type="button" class="body" onclick={press} {disabled} aria-label={label}>
			{@render inside()}
		</button>
	{:else if href}
		<!-- `selected` on a LINK is the page you are on, announced, not merely shaded. -->
		<a
			class="body"
			{href}
			aria-label={label}
			aria-current={selected ? 'page' : undefined}
			aria-disabled={inert ? 'true' : undefined}
		>
			{@render inside()}
		</a>
	{:else}
		<span class="body" aria-label={label} role={label ? 'img' : undefined}>
			{@render inside()}
		</span>
	{/if}

	<!-- Everything below is outside the body: a button inside a button is torn out by the browser. -->
	{#if aside}
		<span class="aside">{@render aside()}</span>
	{/if}

	{#if onremove}
		<button type="button" class="remove" onclick={remove} aria-label={removeLabel} {disabled}>
			<Icon name="close" size={ICON} />
		</button>
	{/if}
</span>

{#if confirm}
	<!-- Asked until somebody says stop, in the caller's `removeLabel`. -->
	<ConfirmDialog
		bind:open={asking}
		title="Remove {confirm.what} from this file?"
		consequence="{removeLabel}. Nothing is deleted and the file stays where it is."
		confirmLabel="Remove"
		onconfirm={confirmed}
	>
		{#snippet extra()}
			{#if confirm?.where}
				<!-- The thing itself, deep-linked; following it shuts the sheet. -->
				<div class="subject">
					<span class="chip {size} {shape} {tone} pressable">
						<a class="body" href={confirm.where} onclick={() => (asking = false)}>
							{@render inside()}
						</a>
					</span>
				</div>
			{/if}
		{/snippet}
		{#snippet below()}
			<!-- Under the buttons, where every guard puts the offer to stop asking; the whole row presses. -->
			<div class="again">
				<Pressable
					class="tick"
					feedback="wash"
					radius="md"
					aria-pressed={stopAsking}
					onclick={() => (stopAsking = !stopAsking)}
				>
					<Checkbox state={stopAsking ? 'on' : 'off'} mark />
					<span>Don't ask me again for these</span>
				</Pressable>
			</div>
		{/snippet}
	</ConfirmDialog>
{/if}

<style>
	.chip {
		display: inline-flex;
		align-items: center;
		max-inline-size: 100%;
		/* Centred against whatever it sits beside, never on the baseline. */
		vertical-align: middle;
		font: var(--text-label);
		line-height: 1;
		/* Every chip carries the border; only `quiet` colours it. */
		border: 1px solid transparent;
		/* What the state layers mix into. Each tone below names its own ground here and paints it. */
		--chip-ground: transparent;
		transition:
			background var(--dur-instant) var(--ease),
			color var(--dur-instant) var(--ease),
			box-shadow var(--dur-fast) var(--ease);
	}

	/* --- Size. The height does the vertical spacing; nothing sets padding-block. --- */
	.chip.sm {
		block-size: var(--chip-height-sm);
	}
	.chip.md {
		block-size: var(--chip-height);
	}
	.chip.lg {
		block-size: var(--chip-height-lg);
	}

	.sm .body,
	.md .body {
		padding-inline: var(--chip-pad);
	}
	.lg .body {
		padding-inline: var(--chip-pad-lg);
	}

	/* --- Shape. --- */
	.chip.pill {
		border-radius: var(--radius-full);
	}
	.chip.square {
		border-radius: var(--radius-sm);
	}

	/* --- Tone. --- */
	.chip.neutral {
		--chip-ground: var(--sift-surface-4);
		background-color: var(--chip-ground);
		color: var(--sift-ink-2);
	}

	/* The border is already there, on every chip. This tone is the one that lets it be seen. */
	.chip.quiet {
		border-color: var(--sift-line);
		background-color: transparent;
		color: var(--sift-ink-2);
	}

	/* The place a thing goes: `quiet` with the line DASHED and nothing else (`ChipTone`). */
	.chip.outline {
		border-color: var(--sift-line);
		border-style: dashed;
		background-color: transparent;
		color: var(--sift-ink-2);
	}

	/* See `ChipTone`. The colour is whatever state class is above it, and this holds no opinion. */
	.chip.state {
		--chip-ground: var(--state-bg);
		background-color: var(--chip-ground);
		color: var(--state-ink);
	}

	/* In force: a ring in the chip's own ink, inset. */
	.chip.state.selected {
		--chip-ground: var(--state-bg);
		background-color: var(--chip-ground);
		color: var(--state-ink);
		box-shadow: inset 0 0 0 1px currentcolor;
	}

	.chip.accent,
	.chip.selected {
		--chip-ground: var(--sift-accent-bg);
		background-color: var(--chip-ground);
		color: var(--sift-accent-text);
	}

	/* Refused, last so it wins over `selected`. */
	.chip.refused {
		--chip-ground: var(--sift-bad-bg);
		background-color: var(--chip-ground);
		color: var(--sift-bad-text);
	}

	/* Hover: the state layer on the body only, as the cross and aside are other controls. */
	.chip.pressable:not(.disabled):has(.body:hover) {
		background-color: color-mix(in srgb, currentColor var(--layer-hover), var(--chip-ground));
		color: var(--hover-ink);
	}

	.chip.pressable:not(.disabled):has(.body:active) {
		background-color: color-mix(in srgb, currentColor var(--layer-pressed), var(--chip-ground));
		color: var(--hover-ink);
	}

	/* The drop target: a ring, not a fill, so it is not read as chosen. */
	.chip.dropping {
		box-shadow: var(--focus-ring);
		background-color: color-mix(in srgb, currentColor var(--layer-dragged), var(--chip-ground));
		color: var(--sift-accent-text);
	}

	.chip.disabled {
		opacity: var(--disabled-opacity);
	}

	/* A link that leads nowhere yet (`inert`): faded and untargetable, still drawn and read out. */
	.chip.inert {
		opacity: 0.6;
		pointer-events: none;
	}

	.body {
		display: inline-flex;
		align-items: center;
		gap: var(--space-1);
		min-inline-size: 0;
		block-size: 100%;
		border: 0;
		background: transparent;
		color: inherit;
		font: inherit;
		cursor: inherit;
		white-space: nowrap;
	}

	/* One line: in a narrow place only the label gives way, to its ellipsis. */
	.body > :global(*) {
		flex: none;
	}

	.body > .label {
		flex: 0 1 auto;
		min-inline-size: 0;
	}

	button.body:not(:disabled) {
		cursor: pointer;
	}

	/* A finger's reach on a phone through Pressable's ring. */
	@media (max-width: 767px) {
		button.body,
		a.body,
		.remove {
			position: relative;
		}

		button.body::after,
		a.body::after,
		.remove::after {
			content: '';
			position: absolute;
			inset-block: min(0px, calc((100% - var(--touch-target)) / 2));
			inset-inline: min(0px, calc((100% - var(--touch-target)) / 2));
		}

		/* A finger's height of margin, so rings in a wrapped stack never overlap. */
		.chip.pressable.sm,
		.chip.sm:has(> .remove) {
			margin-block: calc((var(--touch-target) - var(--chip-height-sm)) / 2);
		}

		.chip.pressable.md,
		.chip.md:has(> .remove) {
			margin-block: calc((var(--touch-target) - var(--chip-height)) / 2);
		}

		.chip.pressable.lg,
		.chip.lg:has(> .remove) {
			margin-block: calc((var(--touch-target) - var(--chip-height-lg)) / 2);
		}
	}

	/* The link body, in the tone's colour, never the browser's blue underline. */
	a.body {
		color: inherit;
		text-decoration: none;
		cursor: pointer;
	}

	/* The picture at the inner height by ratio, round, flush to the leading edge. */
	.shot {
		display: block;
		flex: none;
		block-size: 100%;
		aspect-ratio: 1;
		border-radius: var(--radius-full);
		overflow: hidden;
		margin-inline-start: calc(var(--chip-pad) * -1);
	}

	.lg .shot {
		margin-inline-start: calc(var(--chip-pad-lg) * -1);
	}

	/* `line-height: normal`, or the ellipsis box would cut descenders. */
	.label {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		line-height: normal;
	}

	/* Pulls back the body's padding so the gaps either side match. */
	.aside {
		display: inline-flex;
		align-items: center;
		margin-inline-start: calc(var(--chip-pad) * -1 + var(--space-1));
		padding-inline-end: var(--chip-pad);
	}

	/* The same correction, spent on hover. */
	.remove {
		display: grid;
		place-items: center;
		block-size: 100%;
		border: 0;
		border-start-end-radius: inherit;
		border-end-end-radius: inherit;
		background: transparent;
		color: inherit;
		cursor: pointer;
		transition:
			transform var(--dur-instant) var(--ease),
			color var(--dur-instant) var(--ease),
			opacity var(--dur-fast) var(--ease),
			translate var(--dur-fast) var(--ease);
	}

	/* The cross rises on hover or focus-within, zero wide at rest so names are not cut early. */
	.remove {
		opacity: 0;
		translate: 0 100%;
		pointer-events: none;
		/* Zero wide, not `display: none`, so it can still be tabbed to. */
		inline-size: 0;
		padding-inline: 0;
		margin-inline-start: 0;
		overflow: hidden;
	}

	.chip:hover .remove,
	.chip:focus-within .remove {
		opacity: 1;
		translate: none;
		pointer-events: auto;
		/* Its own width, with the padding pulled back. */
		inline-size: auto;
		padding-inline: var(--space-1) var(--chip-pad);
		margin-inline-start: calc(var(--chip-pad) * -1);
		overflow: visible;
	}

	/* Nothing to pull back after an `aside`. */
	.chip:hover .aside + .remove,
	.chip:focus-within .aside + .remove {
		margin-inline-start: 0;
	}

	/* A touch screen always shows it. */
	@media (hover: none) {
		.remove {
			opacity: 1;
			translate: none;
			pointer-events: auto;
			inline-size: auto;
			padding-inline: var(--space-1) var(--chip-pad);
			margin-inline-start: calc(var(--chip-pad) * -1);
			overflow: visible;
		}

		.aside + .remove {
			margin-inline-start: 0;
		}
	}

	/* It grows and its ink steps up. */
	.remove:not(:disabled):hover {
		transform: scale(1.18);
		color: var(--sift-ink);
	}

	.body:focus-visible,
	.remove:focus-visible {
		outline: none;
		box-shadow: var(--focus-ring);
		border-radius: inherit;
	}

	:global(:root[data-motion='reduce']) .chip {
		transition: none;
	}

	/* The thing the question is about, on its own line; this is only the room around it. */
	.subject {
		display: flex;
		margin-block-start: var(--space-3);
	}

	/* The offer to stop being asked, under the buttons, the delete guard's shape. */
	.again {
		margin: var(--space-3) 0 var(--space-5);
	}

	/* The whole ROW is the control (`Pressable`); `:global`, a handed class. */
	.again :global(.tick) {
		display: flex;
		align-items: center;
		gap: var(--space-3);
		inline-size: 100%;
		padding: var(--space-2) var(--space-3);
		text-align: start;
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}

	/* Still, not absent: no travel under reduced motion. */
	:global(:root[data-motion='reduce']) .remove {
		transition: none;
		translate: none;
	}
</style>
