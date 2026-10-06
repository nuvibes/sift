<script module lang="ts">
	/*
	 * The least room this bar is worth drawing in. It lives here because it is a fact about the bar
	 * rather than about the wall that decides whether to draw one.
	 *
	 * Written as the sum of its parts. The bar shrinks (see `StageBar`'s `.controls`), so nothing
	 * is clipped at any width; this answers when it stops being useful, which is when the controls
	 * come below what the transport, the sound, the drawer and the ways out need.
	 *
	 * The numbers at the start of the controls' row are wider on a wall of nine than a wall of two:
	 * 112 is the two-column figure. A wider picker folds into one press where the bar runs out.
	 */

	/** What the controls need for all of it: the transport, the sound, the drawer, the ways out. */
	const LEAST_FOR_THE_CONTROLS = 400;
	/** The picker of numbers at the start of the row, on the wall Sift opens with. Measured. */
	const LEADING_EDGE = 112;
	/** The bar's own padding either side, the gap after the picker, and its hairline. */
	const THE_BAR_ITSELF = 24 + 12 + 2;
	/** How far the bar stays clear of the wall's own edges. `--space-8`, doubled. */
	const CLEAR_OF_THE_EDGES = 32;

	export const LEAST_FOR_THE_BAR =
		LEAST_FOR_THE_CONTROLS + LEADING_EDGE + THE_BAR_ITSELF + CLEAR_OF_THE_EDGES;
</script>

<script lang="ts">
	/* NOT ON THE GALLERY: this reads Theater's wall and drives it. A second live copy would be a
	   second bar acting on the same cells, which is the app drawn twice. Everything it is MADE of
	   (the bar, the buttons, the player bar) is on the gallery. */

	/*
	 * One bar for the whole wall, while the window is filled.
	 *
	 * ## Why one
	 *
	 * Every cell carries the player's own bar: a scrubber, the transport, a clock, the volume and a
	 * drawer of eleven controls. That is right in a window, where a cell is a picture with chrome
	 * on it. Filling the screen with four of them would put four copies of the same bar on one
	 * screen, and the bar wants about four hundred pixels, which a wall of four does not have to
	 * give, so each cell would draw a cut-down version of it. While this bar is up a cell draws no
	 * chrome at all, the step arrows included.
	 *
	 * ## The picker
	 *
	 * A row of numbers at the start of the controls' row, `All` first. Pointing at one marks the cell
	 * it belongs to on the wall behind, with the accent wash the application draws over anything a
	 * drag is aimed at.
	 *
	 * ## Its width does not move
	 *
	 * Pressing a number must not resize the bar under the hand pressing it. Two things would: a
	 * head saying what the cell is playing, which is as long as the query, and numbers as wide as
	 * their own digits. So nothing on the bar names the cell's filter, and the numbers are a fixed
	 * square. The filter is not on this bar: Filter on the bar above the
	 * wall chooses what the chosen cell plays, and so does the cell's own menu (What it plays).
	 */
	import { Button } from '$lib/components/common';
	import ContextMenuItem from '$lib/components/common/ContextMenuItem.svelte';
	import MenuButton from '$lib/components/common/MenuButton.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { stage } from '$lib/components/shell/stage.svelte';
	import { ACTS, keyOf } from '$lib/player/acts';
	import StageBar from './StageBar.svelte';
	import CellControls from './CellControls.svelte';
	import { aims } from '$lib/theater/aim';
	import Icon from '$lib/components/Icon.svelte';
	import { controlledWords, screenOffer } from '$lib/remote/offer.svelte';
	import type { Wall } from '$lib/theater/wall.svelte';

	interface Props {
		wall: Wall;
		/** Whether there is a wall to control at all. */
		up: boolean;
		/**
		 * Gone quiet with the rest of the wall's chrome.
		 *
		 * Handed in rather than read off the shell, because a wall is drawn on three surfaces with
		 * three different clocks and only the thing drawing it knows which one applies. See
		 * `TheaterWall`, which is where that is answered once for the bar and the cells together.
		 */
		quiet?: boolean;
		/** How tall the bar has come out, so the wall can keep the strip clear of it. */
		tall?: number;
	}

	let { wall, up, quiet = false, tall = $bindable(0) }: Props = $props();

	/* Which cell the bar is acting on: the one the keyboard is on, which the numbers below set. One
	   answer rather than a second selection of this bar's own: a bar that could be pointed at cell
	   two while the keys talked to cell three would be two answers to one question. */
	const chosen = $derived(Math.min(wall.focused, wall.cells.length - 1));
	const cell = $derived(wall.cells[chosen]);

	/* Nothing is being aimed at once the bar goes, or the wall would keep a wash on a cell nobody is
	   pointing at: leaving fullscreen with the pointer over a number is exactly how that happens.
	   The bar fading counts as going: it is `inert` while it is quiet, so the pointer never leaves
	   the number it was on and the wash would stay lit under a bar that is no longer there. */
	$effect(() => {
		if (!up || quiet) wall.aiming = null;
	});

	/* Which phones are driving this wall, in words, or nothing while none is. */
	const controlled = $derived(
		screenOffer.wallControlled ? controlledWords(screenOffer.controlledBy) : ''
	);

	/* The numbers fold into one press when the row wants more than the bar can grow to; folded, they
	   stay laid out out of sight, so the width they want is still known. */
	let room = $state(0);
	let numbersWide = $state(0);
	let foldWide = $state(0);
	const folds = (beside: number) =>
		room > 0 && numbersWide + beside > room && (foldWide === 0 || foldWide < numbersWide);
	const onCell = $derived(wall.everyCell ? 'All' : `${chosen + 1} of ${wall.cells.length}`);
	const foldNames = $derived(wall.everyCell ? 'every cell' : `cell ${onCell}`);
</script>

<!-- Quiet when the rest of the chrome is. `stage.barHidden` is the one clock a filled screen runs;
     a second one here would be a bar that went away at its own moment. -->
<StageBar open={up && cell !== undefined} {quiet} bind:tall bind:room label="The wall's controls">
	{#if cell}
		<!-- The cell's own bar, unchanged, at the width it was designed for, with the numbers at the
		     start of its controls' row. -->
		<CellControls
			{wall}
			{cell}
			index={chosen}
			position={cell.position}
			duration={cell.duration}
			onseek={(seconds) => cell.seek?.(seconds)}
			filled
			{picker}
		/>
	{/if}
</StageBar>

<!--
	BEING REMOTE CONTROLLED, at the head of the numbers: the Remote's own glyph in the accent while
	a phone is driving this wall, gone when it lets go. A mark, not a button, as the cell's own waiting
	mark is: the words are the shared tooltip, and there is nothing to press.
-->
{#snippet controlledMark()}
	<span class="controlled">
		<Tooltip label={controlled} placement="top">
			<Icon name="settings_remote" size={18} label={controlled} />
		</Tooltip>
	</span>
{/snippet}

<!--
	The cell picker, at the start of the controls' row, under the start of the timeline.

	The scrubber is what somebody reaches for constantly and the numbers are pressed once in a
	while, so the timeline runs the width of the bar over them: beside it, on a wall of nine, they
	would push the timeline's start a third of the way across the bar. The transport keeps the
	middle of the row, so the numbers take its start.
-->
{#snippet picker(beside: number)}
	{@const folded = folds(beside)}
	<div class="picker">
		<!--
		EVERY CELL AS ONE ROW OF NUMBERS, on the controls' line.

		Not in the WALL'S OWN SHAPE (a two-by-two block of numbers for a two-by-two wall, with the
		strip's five in a row underneath). That is a good property and it is given up deliberately:
		a picker in the shape of a wall of nine is three rows tall, which makes the bar three rows
		tall, and the thing it would mirror is on screen directly above it anyway.

		One row, spread evenly, however many there are, so two look like two and nine look the same
		way. The bar grows with them because this sits in the row rather than over it.
	-->
		<div
			class="numbers"
			class:folded
			role="group"
			aria-label="Which cell to control"
			aria-hidden={folded || undefined}
			inert={folded || undefined}
			bind:offsetWidth={numbersWide}
		>
			{#if controlled}{@render controlledMark()}{/if}
			<!--
				EVERY CELL AT ONCE, at the head of the numbers: it is one of them.

				The wall's own verbs reach all of it (stop everything, silence everything), and without
				this nothing else would: "next file in all four" and "five seconds back in all four" could
				not be said at all, and "mute this one" and "mute everything" would be a modifier apart
				with no way to see which you were about to do. It is a SELECTION rather than a second set of controls, so
				the row beside it does not change: pick this, and every verb on the bar lands on the whole
				wall.

				A word rather than a digit, so it is wider than the numbers beside it, and fixed, so the
		row does not move when it is pressed.
		-->
			<Tooltip label={ACTS.everyCell} placement="top" shortcut={keyOf('everyCell', 'theater')}>
				<Button
					size="small"
					tone="ghost"
					class="pick every {wall.everyCell ? 'on' : ''}"
					pressed={wall.everyCell}
					aria-label="Controls for every cell"
					{...aims(wall, 'every')}
					onclick={() => wall.focusEvery()}
				>
					All
				</Button>
			</Tooltip>
			{#each wall.cells as one, at (one.key)}
				<Button
					size="small"
					tone="ghost"
					class="pick {at === chosen && !wall.everyCell ? 'on' : ''}"
					pressed={at === chosen && !wall.everyCell}
					aria-label="Controls for cell {at + 1}"
					{...aims(wall, at)}
					onclick={() => wall.chooseByNumber(at)}
				>
					{at + 1}
				</Button>
			{/each}
		</div>
		{#if folded}
			<div class="fold" bind:offsetWidth={foldWide}>
				{#if controlled}{@render controlledMark()}{/if}
				<MenuButton label="Which cell to control" side="top" portalTo={stage.whatFillsTheWindow}>
					{#snippet trigger({ props })}
						<Tooltip label="Which cell to control" placement="top">
							<Button
								{...props}
								size="small"
								tone="ghost"
								class="pick on"
								trailing="expand_more"
								aria-label="Choose which cell to control, now {foldNames}"
							>
								{onCell}
							</Button>
						</Tooltip>
					{/snippet}
					<ContextMenuItem
						label="All"
						checked={wall.everyCell}
						oneOf
						onselect={() => wall.focusEvery()}
					/>
					{#each wall.cells as one, at (one.key)}
						<ContextMenuItem
							label={`${at + 1}`}
							checked={at === chosen && !wall.everyCell}
							oneOf
							onselect={() => wall.chooseByNumber(at)}
						/>
					{/each}
				</MenuButton>
			</div>
		{/if}
	</div>
{/snippet}

<style>
	/*
	 * Square whatever the digit, so the row is one width whichever cell is chosen. `:global`: the
	 * class lands on the shared button's element.
	 */
	.picker :global(.pick) {
		min-inline-size: 28px;
		inline-size: 28px;
		block-size: 28px;
		padding-inline: 0;
		font-variant-numeric: tabular-nums;
	}

	/* One row, evenly spaced, whatever the wall holds; the bar grows to hold it until it folds. */
	.numbers,
	.fold {
		display: flex;
		flex: none;
		align-items: center;
		gap: var(--space-1);
	}

	.picker {
		position: relative;
		display: flex;
	}

	/* Out of sight and spilling toward the start, which adds nothing to the row's measured width. */
	.numbers.folded {
		position: absolute;
		inset-inline-end: 0;
		inline-size: max-content;
		visibility: hidden;
	}

	.fold :global(.pick) {
		min-inline-size: auto;
		inline-size: auto;
		padding-inline: var(--space-2);
	}

	/* The accent, and the whole mark: it states a fact about the wall, so it is lit, not dimmed. */
	.controlled {
		display: inline-flex;
		color: var(--sift-accent-text);
	}

	/* A word, wider than the squares, drawn from its start so the time above stands over it. */
	.numbers :global(.pick.every) {
		min-inline-size: 44px;
		inline-size: 44px;
		justify-content: flex-start;
		padding-inline-start: var(--space-2);
	}

	/*
	 * The one being controlled: the app's own selected ground, and an accent edge.
	 *
	 * Not the shared button's `primary` tone, a solid accent block, which in this app means "the
	 * thing to press" rather than "the one selected". Not an accent wash either: a wash is
	 * translucent, and this bar floats on a scrim over moving pictures, so the chip would take its
	 * colour from whatever frame was behind it.
	 *
	 * `--sift-accent-bg` is the token layer's answer: an opaque surface, "the ground under an
	 * active nav row, a selected chip", which reads the same over any frame.
	 */
	.picker :global(.pick.on) {
		border: 1px solid var(--sift-accent);
		background: var(--sift-accent-bg);
		color: var(--sift-accent-text);
	}
</style>
