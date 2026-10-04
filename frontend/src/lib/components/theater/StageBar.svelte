<script lang="ts">
	/*
	 * BESIDE ITS ONE USER, not in `common/`, and that is the repository's own rule rather than a
	 * demotion. Something in `common/` is a claim that more than one screen wants it, and a contract
	 * test refuses a claim with one consumer, because that is what extracting too early looks like
	 * from the outside, and it is a habit worth catching.
	 *
	 * It is written to be moved: it knows nothing about Theater, takes what it draws as snippets, and
	 * would go back to `common/` unchanged the day a second filled screen wants one. The player's own
	 * fullscreen is the obvious candidate and does not need it today.
	 */

	/*
	 * THE BAR THAT COMES UP FROM THE BOTTOM WHILE A SCREEN IS FILLING THE WINDOW.
	 *
	 * ## What it is for
	 *
	 * A screen that fills the window has no room for chrome and still needs controls. The answer
	 * everywhere else in this app is a bar that appears over the picture and fades when nobody is
	 * doing anything, but on a screen showing SEVERAL things at once, one bar per thing is the
	 * wrong shape: four videos on a 1400px wall leave each of them a bar too narrow for its own
	 * controls, and four copies of the same six buttons is four times the thing to read.
	 *
	 * So: one bar, for whichever one is selected, and a way to change which. That is the whole idea.
	 *
	 * ## Where its look comes from, and why neither half is new
	 *
	 * The SHAPE is the selection bar's: floating over the screen rather than pushing it, centred on
	 * the screen rather than on the window, a full-radius pill, and it RISES rather than appearing,
	 * because it is the answer to something that just happened somewhere else and the travel is what
	 * connects the two.
	 *
	 * The SURFACE is the facts panel's: a hairline edge and a blur, so it reads as a pane over the
	 * picture rather than as a hole punched in it, and it stays legible over a bright frame where a
	 * flat scrim does not. That was worked out for a panel that sits over video, which is exactly
	 * what this does.
	 *
	 * Both are cited rather than copied so that a change to either is a change to one thing.
	 */
	import type { Snippet } from 'svelte';
	import { arrive } from '$lib/shell/motion.svelte';

	interface Props {
		/** Whether it is up. Absent is down; the bar draws nothing at all rather than an empty strip. */
		open: boolean;
		/** What a screen reader calls the region. */
		label: string;
		/**
		 * Gone quiet with the rest of the chrome: faded out, and not reachable while it is.
		 *
		 * A filled screen puts its chrome on an idle clock and brings it back on the next thing
		 * anybody does. This bar is part of that chrome and has to go and come back WITH it: one
		 * gesture, one answer, rather than a screen where the top strip has gone and a bar is still
		 * sitting over the picture.
		 *
		 * Faded rather than unmounted, which is the difference between this and `open`. The bar is
		 * floating over the screen and takes no space, so there is nothing to give back by removing
		 * it, and unmounting would replay its arrival every time a pointer moved, which is a bar
		 * that leaps up from the edge every few seconds.
		 */
		quiet?: boolean;
		/**
		 * How tall this bar has come out, in pixels, whenever that changes.
		 *
		 * The bar floats over the foot of the screen, so anything that must not be under it has to
		 * be told how much room it takes. Measured and handed over rather than written as a number
		 * elsewhere: the height depends on what a caller puts in the bar.
		 *
		 * The border box (`offsetHeight` below, not `clientHeight`): this bar has a hairline on
		 * each edge, and a content height leaves both out, so anything meeting the bar's top edge
		 * exactly would meet it two pixels late.
		 */
		tall?: number;
		/**
		 * The picker, at the leading edge: which of the several things the controls act on.
		 *
		 * Separated from the controls rather than left to the caller to lay out, because the two are
		 * read in that order and a bar that put them the other way round would be answering "do this"
		 * before "to what".
		 */
		lead?: Snippet;
		/**
		 * A mark standing over the picker, on the timeline's row: the leading column is empty
		 * there, so a mark about the whole bar sits in it without making the bar any taller.
		 */
		overLead?: Snippet;
		/** The controls, for whichever one is selected. */
		children: Snippet;
	}

	let {
		open,
		label,
		lead,
		overLead,
		children,
		quiet = false,
		tall = $bindable(0)
	}: Props = $props();
</script>

{#if open}
	<!-- A region rather than a dialog: nothing is trapped and nothing behind it is blocked. What is
	     on the screen is still playing, and a bar that took the keyboard to announce itself would be
	     in the way of exactly the thing somebody is watching. -->
	<div
		class="stage-bar"
		class:led={lead !== undefined}
		bind:offsetHeight={tall}
		class:quiet
		role="region"
		aria-label={label}
		aria-hidden={quiet}
		inert={quiet || undefined}
		transition:arrive={{ y: 64, pace: 'slow', spring: true }}
	>
		{#if lead}
			<div class="lead">{@render lead()}</div>
		{/if}
		{#if overLead}
			<div class="over-lead">{@render overLead()}</div>
		{/if}
		<div class="controls">{@render children()}</div>
	</div>
{/if}

<style>
	/*
	 * Centred on THE SCREEN, not on the window, and absolute rather than fixed.
	 *
	 * The same decision the selection bar records, for the same reason and with the same consequence
	 * if it is got wrong: `fixed` at `inset-inline-start: 50%` centres on the browser window, which is
	 * not where the app is. Its container IS the box, so it follows the rail, the top bar and the
	 * frame with nothing to remember and no width to keep in step.
	 */
	.stage-bar {
		position: absolute;
		inset-block-end: var(--space-4);
		inset-inline-start: 50%;
		translate: -50% 0;
		z-index: var(--z-bar);
		/*
		 * The picker in one column and the controls in the other, on rows the controls share (see
		 * `.controls`): the picker stands in the row named `transport`, level with Play whatever
		 * opens under that row. Its width is its contents, up to the ceiling; the controls' column
		 * is what gives way below that.
		 */
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		grid-template-rows: [scrubber] auto [transport] auto;
		column-gap: var(--space-3);
		inline-size: max-content;
		max-inline-size: calc(100% - var(--space-8));
		padding: var(--space-2) var(--space-3);
		border: 1px solid var(--sift-line);
		border-radius: var(--radius-xl);
		/* The facts panel's surface: a scrim and a blur, which is what stays legible over a bright
		   frame. A flat ground does not, and this sits over moving pictures by definition. */
		background: var(--sift-scrim);
		backdrop-filter: blur(var(--blur-glass));
		/*
		 * No shadow: it is a pane over a picture, not a card lifted off a page. A shadow reads as a
		 * grey smear under a floating row; the hairline and the blur separate it from what is
		 * behind.
		 *
		 * It slides, on the same token the bar at the top of a filled screen uses: the two are one
		 * gesture, the chrome of a filled screen arriving or leaving from its two edges, and a fade
		 * at one end with a slide at the other would read as two things. `--dur-slow` is the
		 * longest token the app animates chrome with, and entering a filled screen is its biggest
		 * change of state.
		 *
		 * The translate is composited and the layout never moves: the bar floats over the screen,
		 * so sliding it costs nothing and takes no space.
		 */
		transition:
			translate var(--dur-slow) var(--ease),
			opacity var(--dur-slow) var(--ease),
			visibility var(--dur-slow) var(--ease);
	}

	/*
	 * Faded out with the rest of the chrome.
	 *
	 * `visibility` alongside the opacity, and it is not decoration: an element at zero opacity is
	 * still hit by the pointer, so a bar nobody can see would go on swallowing clicks aimed at the
	 * picture underneath it. The transition on `visibility` is what lets the fade play out before it
	 * is taken away: a plain `hidden` would cut the fade off at the first frame.
	 */
	.stage-bar.quiet {
		/* Down past its own bottom edge. The bar is already offset from the edge by `--space-4`, so
		   its own height alone leaves a sliver showing; the step clears that too. */
		translate: -50% calc(100% + var(--space-4));
		opacity: 0;
		visibility: hidden;
		transition:
			translate var(--dur-slow) var(--ease-in),
			opacity var(--dur-slow) var(--ease-in),
			visibility var(--dur-slow) var(--ease-in);
	}

	/*
	 * WHATEVER IS PUT IN THIS BAR DOES NOT BRING ITS OWN GROUND.
	 *
	 * The player's bar carries a scrim gradient and a blur of its own, because it is normally drawn
	 * straight over a picture and has to make itself legible there. Inside this bar that is a second
	 * dark pane inside a dark pill: a black rectangle with rounded corners floating in the middle
	 * of the control.
	 *
	 * THIS is the surface. Anything dropped into it sits on it, so its own scrim, its own blur and
	 * its own corner all come off. `:global` because what is inside arrives through a snippet and is
	 * compiled in the caller's file; anchored to this component's own box, so the rule can only ever
	 * reach what is actually in the bar.
	 */
	.stage-bar :global(.player-bar) {
		padding: 0;
		border-radius: 0;
		background: none;
		backdrop-filter: none;
	}

	.stage-bar.led {
		grid-template-columns: auto minmax(0, 1fr);
	}

	/* A third row only while something is open under the transport (the timer, the sizes), so a
	   bar with nothing open has no empty row and no gap for it. */
	.stage-bar:has(:global(.player-bar > :nth-child(3))) {
		grid-template-rows: [scrubber] auto [transport] auto [below] auto;
	}

	.lead {
		grid-column: 1;
		grid-row: transport;
		align-self: center;
		display: flex;
		align-items: center;
		gap: var(--space-1);
	}

	/* Over the picker, on the timeline's row, and centred on the same column. */
	.over-lead {
		grid-column: 1;
		grid-row: scrubber;
		align-self: center;
		display: flex;
		align-items: center;
		justify-content: center;
	}

	/*
	 * The controls: their natural width where there is room, and down to whatever the wall gives
	 * where there is not (`minmax(0, 1fr)` above, and `min-inline-size: 0` here, which a grid item
	 * needs to go below its contents).
	 *
	 * The bar does not change width under the hand using it: the picker's numbers are fixed
	 * squares, and the player's clock keeps its room with or without a clock in it. Only the wall's
	 * shape grows the bar, and that changes when somebody changes it.
	 */
	.controls {
		grid-column: -2;
		grid-row: 1 / -1;
		display: grid;
		grid-template-rows: subgrid;
		min-inline-size: 0;
	}

	/* The player's bar lays its timeline, its transport row and whatever opens under it on the
	   bar's own rows, which is what puts the transport where the picker is. */
	.controls > :global(.player-bar) {
		grid-row: 1 / -1;
		grid-template-rows: subgrid;
	}
</style>
