<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'Spinner',
		category: 'primitive',
		role: 'a turning arc and a word for a screen reader, while something is in flight',
		basis: 'own',
		states: ['sm', 'md', 'lg']
	} satisfies DesignEntry;

	/** 12 inside a chip or a badge, 16 beside a word, 20 on its own, 28 for a whole panel.
	 *
	 * 12 is what a spinner riding inside something else is. No rung sits between it and 16: a rung
	 * nothing in the application reads is a rung the next call site picks by accident. */
	export type SpinnerSize = 10 | 12 | 16 | 20 | 28;
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: there is no spinner in it and there will not be. A spinner has no focus, no
	   keyboard, no state anything can be in: it is a rotating arc and a live-region word. The
	   library ships behaviour worth sharing and this has none to share. */

	/*
	 * Something is working, and it is worth watching.
	 *
	 * ## A spinner, and not the shimmer
	 *
	 * Sift has one of the two in `Skeleton`, which says "this is becoming content" and is the right
	 * answer for a thing whose shape is known and whose contents are on the way: a tile, a row, a
	 * card. This is the other half, for work with no shape at all: a button waiting on the server, a
	 * badge on a job that is running, a panel checking something.
	 *
	 * Reaching for the wrong one is not a small mistake. A shimmer where a spinner belongs draws the
	 * outline of a thing that is never going to appear there.
	 *
	 * ## The arc rather than a ring of dots
	 *
	 * One turning arc reads as continuous work at every size, including 12 pixels inside a badge,
	 * where a ring of dots becomes a smudge. It is drawn with a border rather than an image so it
	 * takes the colour of whatever it is inside: a spinner on a primary button has to be the
	 * button's own text colour, and one that carried its own would be wrong on four of five tones.
	 */
	interface Props {
		size?: SpinnerSize;
		/**
		 * What is being waited for, said out loud.
		 *
		 * A spinner beside its own explanation ("Saving...") passes nothing, because the words are
		 * already there to be read. A spinner standing alone takes a label, or somebody who cannot
		 * see it is told nothing at all.
		 */
		label?: string;
	}

	let { size = 16, label }: Props = $props();
</script>

<span
	class="spinner size-{size}"
	role={label ? 'status' : undefined}
	aria-label={label}
	aria-hidden={label ? undefined : 'true'}
></span>

<style>
	/*
	 * Three of the four sides transparent, so what is left is an arc, and the whole thing turns.
	 *
	 * `currentColor` is the load-bearing part: the arc is whatever colour the text around it is, so
	 * one component is correct inside a primary button, a danger button, a badge and a panel without
	 * being told which it is in.
	 */
	.spinner {
		inline-size: var(--spinner-size);
		block-size: var(--spinner-size);
		border-width: var(--spinner-border);
		display: inline-block;
		flex: none;
		/*
		 * The size is the whole arc, border included. Otherwise the border is added outside the
		 * declared box and every size draws about five pixels larger than its name. With it, 14
		 * means fourteen, which makes a rung comparable to a Material Symbol: a symbol at 16px
		 * paints about 13.3px of ink (the glyph occupies roughly 20 of its 24-unit grid), so a 14px
		 * arc and a 16px glyph are the same circle on screen.
		 */
		box-sizing: border-box;
		border-radius: var(--radius-full);
		border-style: solid;
		border-color: currentColor;
		border-inline-end-color: transparent;
		border-block-end-color: transparent;
		opacity: 0.9;
		animation: turn var(--dur-loop) linear infinite;
		vertical-align: -0.125em;
	}

	/*
	 * A heavy enough arc at every size: a hairline turning arc on a dark ground reads as a flicker,
	 * and at the smallest size vanishes into the text beside it. The thickness grows with the size.
	 *
	 * Each rung sets only the two numbers; the one rule above turns them into a box, so the
	 * decision is written once.
	 *
	 * The smallest rung: inside a badge, beside a 16px glyph whose disc is 12 wide, where 12 would
	 * read as the largest thing in the row.
	 */
	.size-10 {
		--spinner-size: 10px;
		--spinner-border: 1.5px;
	}

	.size-12 {
		--spinner-size: 12px;
		--spinner-border: 2px;
	}

	.size-16 {
		--spinner-size: 16px;
		--spinner-border: 2.5px;
	}

	.size-20 {
		--spinner-size: 20px;
		--spinner-border: 3px;
	}

	.size-28 {
		--spinner-size: 28px;
		--spinner-border: 4px;
	}

	/*
	 * Reduced motion stills it, and closes the ring.
	 *
	 * Nothing repeats under reduced motion, a pulse included: `app.css` holds every animation to one
	 * instant pass. A still ARC reads as a rendering fault, a ring with a piece missing; a whole ring,
	 * fainter than the arc, reads as a mark that something is under way, and a spinner standing alone
	 * still says so in words through its label.
	 */
	:global(:root[data-motion='reduce']) .spinner {
		animation: none;
		border-color: currentColor;
		opacity: 0.5;
	}
</style>
