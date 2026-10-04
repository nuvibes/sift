<script lang="ts" module>
	/* WHY NOT BITS-UI: bits-ui's Button adds exactly one thing to the site element: it renders an <a> when
	   given an href, and Sift's buttons are never links. Everything that matters here (type,
	   form participation, Enter and Space, the disabled semantics) is the real <button>'s already. */
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

	/**
	 * What the button is for, which decides how loud it is: `primary` the one thing the screen wants
	 * (at most one per view), `secondary` ordinary, `ghost` quieter than the text beside it, `danger`
	 * destroys data and is never a default, `link` a pressable run inside a sentence. No tone over
	 * media: the glass goes on the container, one compositing layer for the row.
	 */
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

	/**
	 * Rounded rectangle, or a circle: `circle` for an icon-only control ON something (a card's
	 * delete), outside the concentric-corner rule so it reads as dropped onto the surface. Offered
	 * only on the icon-only shape.
	 */
	export type ButtonShape = 'box' | 'circle';
</script>

<script lang="ts">
	/*
	 * The app's button: one component, so every screen's controls look and behave alike, kept a real
	 * <button> for the browser's own type, form, keyboard and screen-reader behaviour.
	 *
	 * What it wears follows what pressing it does:
	 *
	 * - Glyph and words: an act on a thing, wearing the act's glyph wherever it is offered (Add
	 *   `add`, Save `save`, Delete `delete`), or the mark of a thing it names.
	 * - Words only: a navigation, or an answer to what the screen asked (Cancel, Done, Back); an
	 *   arrow or chevron toward where it moves is punctuation.
	 * - Glyph only: where there is no room for words, inside a `Tooltip`, with an `aria-label`
	 *   holding its words.
	 *
	 * `quiet` and `link` are words; a glyph with a count is a mark. `scripts/check_button_glyphs.js`
	 * holds the rule. The glyph wears the button's ink; `scripts/check_glyph_reach.js` refuses a
	 * caller's rule tinting it.
	 */
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
		/**
		 * Waiting on something, and unable to be pressed again while it does (no double submit). A
		 * turning arc replaces the glyph, so the width holds; the words stay, saying what was pressed.
		 */
		busy?: boolean;
		/**
		 * A toggle that is currently ON: the accent and `aria-pressed` together, so the look and the
		 * announcement cannot come apart. About the thing it controls, unlike `disabled` or `busy`.
		 */
		pressed?: boolean;
		/**
		 * Draw the glyph FILLED, whatever the pressed state is, for a glyph filled by what it IS (the
		 * folder over the wall); filled otherwise means "the current one", which `pressed` gives.
		 */
		iconFilled?: boolean;
		/** Fills the space it is given. Off by default: a button as wide as the page reads as a
		 *  banner. */
		full?: boolean;
		/**
		 * Extra classes from the caller, for POSITION only, never for the button's own look. Merged,
		 * not spread: a spread `class` would replace `btn secondary medium` and leave a grey slab.
		 */
		class?: string;
		/** See `ButtonShape`. Refused on a button with words in it, one interface down. */
		shape?: ButtonShape;
		/**
		 * A glyph AFTER the words (a menu trigger's chevron): a prop, so it is a sibling of the label
		 * in the flex row and centred, rather than riding high on the label's baseline.
		 */
		trailing?: IconName;
		/** The glyph's size, from the sanctioned list. 18 fits the control; a bar's Play asks for more. */
		iconSize?: IconSize;
	}

	/**
	 * A button with words on it, optionally with a glyph before them. The label carries the meaning.
	 */
	interface Labelled extends Common {
		icon?: IconName;
		children: Snippet;
		/* A circle with words in it is a lozenge, so this is a type error rather than a note in a
		   comment. Same reasoning as the aria-label below: a rule a machine checks is a rule. */
		shape?: never;
		/* Words are laid out on a line and have a height of their own; only a glyph stands tall. */
		tall?: never;
	}

	/**
	 * A button that is only a glyph: a toolbar control, a close, a row action. The union makes its
	 * `aria-label` a type error to forget, since nothing else names it.
	 */
	interface IconOnly extends Common {
		icon: IconName;
		'aria-label': string;
		children?: never;
		/**
		 * As tall as what holds it, rather than square: the arrow at each end of a sideways strip, so
		 * the way on is as tall as the row it moves; the width stays square's.
		 */
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

	/* What holds the button decides its height when the caller named none: a settings row's press
	   is the small one, a press beside a field takes the field's height. See `press-size`. */
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
	<!-- The arc takes the glyph's place, so nothing moves while pressed; with no glyph it goes before
     the words. -->
	{#if busy}
		<Spinner size={16} />
	{:else if icon}
		<!-- Filled while it is on, so "the current one" is said in shape as well as colour. -->
		<Icon name={icon} size={iconSize} filled={iconFilled || pressed === true} />
	{/if}
	{#if children}<span class="label">{@render children()}</span>{/if}
	<!-- After the words, and quieter than them: a trailing glyph is punctuation. It says which way a
	     menu opens or where a control goes, and it is not the thing being read. -->
	{#if trailing}<span class="trailing"><Icon name={trailing} size={16} /></span>{/if}
</button>

<style>
	/* Square, so the glyph sits in the middle of a button rather than adrift in a wide one. The
	   width is the height, which is also what makes a row of them line up with each other. */
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

	/* See `ButtonShape`. Two classes deep so it beats the `.btn` radius outright rather than relying
	   on where it happens to sit in the file. */
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
		/* Sized by its content, not by its container. A button that fills the row is the commonest
		   way a pane stops looking like the pane beside it. */
		inline-size: fit-content;
		/*
		 * The Light register, once, for every tone: the three properties a tone changes step rather
		 * than snap. Nothing that reflows is animated.
		 */
		transition:
			background var(--dur-instant) var(--ease),
			border-color var(--dur-instant) var(--ease),
			color var(--dur-instant) var(--ease),
			scale var(--dur-instant) var(--ease);
		/* What the state layer is mixed into. Each tone names its own ground below. */
		--btn-ground: transparent;
	}

	/*
	 * The state matrix, once for every tone: the hover layer under the pointer, the pressed layer and
	 * a little give under a press, mixed from the button's own ink (`--layer-hover`). The word tones
	 * underline instead; disabled takes neither.
	 */
	.btn:hover:not(:disabled, .quiet, .link) {
		background-color: color-mix(in srgb, currentColor var(--layer-hover), var(--btn-ground));
	}

	/* The control height, the same token every text box and select wears, so a Save beside an
	   icon-only Options is one height. */
	.medium {
		padding: 0 var(--space-4);
		/*
		 * A minimum, not a fixed height: a button may hold a picture over two lines.
		 */
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

	/*
	 * No edge: the fill is the whole shape, as a lone primary should be. The border stays declared
	 * transparent on `.btn`, so every tone measures the same.
	 */
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
	 * A word that acts, with no box around it at all ("Clear all" after chips, "View 26 more"): not
	 * `ghost`, which keeps a control's padding and hit area, and not `link`, which sits inside a
	 * sentence at its size with an underline.
	 */
	.quiet {
		padding: 0;
		min-block-size: 0;
		border: 0;
		background: none;
		color: var(--sift-ink-3);
		font: var(--text-label);
	}

	/*
	 * The ink steps AND the word is underlined: colour alone is no hover state, and this tone exists
	 * to have no ground. The underline sits outside the line box, so nothing reflows.
	 */
	.quiet:hover:not(:disabled) {
		background: none;
		color: var(--sift-ink);
		text-decoration: underline;
	}

	/* A finger's reach on a phone, `Pressable`'s invisible ring, for the small presses and the word
	   tones, taking no room in the layout. */
	@media (max-width: 767px) {
		/* At no weight, so a caller that places its button itself (absolutely, in a corner) keeps
		   its placing: such a button is a containing block for the ring already. */
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

	/*
	 * Part of the sentence, not a slab in it: no padding, minimum height, fitted width or nowrap, and
	 * the size and face of what is around it.
	 */
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

	/*
	 * Switched on (the matrix's SELECTED; `pressed`), read off the attribute so look and announcement
	 * agree. Per tone, so "on" is legible against each; the ground is the one the layers mix into.
	 */
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

	/* A glyph in the quiet ink comes up to the full one as well as taking the layer, so an icon on
	   its own reads as reached for. */
	.ghost:hover:not(:disabled) {
		background-color: color-mix(in srgb, currentColor var(--layer-hover), var(--btn-ground));
		color: var(--sift-ink);
	}

	/* Outlined rather than filled. A filled red button among ordinary ones drags the eye to
	   whichever action happens to be dangerous instead of to the one you came to do. */
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

	/*
	 * The same idea with no box at all, for icon-only buttons among bare ones (the face card), where
	 * `danger-quiet`'s ground would read as the important one.
	 */
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

	/*
	 * Busy is not refused: disabled against a second press, not dimmed. A button has no ERROR state;
	 * what failed is said in words under it (`Problem`).
	 */
	.btn[aria-busy='true'] {
		opacity: 1;
		cursor: progress;
	}

	/* Pressed, after every tone's hover so it wins while the pointer is also over the button. No
	   word tone: they have no ground, and an underline has no pressed step. */
	.btn:active:not(:disabled, .quiet, .link) {
		background-color: color-mix(in srgb, currentColor var(--layer-pressed), var(--btn-ground));
		scale: var(--press-scale);
	}

	/* No focus rule here: the ring is `app.css`'s `:focus-visible`, drawn in one place for
	   everything focusable. */
	.label {
		display: inline-block;
	}

	/* Words in the link tone run inline: an inline-block box takes no part in its parent's
	   underline, so the link's permanent underline would never be drawn under them. */
	.link > .label:not(:has(> :global(.icon))) {
		display: inline;
	}

	/*
	 * A glyph handed in as children, beside words, is centred against them, as `icon=` would be,
	 * rather than riding high in an `inline-block` text box. Only where the label holds a glyph, or
	 * prose with emphasis would gain flex gaps. `:global`, `Icon`'s class.
	 */
	.label:has(> :global(.icon)) {
		display: inline-flex;
		align-items: center;
		gap: var(--space-2);
	}

	/*
	 * A label holding nothing but a glyph is not text: `line-height: 0` drops the descender strut so
	 * the box is the glyph. `:global`, `Icon`'s class.
	 */
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
