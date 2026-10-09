<script lang="ts" module>
	/* WHY NOT BITS-UI: bits-ui's Button adds exactly one thing to the site element: it renders an <a> when
	given an href; everything that matters here is the real <button>'s already. */
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'Button',
		category: 'primitive',
		role: 'every button: every tone, every size, icon-only or with words',
		basis: 'site:<button>',
		states: [
			'primary',
			'secondary',
			'ghost',
			'quiet',
			'danger',
			'small',
			'icon',
			'disabled',
			'pressed'
		]
	} satisfies DesignEntry;

	/** How loud: `primary` the one thing a screen wants, `secondary`, `ghost`, `danger` (never a
	 * default), `link` inside a sentence. Over media the glass goes on the container. */
	export type ButtonTone =
		| 'primary'
		| 'secondary'
		| 'ghost'
		| 'quiet'
		| 'danger'
		| 'danger-quiet'
		| 'danger-ghost'
		| 'link';

	export type ButtonSize = 'small' | 'medium';

	/** A circle for an icon-only control dropped onto something; only on the icon-only shape. */
	export type ButtonShape = 'box' | 'circle';
</script>

<script lang="ts">
	/* The app's button, a real <button>. Glyph and words for an act, words only for a navigation or
	 * an answer, a glyph alone inside a Tooltip with an aria-label; `check_button_glyphs.js` and
	 * `check_glyph_reach.js` hold the rules. */
	import type { Snippet } from 'svelte';
	import type { HTMLButtonAttributes } from 'svelte/elements';

	import Icon from '$lib/components/Icon.svelte';
	import Spinner from './Spinner.svelte';
	import { pressSize, pressSizeFor } from './press-size';
	import type { IconName } from '$lib/design/icons';
	import type { IconSize } from '$lib/components/Icon.svelte';

	interface Common extends HTMLButtonAttributes {
		tone?: ButtonTone;
		size?: ButtonSize;
		/** Waiting: no second press; an arc takes the glyph's place, so the width holds. */
		busy?: boolean;
		/** A toggle that is on: the accent and `aria-pressed` together. */
		pressed?: boolean;
		/** Draw the glyph filled whatever the pressed state, for a glyph filled by what it is. */
		iconFilled?: boolean;
		/** Fill the space it is given. */
		full?: boolean;
		/** Extra classes for position only, merged, never spread over `btn`. */
		class?: string;
		/** See `ButtonShape`. Refused on a button with words in it, one interface down. */
		shape?: ButtonShape;
		/** A glyph after the words (a chevron), centred as a sibling of the label. */
		trailing?: IconName;
		/** The glyph's size, from the sanctioned list. 18 fits the control; a bar's Play asks for more. */
		iconSize?: IconSize;
	}

	/** A button with words, optionally a glyph before them. */
	interface Labelled extends Common {
		icon?: IconName;
		children: Snippet;
		/* A circle with words is a lozenge, so it is a type error. */
		shape?: never;
		/* Words are laid out on a line and have a height of their own; only a glyph stands tall. */
		tall?: never;
	}

	/** A glyph-only button; its `aria-label` is required by the type. */
	interface IconOnly extends Common {
		icon: IconName;
		'aria-label': string;
		children?: never;
		/** As tall as what holds it, for a strip's end arrows; the width stays square's. */
		tall?: boolean;
	}

	type Props = Labelled | IconOnly;

	let {
		tone = 'secondary',
		size: named,
		shape = 'box',
		icon,
		iconSize = 18,
		full = false,
		busy = false,
		pressed,
		iconFilled = false,
		trailing,
		type = 'button',
		disabled,
		children,
		class: extra = '',
		tall = false,
		...rest
	}: Props = $props();

	/* The holder decides the height when the caller named none (`press-size`). */
	const given = pressSize();
	const size = $derived(pressSizeFor(named, type, given));
</script>

<button
	{type}
	class="btn {tone} {size} {extra}"
	class:full
	class:circle={shape === 'circle'}
	class:icon-only={!children}
	class:tall
	disabled={disabled || busy}
	aria-busy={busy ? 'true' : undefined}
	aria-pressed={pressed === undefined ? undefined : pressed}
	{...rest}
>
	<!-- The arc in the glyph's place, or before the words. -->
	{#if busy}
		<Spinner size={16} />
	{:else if icon}
		<!-- Filled while it is on, so "the current one" is said in shape as well as colour. -->
		<Icon name={icon} size={iconSize} filled={iconFilled || pressed === true} />
	{/if}
	{#if children}<span class="label">{@render children()}</span>{/if}
	<!-- A trailing glyph is quieter punctuation. -->
	{#if trailing}<span class="trailing"><Icon name={trailing} size={16} /></span>{/if}
</button>

<style>
	/* Square, so a row of them lines up. */
	.btn.icon-only {
		padding: 0;
		aspect-ratio: 1;
	}

	.btn.icon-only.medium {
		inline-size: var(--control-height);
		block-size: var(--control-height);
	}

	.btn.icon-only.small {
		inline-size: var(--control-height-sm);
		block-size: var(--control-height-sm);
	}

	/* See `tall`: the height is what holds it, which stretches it, and the square gives way. */
	.btn.icon-only.tall {
		block-size: auto;
		align-self: stretch;
		aspect-ratio: auto;
	}

	/* Two classes deep, so it beats the `.btn` radius. */
	.btn.circle {
		border-radius: var(--radius-full);
	}

	.btn {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		gap: var(--space-2);
		border: 1px solid transparent;
		/* `--radius-md`, a button's rung on the scale; the small rung is for chips and inputs. */
		border-radius: var(--radius-md);
		font: var(--text-body);
		line-height: 1;
		cursor: pointer;
		white-space: nowrap;
		/* Sized by its content, not its container. */
		inline-size: fit-content;
		/* The Light register: the tone properties step; nothing that reflows animates. */
		transition:
			background var(--dur-instant) var(--ease),
			border-color var(--dur-instant) var(--ease),
			color var(--dur-instant) var(--ease),
			scale var(--dur-instant) var(--ease);
		/* What the state layer is mixed into. Each tone names its own ground below. */
		--btn-ground: transparent;
	}

	/* The state matrix for every tone, mixed from the ink; the word tones underline instead. */
	.btn:hover:not(:disabled, .quiet, .link) {
		background-color: color-mix(in srgb, currentColor var(--layer-hover), var(--btn-ground));
	}

	/* The control height every box and select wears. */
	.medium {
		padding: 0 var(--space-4);
		/* A minimum: a button may hold two lines. */
		min-block-size: var(--control-height);
	}

	.small {
		padding: 0 var(--space-3);
		min-block-size: var(--control-height-sm);
		font: var(--text-body-sm);
	}

	.full {
		inline-size: 100%;
	}

	/* No edge: the fill is the whole shape. */
	.primary {
		--btn-ground: var(--sift-accent);
		border-color: transparent;
		background-color: var(--btn-ground);
		color: var(--primary-foreground);
	}

	.secondary {
		--btn-ground: var(--sift-surface-2);
		border-color: var(--sift-line-strong);
		background-color: var(--btn-ground);
		color: var(--sift-ink);
	}

	.ghost {
		background-color: transparent;
		color: var(--sift-ink-2);
	}

	/*
	 * A word that acts, with no box ("Clear all"): not `ghost`'s padding, not `link`'s underline.
	 */
	.quiet {
		padding: 0;
		min-block-size: 0;
		border: 0;
		background: none;
		color: var(--sift-ink-3);
		font: var(--text-label);
	}

	/* The ink steps and the word underlines: colour alone is no hover state. */
	.quiet:hover:not(:disabled) {
		background: none;
		color: var(--sift-ink);
		text-decoration: underline;
	}

	/* A finger's reach on a phone through Pressable's ring, for small presses and word tones. */
	@media (max-width: 767px) {
		/* No weight, so a button placed absolutely keeps its placing. */
		:where(.btn.small, .quiet, .link) {
			position: relative;
		}

		.btn.small::after,
		.quiet::after,
		.link::after {
			content: '';
			position: absolute;
			inset-block: min(0px, calc((100% - var(--touch-target)) / 2));
			inset-inline: min(0px, calc((100% - var(--touch-target)) / 2));
		}
	}

	/* Part of the sentence: no padding, no minimum, its size and face. */
	.link {
		display: inline;
		padding: 0;
		text-align: inherit;
		min-block-size: 0;
		inline-size: auto;
		border: 0;
		background: none;
		color: var(--sift-accent-text);
		font: inherit;
		line-height: inherit;
		white-space: normal;
		text-decoration: underline;
	}

	/* Already marked by its accent and underline, so hover only confirms WHICH, with a heavier line. */
	.link:hover:not(:disabled) {
		text-decoration-thickness: 2px;
	}

	/* Switched on, read off `aria-pressed` so look and announcement agree, per tone. */
	.ghost[aria-pressed='true'] {
		--btn-ground: var(--sift-accent-bg);
		background-color: var(--btn-ground);
		color: var(--sift-accent-text);
	}

	.secondary[aria-pressed='true'] {
		--btn-ground: var(--sift-accent-bg);
		border-color: var(--sift-accent);
		background-color: var(--btn-ground);
		color: var(--sift-accent-text);
	}

	/* A quiet glyph comes up to the full ink on hover. */
	.ghost:hover:not(:disabled) {
		background-color: color-mix(in srgb, currentColor var(--layer-hover), var(--btn-ground));
		color: var(--sift-ink);
	}

	/* Outlined: a filled red button drags the eye to the danger. */
	.danger {
		border-color: var(--sift-bad);
		background-color: transparent;
		color: var(--sift-bad-text);
	}

	/* An ordinary button that only turns red under the pointer, so a column of them is no warning. */
	.danger-quiet {
		--btn-ground: var(--sift-surface-2);
		border-color: var(--sift-line-strong);
		background-color: var(--btn-ground);
		color: var(--sift-ink);
	}

	/* The red ground is the one the layers mix into, so hover and press answer as on every tone. */
	.danger-quiet:hover:not(:disabled),
	.danger-quiet:focus-visible:not(:disabled) {
		--btn-ground: var(--sift-bad-bg);
		border-color: var(--sift-bad);
		color: var(--sift-bad-text);
	}

	/* The same with no box, among bare icon buttons. */
	.danger-ghost {
		background-color: var(--btn-ground);
		color: var(--sift-ink-2);
	}

	.danger-ghost:hover:not(:disabled),
	.danger-ghost:focus-visible:not(:disabled) {
		--btn-ground: var(--sift-bad-bg);
		color: var(--sift-bad-text);
	}

	.btn:disabled {
		opacity: var(--disabled-opacity);
		cursor: default;
	}

	/* Busy is not dimmed; a button has no error state (`Problem` says it). */
	.btn[aria-busy='true'] {
		opacity: 1;
		cursor: progress;
	}

	/* Pressed, after every hover so it wins; not for the word tones. */
	.btn:active:not(:disabled, .quiet, .link) {
		background-color: color-mix(in srgb, currentColor var(--layer-pressed), var(--btn-ground));
		scale: var(--press-scale);
	}

	/* The focus ring is app.css's. */
	.label {
		display: inline-block;
	}

	/* Link words inline, so the underline reaches them. */
	.link > .label:not(:has(> :global(.icon))) {
		display: inline;
	}

	/*
	 * A glyph handed in as children is centred against the words; only there, or prose gains gaps.
	 */
	.label:has(> :global(.icon)) {
		display: inline-flex;
		align-items: center;
		gap: var(--space-2);
	}

	/* A glyph alone: line-height 0, so the box is the glyph. */
	.label:has(> :global(.icon:only-child)) {
		line-height: 0;
	}

	/* Tight to the words and quieter, punctuation rather than a second icon. */
	.trailing {
		display: inline-flex;
		margin-inline-start: calc(var(--space-2) * -0.5);
		opacity: 0.6;
	}
</style>
