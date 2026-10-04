<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'DoorCard',
		category: 'surface',
		role: 'a centered box on an otherwise empty page, for a screen that is one step',
		basis: 'own',
		states: ['default']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* NOT ON THE GALLERY: it fills the window. A card that covers the page cannot sit in a row of
	   specimens beside other components: the gallery shows the screens that use it instead
	   (sign-in, first-run setup, and the desktop client's connect screen). */
	/* WHY NOT BITS-UI: bits-ui has no page-level card. This is a centred box on an empty page:
	   layout and tokens, with no behaviour of its own at all. */
	/*
	 * The card on an empty page: one thing to do, before you are inside Sift.
	 *
	 * There are three screens like this and they have nothing in common except this shape: sign
	 * in, create the first account, and (in the desktop client) say which computer the library is
	 * on. Written twice from the same tokens they would stay identical right up until somebody
	 * changed one of them.
	 *
	 * It is layout and nothing else. No form, no submit, no state: what goes inside comes in as a
	 * snippet, so the screen that owns the decision still owns all of it.
	 */
	import type { Snippet } from 'svelte';

	import Logo from '$lib/components/Logo.svelte';

	interface Props {
		/** The one thing this screen is for, as a sentence fragment somebody reads first. */
		heading: string;
		/**
		 * Draw the heading, or keep it for assistive technology alone.
		 *
		 * Every screen needs a name (it is the page's only h1, and a page with no heading is one a
		 * screen reader cannot describe) but a heading that merely repeats the card's one button
		 * says the same word twice to everybody who can see it. Sign-in is exactly that case: a card
		 * with the brand mark, two fields and a button reading "Sign in" does not need a line above
		 * it reading "Sign in". A heading carrying something the button does not ("Where should Sift
		 * keep your library?") stays drawn.
		 */
		drawHeading?: boolean;
		/** A line under the heading, where the heading alone would leave a question. */
		explain?: string | null;
		/**
		 * Centre the whole stack rather than only the brand mark.
		 *
		 * For a card that asks ONE short thing. The default is a centred logo over left-aligned
		 * fields, which is right where the card carries a long heading ("Where should Sift keep your
		 * library?") or more than one field: a label sitting above a full-width box belongs at that
		 * box's left edge, and centring it over two of them reads as decoration.
		 *
		 * The lock screen is the other case. It is a single column of short things (the mark, the
		 * word Locked, the word PIN, one box, one button) and with only the logo centred the two
		 * words would be the one thing on the card aligned to nothing, as the rule about the button
		 * below says.
		 */
		centred?: boolean;
		/** Whatever the screen is: fields, a button, a message. */
		children: Snippet;
		/** Wraps the contents. A form when the screen submits one; a plain box when it does not. */
		onsubmit?: (event: SubmitEvent) => void;
		/** The card itself, for a screen that has to move it: the locked screen shakes it on a
		 *  wrong PIN, from script rather than a class, because a CSS animation runs once. */
		element?: HTMLElement | null;
	}

	let {
		heading,
		explain,
		children,
		onsubmit,
		drawHeading = true,
		centred = false,
		element = $bindable(null)
	}: Props = $props();
</script>

<main class="page">
	{#if onsubmit}
		<form class="card" class:centred bind:this={element} {onsubmit}>
			{@render inside()}
		</form>
	{:else}
		<div class="card" class:centred bind:this={element}>
			{@render inside()}
		</div>
	{/if}
</main>

{#snippet inside()}
	<Logo variant="lockup" height={32} />

	<h1 class:named-only={!drawHeading}>{heading}</h1>
	{#if explain}
		<p class="explain">{explain}</p>
	{/if}

	{@render children()}
{/snippet}

<style>
	.page {
		display: grid;
		place-items: center;
		/* THE WINDOW BY DEFAULT, THE CONTAINER WHEN SOMETHING ASKS.

		   `100dvh` is what the three real screens want: on each of them this card IS the screen, so
		   the page it sits on is the window. But a container that is not the window (the design
		   gallery draws this through a fixed-height box) would get a page a whole viewport tall
		   inside it, with the card centred on THAT and therefore below the box's bottom edge.

		   A custom property rather than a prop, because this is a fact the CONTAINER knows and the
		   card does not, and it wants to reach a rule in a stylesheet rather than a branch in the
		   markup. Unset everywhere except that box. */
		min-height: var(--door-height, 100dvh);
		/*
		 * The shorthand first, and the strip folded into what follows it.
		 *
		 * Under the desktop window's own title bar, so a card centred on this page is centred on
		 * the room it has. Zero in a browser, where the strip is not drawn (see `--window-chrome`
		 * in `app.css`). Padding rather than a margin, which would collapse through `body` and give
		 * the document a scrollbar (see `.shell` in the root layout).
		 *
		 * The order matters: a shorthand writes every side, so `padding: var(--space-4)` must come
		 * before the longhand that clears the strip, or it overwrites the clearing. The two agree
		 * in a browser, where the strip is zero, so the fault would show only in the packaged app.
		 */
		padding: var(--space-4);
		padding-block-start: calc(var(--window-chrome) + var(--space-4));
		background: var(--sift-bg);
	}

	.card {
		display: flex;
		flex-direction: column;
		gap: var(--space-4);
		width: 100%;
		max-width: 380px;
		/* Less room above the logo than around the rest: the brand mark sits near the top edge rather
		   than floating in the middle of a tall header. Full padding on the other three sides. */
		padding: var(--space-4) var(--space-8) var(--space-8);
		border-radius: var(--radius-xl);
		background: var(--sift-surface-1);
		border: 1px solid var(--sift-line);
		box-shadow: var(--elev-3);
	}

	/* The card is a flex column, so its default align-items: stretch would pull the logo out to the
	   full card width and squash it to the set height. Hold it at its natural size, centered over
	   the form: a centred brand mark above the left-aligned fields. */
	.card :global(.logo) {
		align-self: center;
	}

	/*
	 * THE ONE THING TO DO, CENTRED UNDER THE FIELDS.
	 *
	 * A button in a flex column takes its own width and sits at the start of the cross axis, so
	 * every one of these screens would have its action tucked against the left edge under a centred
	 * brand mark and a set of full-width fields, the only thing on the card aligned to nothing.
	 *
	 * Here rather than on each screen because it is a fact about this card's layout, and the three
	 * screens using it would otherwise each have to remember it. Direct children only: a button
	 * inside a field, or inside the password box's own reveal, keeps whatever its parent says.
	 */
	.card > :global(button) {
		align-self: center;
	}

	h1 {
		margin: 0;
		font: var(--text-h2);
		letter-spacing: var(--tracking-h2);
		color: var(--sift-ink);
		/* A name in a heading can be one unbroken token; it breaks rather than leaving the box. */
		overflow-wrap: anywhere;
	}

	/*
	 * A card that asks ONE short thing, centred all the way down.
	 *
	 * `text-align` rather than the flex alignment, deliberately. Centring the ITEMS would take the
	 * fields off full width (a flex child sitting at the cross-axis centre takes its own width)
	 * so the box would shrink to whatever it happened to contain and the button beside it would
	 * stop matching. This moves the words inside boxes that still stretch.
	 *
	 * `:global` on the label because it belongs to `Field`, and a component's stylesheet does not
	 * reach inside another one's. Scoped to `.centred .field` rather than written loose: a bare
	 * `:global(.label)` would be a rule on a generic class name, reaching every label in Sift the
	 * moment this file was imported anywhere.
	 */
	.centred {
		text-align: center;
	}

	.centred :global(.field .label) {
		/* The label is a block in a grid cell, so its own text needs telling as well. */
		text-align: center;
	}

	/* Named for assistive technology and drawn nowhere: the same treatment a full-bleed sheet's
	   title gets in `app.css`, and for the same reason: the screen still has to say what it is, it
	   just has nowhere useful to put the words. */
	h1.named-only {
		position: absolute;
		inline-size: 1px;
		block-size: 1px;
		margin: -1px;
		padding: 0;
		overflow: hidden;
		clip-path: inset(50%);
		white-space: nowrap;
	}

	.explain {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}
</style>
