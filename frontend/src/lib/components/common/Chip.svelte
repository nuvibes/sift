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

	/**
	 * How big, in the three sizes the app has: `sm` inside something else, `md` the default, `lg` a
	 * chip that is really a control.
	 */
	export type ChipSize = 'sm' | 'md' | 'lg';

	/**
	 * Square-ish or a pill: a pill names a LABEL somebody applied (a tag, a person), a square a FACT
	 * about the file or the machine (a filter token, a codec), the distinction that elsewhere decides
	 * whether a drop moves a file.
	 */
	export type ChipShape = 'pill' | 'square';

	/**
	 * The resting look: `neutral` filled, the usual; `quiet` an outline, one of many; `accent` the
	 * chosen one; `outline` dashed, the place a thing goes (Add a tag). No ok/warn/bad: a state is a
	 * badge. `state` takes the colour of the work state around it (`--state-bg`, `--state-ink` from a
	 * `state-*` ancestor), so the Jobs tallies match the badges.
	 */
	export type ChipTone = 'neutral' | 'quiet' | 'outline' | 'accent' | 'state';

	/**
	 * A picture at the head of a chip: a face, a cover, a site's own mark, drawn at the chip's own inner
	 * height so a chip with a face measures the same as one without, which a screen could not hold.
	 * Extends `ChoicePicture`, adding the `name` `Avatar` needs for its letter.
	 */
	export interface ChipPicture extends ChoicePicture {
		/** Whose it is. The fallback letter and its tint come from this. See `Avatar.name`. */
		name: string;
		/** A glyph drawn in the letter's place when nothing loads: a song's music glyph. See
		 *  `Avatar.glyph`. */
		glyph?: GlyphName;
	}
</script>

<script lang="ts">
	/*
	 * A chip: a short label in a shape, sometimes pressable, sometimes removable: one object, so
	 * chips agree in height on every screen.
	 *
	 * It sets no vertical padding of its own (the height does, and setting both is how heights
	 * drift), and it centres rather than baseline-aligns with the text beside it.
	 */
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
		 * The opposite of `selected`: this value is being REFUSED, the third position of a three-way
		 * pick, as in the facet panel's rows (not a state, so not a badge). The refusal colour at its
		 * quiet weight, with the caller's strike-through and a bar in the box.
		 */
		refused?: boolean;
		/** A glyph before the label. */
		icon?: IconName;
		/**
		 * A picture before the label (`ChipPicture`), drawn by the chip at its inner height; `lead`
		 * is caller markup of any height.
		 */
		picture?: ChipPicture;
		/**
		 * Something before the label that is not an icon: markup the caller owns, where `icon` takes a
		 * name and is sized for it.
		 */
		lead?: Snippet;
		/**
		 * Something after the label, INSIDE the pressable body (a count), so never a control (`aside`).
		 */
		trail?: Snippet;
		/**
		 * Something after the label and OUTSIDE the pressable body: where a control goes (a tag's
		 * sharing mark), since a button inside a button is torn out by the browser.
		 */
		aside?: Snippet;
		/** Makes the chip pressable. It becomes a real `<button>`. */
		onselect?: () => void;
		/**
		 * Somebody else's wiring for the pressable body (`MenuButton`'s `trigger`), making the chip a
		 * door. Named rather than `{...rest}`, since a chip is one of three elements and loose
		 * attributes could land on a `<span>`. `onselect` beside it is refused: two acts, one target.
		 */
		trigger?: Record<string, unknown>;
		/**
		 * Makes the chip a link. It becomes a real `<a>`: a chip is a NOUN that often has a page,
		 * unlike a `Button`'s verb, and a link keeps middle-click and the status bar. `href` goes,
		 * `onselect` does, neither is a label; both is a type error.
		 */
		href?: string;
		/** A link that leads nowhere yet (an empty pile): quiet and unpressable rather than removed, so
		 *  nobody goes looking for it. Only with `href`. */
		inert?: boolean;
		/**
		 * Adds a remove affordance. Independent of `onselect`, because a chip can be pressable, or
		 * removable, or both: a filter token is both.
		 */
		onremove?: () => void;
		/** What the remove button is called, for anybody who cannot see the cross. */
		removeLabel?: string;
		/**
		 * Ask before removing, naming the thing being taken off: the cross is a small target beside the
		 * half that opens, and a gone chip is hard to notice. Here, so every surface asks; the
		 * sentence is the caller's `removeLabel`. The sheet draws the chip itself, opening `where` (the
		 * chip's own `href`). Absent removes immediately, as a filter token does.
		 */
		confirm?: { what: string; where?: string };
		/** Drawn as the drop target it currently is. Set by whoever owns the drag. */
		dropping?: boolean;
		disabled?: boolean;
		/** Extra classes from the caller, for position only, never for the chip's own look. */
		class?: string;
		/**
		 * What this chip is called, when its own contents do not say (a rating chip of a figure and a
		 * star). Not required, since a chip nearly always has its noun written in it.
		 */
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

	/* Both would be two controls in one target; checked in an effect, since a read up here sees only
	   the first render. */
	$effect(() => {
		if (href !== undefined && onselect !== undefined) {
			throw new Error('A chip goes somewhere or does something, not both. See `href`.');
		}
		if (trigger !== undefined && (onselect !== undefined || href !== undefined)) {
			throw new Error('A chip that opens a menu does only that. See `trigger`.');
		}
	});

	/*
	 * One size at every chip size: the icon set's sizes are a fixed list, and the chip takes the
	 * smallest that exists rather than add one for itself.
	 */
	const ICON = 16;

	function press(event: MouseEvent) {
		// A chip is very often inside something else that is itself clickable: a tile, a row, a
		// card. Choosing the chip is not choosing what it sits on.
		event.stopPropagation();
		onselect?.();
	}

	/* The question, and whether it is still being asked: the ACCOUNT's answer (the shared interface
	 * state), read when a chip that could ask is drawn; until it lands the guard stays. */
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

	/* The box is written only here, after the button has been pressed. A tick on a dialog somebody
	   then cancelled is not an agreement to anything: the same rule the delete guard follows. */
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
	<!-- Asked until somebody says to stop. The sentence is the caller's `removeLabel`, the words the
	     cross already announces. -->
	<ConfirmDialog
		bind:open={asking}
		title="Remove {confirm.what} from this file?"
		consequence="{removeLabel}. Nothing is deleted and the file stays where it is."
		confirmLabel="Remove"
		onconfirm={confirmed}
	>
		{#snippet extra()}
			{#if confirm?.where}
				<!--
					THE THING ITSELF, deep-linked, between the sentence and the buttons: this component's own
					`inside()`, so it is the pressed object, minus the cross. Following the link abandons
					the question, so the sheet shuts on the way out.
				-->
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
		/*
		 * EVERY chip carries the border; only `quiet` colours it in, so a filled chip's contents start
		 * on the same line as an outlined one's (`border-box`).
		 */
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

	/*
	 * Which one is IN FORCE, said without a second colour: a ring in the chip's own ink, inset, since
	 * these chips already carry a colour that means something.
	 */
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

	/* Refused. Last of the three so it wins over `selected` when a caller sets both, which it
	   should not, but "excluded" is the more urgent of the two facts to get right. See `refused`. */
	.chip.refused {
		--chip-ground: var(--sift-bad-bg);
		background-color: var(--chip-ground);
		color: var(--sift-bad-text);
	}

	/*
	 * Hover: the state layer on the chip's own ground (`--layer-hover`), ink to full, one rule for
	 * every tone. Scoped to the body (`:has`), since the cross and an aside are other controls.
	 */
	.chip.pressable:not(.disabled):has(.body:hover) {
		background-color: color-mix(in srgb, currentColor var(--layer-hover), var(--chip-ground));
		color: var(--hover-ink);
	}

	.chip.pressable:not(.disabled):has(.body:active) {
		background-color: color-mix(in srgb, currentColor var(--layer-pressed), var(--chip-ground));
		color: var(--hover-ink);
	}

	/* The drop target, while something is over it: the dragged layer, and a ring rather than a
	   fill, so it reads as "this will take it" rather than as "this is now chosen". */
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

	/* A finger's reach on a phone, without the chip growing: `Pressable`'s ring, on the body and the
	   cross. */
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

		/*
		 * A pressable chip stands a finger's height from the chips above and below it, as margin, so
		 * a ring in a wrapped stack never reaches into the next line's chip.
		 */
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

	/*
	 * THE PICTURE, at the chip's own inner height: `block-size: 100%` and `aspect-ratio`, so no number
	 * follows `--chip-height`. Flush to the leading edge, inside a pill's curve; round on every
	 * shape, as faces are drawn everywhere.
	 */
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

	/*
	 * The label, and why it keeps its descenders: `overflow: hidden` (for the ellipsis) makes the line
	 * box the window glyphs are drawn through, and `.chip`'s `line-height: 1` would cut every
	 * descender. `normal` is the font's own metrics; the chip neither grows nor shifts.
	 */
	.label {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		line-height: normal;
	}

	/* Pulls back the body's trailing padding so the gap either side of it matches the gap inside the
	   label. Without it a chip with a mark reads as two things pushed together. */
	.aside {
		display: inline-flex;
		align-items: center;
		margin-inline-start: calc(var(--chip-pad) * -1 + var(--space-1));
		padding-inline-end: var(--chip-pad);
	}

	/* The same correction, for the same reason, and it is spent on hover rather than at rest,
	   with the width. See the block below. */
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

	/*
	 * The cross rises into the chip, and is not there until somebody points at it, so a row of chips
	 * is read by its names. Zero wide at rest, so names are not ellipsised early; the chips after it
	 * move, the one under the pointer does not. A fade, not a clip on the chip, which would cut its
	 * focus ring. `:focus-within`, so tabbing onto the body shows what is next.
	 */
	.remove {
		opacity: 0;
		translate: 0 100%;
		pointer-events: none;
		/* NO ROOM AT REST: `inline-size: 0`, not `display: none`, so it can still be tabbed to and
		   clipped HERE, never on the chip, whose focus ring is outside its box. */
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
		/* Back to the glyph's own width (`auto`), with the body's trailing padding pulled back so the
		   gaps either side of the mark match. */
		inline-size: auto;
		padding-inline: var(--space-1) var(--chip-pad);
		margin-inline-start: calc(var(--chip-pad) * -1);
		overflow: visible;
	}

	/*
	 * And nothing to pull back when the mark follows an `aside`, which has spent that padding.
	 */
	.chip:hover .aside + .remove,
	.chip:focus-within .aside + .remove {
		margin-inline-start: 0;
	}

	/* No hover to point with: a touch screen shows it always, because the alternative is a control
	   that cannot be reached at all rather than one that is quiet until it is wanted. */
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

	/* It grows and its ink steps up: the half that destroys the chip must not differ by a shade
	   alone, and a ground would look stuck on. */
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

	/* Still, not absent. The cross still appears on a pointer and still goes when it leaves: what
	   goes is the travel, so it does not rise, it is simply there. */
	:global(:root[data-motion='reduce']) .remove {
		transition: none;
		translate: none;
	}
</style>
