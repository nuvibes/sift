<script lang="ts">
	/*
	 * A picture of a tile, with every mark it can carry sitting where it really sits.
	 *
	 * ## Why a picture rather than seven rows
	 *
	 * Because the question is "what do I want on my thumbnails", and the answer is a thing you look
	 * at. Seven rows reading "Times watched", "Pinned", "Shared or kept back" describe positions
	 * nobody can hold in their head: the top-left badges are a ROW, so turning two of them off
	 * moves the third, and the heart and the clock share the bottom edge. A row of switches says
	 * none of that. Pressing the badge you can see, in the corner it lives in, says all of it.
	 *
	 * ## The three answers, and why pressing cycles rather than choosing
	 *
	 * A menu per badge is seven menus over a photograph. Cycling is right where the answers are
	 * FEW, ORDERED and each one visible in the thing being pressed: lit, then faded, then crossed
	 * out, then lit again. Every press changes what you are looking at, which is the only way a
	 * cycle is ever honest: a cycle whose steps look alike is a control nobody can aim.
	 *
	 * They are still buttons, so Enter and Space step them and every one carries its name and its
	 * current answer for a screen reader, which is the half a picture cannot do.
	 *
	 * ## Where the words come from
	 *
	 * The server. Every label and sentence here is the setting's own declaration, read out of the
	 * panel, so the copy is written once, beside the thing it describes, and this file cannot
	 * drift from the registry the way a hand-written legend does.
	 */
	import Icon from '$lib/components/Icon.svelte';
	import { Pressable } from '$lib/components/common';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { markFor } from '$lib/library/sharing-marks';
	import type { SettingsPanel } from './panel.svelte';
	import {
		ALWAYS,
		DURATION_MARK,
		FAVORITE_MARK,
		GIF_MARK,
		HIDDEN_MARK,
		NEVER,
		ON_HOVER,
		O_COUNT_MARK,
		PINNED_MARK,
		RATING_MARK,
		SHARING_MARK,
		tileMarks,
		VIEWS_MARK,
		type MarkAnswer
	} from '$lib/grid/tile-marks.svelte';

	interface Props {
		/** Where the labels and help sentences come from. See the note above. */
		declarations: SettingsPanel;
	}

	let { declarations }: Props = $props();

	/* The order a press moves through, and it is the order of how much picture a mark costs. */
	const CYCLE: MarkAnswer[] = [ALWAYS, ON_HOVER, NEVER];

	const SAID: Record<MarkAnswer, string> = {
		[ALWAYS]: 'always',
		[ON_HOVER]: 'only when you point at the tile',
		[NEVER]: 'never'
	};

	function step(key: string): void {
		const at = CYCLE.indexOf(tileMarks.answer(key));
		void tileMarks.set(key, CYCLE[(at + 1) % CYCLE.length]).catch(() => {
			/* `set` puts the answer back itself. Nothing further to say here: the tile in front of
			   the person returns to what it was, which is the message. */
		});
	}

	/** The name the setting declares, or nothing until the declarations have arrived. */
	function named(key: string): string {
		return declarations.entry(key)?.label ?? '';
	}

	function explained(key: string): string {
		return declarations.entry(key)?.help ?? '';
	}

	/* The sharing badge, asked of the same function every tile and the legend below ask. Written
	   out as a glyph name here it would be a third copy of what "shared" looks like, free to
	   disagree with the other two. */
	const shared = markFor({ shared: true, shared_here: true }, { file: true });
</script>

<div class="picture">
	<!-- The ground. Deliberately not a real thumbnail: Settings has to draw the same thing on an
	     empty library as on a full one, and reaching into somebody's files for an illustration is a
	     screen that behaves differently depending on what they own. -->
	<div class="ground" aria-hidden="true"></div>

	<!-- Every mark below carries its setting's key as its id, so a search result or a link naming
	     one rings that mark rather than finding no row. -->
	<!-- The top-left row, in the order a tile draws them.
	     The warning that a file has gone missing shares this row and is not here: it is not a
	     preference, and the sentence under the picture says so. -->
	<div class="marks">
		{#each [{ key: VIEWS_MARK, icon: 'visibility' as const, tally: '24' }, { key: O_COUNT_MARK, icon: 'water_drop' as const, tally: '3' }, { key: PINNED_MARK, icon: 'keep' as const, tally: '' }, { key: SHARING_MARK, icon: shared?.icon ?? ('group' as const), tally: '' }, { key: HIDDEN_MARK, icon: 'visibility_off' as const, tally: '' }] as one (one.key)}
			<Tooltip label={`${named(one.key)} \u2014 ${explained(one.key)}`} placement="bottom">
				<Pressable
					feedback="lift"
					radius="sm"
					class="target"
					id={one.key}
					aria-label="{named(one.key)}: {SAID[tileMarks.answer(one.key)]}. Press to change."
					onclick={() => step(one.key)}
				>
					<span
						class="tile-badge answer-{tileMarks.answer(one.key)}"
						class:tile-badge-square={one.tally === ''}
					>
						<Icon name={one.icon} size={16} />
						{#if one.tally}<span class="tally">{one.tally}</span>{/if}
						<span class="crossed" aria-hidden="true"></span>
					</span>
				</Pressable>
			</Tooltip>
		{/each}
	</div>

	<!-- The bottom-left pair: the heart you press and the score you gave. -->
	<div class="controls">
		<Tooltip label={`${named(FAVORITE_MARK)} \u2014 ${explained(FAVORITE_MARK)}`} placement="top">
			<Pressable
				feedback="lift"
				radius="sm"
				class="target"
				id={FAVORITE_MARK}
				aria-label="{named(FAVORITE_MARK)}: {SAID[
					tileMarks.answer(FAVORITE_MARK)
				]}. Press to change."
				onclick={() => step(FAVORITE_MARK)}
			>
				<!-- No badge ground: the heart sits straight on the picture, exactly as it does on a
				     real tile. -->
				<span class="tile-badge tile-badge-square bare answer-{tileMarks.answer(FAVORITE_MARK)}">
					<Icon name="favorite" size={16} filled />
					<span class="crossed" aria-hidden="true"></span>
				</span>
			</Pressable>
		</Tooltip>

		<Tooltip label={`${named(RATING_MARK)} \u2014 ${explained(RATING_MARK)}`} placement="top">
			<Pressable
				feedback="lift"
				radius="sm"
				class="target"
				id={RATING_MARK}
				aria-label="{named(RATING_MARK)}: {SAID[tileMarks.answer(RATING_MARK)]}. Press to change."
				onclick={() => step(RATING_MARK)}
			>
				<span class="tile-badge score answer-{tileMarks.answer(RATING_MARK)}">
					3&#9733;
					<span class="crossed" aria-hidden="true"></span>
				</span>
			</Pressable>
		</Tooltip>
	</div>

	<!-- The corner opposite the badges. On a real tile only ONE of these is ever drawn (a file is
	     a loop or it is a video), and they sit side by side here because both are pressable. -->
	<div class="clock">
		{#each [{ key: GIF_MARK, text: 'GIF' }, { key: DURATION_MARK, text: '1:52' }] as one (one.key)}
			<Tooltip label={`${named(one.key)} \u2014 ${explained(one.key)}`} placement="top">
				<Pressable
					feedback="lift"
					radius="sm"
					class="target"
					id={one.key}
					aria-label="{named(one.key)}: {SAID[tileMarks.answer(one.key)]}. Press to change."
					onclick={() => step(one.key)}
				>
					<span class="tile-badge score answer-{tileMarks.answer(one.key)}">
						{one.text}
						<span class="crossed" aria-hidden="true"></span>
					</span>
				</Pressable>
			</Tooltip>
		{/each}
	</div>
</div>

<ul class="answer-key">
	<li><span class="tile-badge tile-badge-square answer-always"></span> Always on the tile</li>
	<li><span class="tile-badge tile-badge-square answer-hover"></span> Only when you point at it</li>
	<li>
		<span class="tile-badge tile-badge-square answer-never"
			><span class="crossed" aria-hidden="true"></span></span
		> Never
	</li>
</ul>

<style>
	/*
	 * A tile, at a size somebody can aim at. Not the size the grid draws: the grid's smallest
	 * notch is 120px tall and hides the top row entirely, which would be a picture of the thing not
	 * working. Wide enough that the four badges along the top sit apart, and no wider than the
	 * column of reading this pane is.
	 */
	.picture {
		position: relative;
		inline-size: 100%;
		max-inline-size: 420px;
		aspect-ratio: 16 / 10;
		border-radius: var(--tile-radius);
		/* The edge, so the picture reads as a tile rather than as a patch of the page. On a real
		   wall a photograph supplies its own edge; a grey ground does not. */
		border: 1px solid var(--sift-line);
		overflow: hidden;
		margin-block: var(--space-4);
	}

	/*
	 * Where the photograph would be.
	 *
	 * Steps of the surface scale rather than a colour: it has to read as a picture's ground under
	 * every background, and a hue picked for one of them is wrong in the others. The lighter
	 * end is what makes the badges legible: their ground is a dark scrim, which is invisible over
	 * a page that is already dark and reads exactly as it does over a real thumbnail here.
	 *
	 * Called `ground` and not `thumb`. `Switch` claims `:global(.thumb)` for its knob, so a div by
	 * that name anywhere in the app is 16px square and round; see the note in that file.
	 */
	.ground {
		position: absolute;
		inset: 0;
		background: linear-gradient(135deg, var(--sift-surface-4), var(--sift-surface-2));
	}

	.marks {
		position: absolute;
		inset-block-start: var(--tile-chip-inset);
		inset-inline-start: var(--tile-chip-inset);
		display: flex;
		align-items: center;
		gap: var(--space-1);
	}

	.controls {
		position: absolute;
		inset-block-end: var(--tile-chip-inset);
		inset-inline-start: var(--tile-chip-inset);
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}

	.clock {
		position: absolute;
		inset-block-end: var(--tile-chip-inset);
		inset-inline-end: var(--tile-chip-inset);
		display: flex;
		align-items: center;
		gap: var(--space-1);
	}

	/*
	 * The press target. The badge inside it is `.tile-badge` from `app.css`: the same rule the
	 * real ones on a real tile use, so a picture of a badge is the size a badge is.
	 *
	 * `Pressable` rather than a `<button>` of this file's own: it carries the reset, the focus ring
	 * and the lift, three things a screen would otherwise write out for itself. The
	 * badge is a child rather than the surface itself because the two would otherwise argue about
	 * `display` and `border-radius` at equal specificity, which is a coin toss rather than a rule.
	 *
	 * From this file's own `.picture`, which outranks `Pressable`'s `display: block` whichever
	 * stylesheet loads last, and reaches no other file's picture.
	 */
	.picture :global(.target) {
		display: inline-flex;
	}

	/* Not a badge ground: the heart sits straight on the picture, as it does on a tile. */
	.bare {
		background: none;
	}

	/* No glyph inside them, so the ink correction is on the badge. See `--tile-chip-ink`. */
	.score,
	.tally {
		padding-block-start: var(--tile-chip-ink);
	}

	.score {
		white-space: nowrap;
	}

	/* --- the three answers, drawn ------------------------------------------------------------ */

	.answer-always {
		opacity: 1;
	}

	/* Faded, and it has to be a real step rather than a hint: the difference between this and lit is
	   the whole of what the middle answer looks like, and a 10% change is one nobody sees. */
	.answer-hover,
	.answer-never {
		opacity: 0.4;
	}

	/*
	 * The X, drawn over a mark that is off.
	 *
	 * Two bars rather than a glyph. A crossed-out eye is already one of the marks, so an X made of
	 * an icon would put a second meaning-carrying glyph inside a badge trying to say only "not this
	 * one", and at 22px the two would be indistinguishable.
	 */
	/*
	 * A SQUARE THE SIZE OF THE GLYPH, CENTRED, not the badge stretched.
	 *
	 * With `inset: 0` the bars would be as long as whatever they crossed and then rotated. That is
	 * fine on a square badge and wrong on every other one: the clock and the eye-and-count are
	 * twice as wide as they are tall, and a bar the width of the chip turned 45 degrees reaches
	 * `width x 0.707` ABOVE and BELOW it, hanging out of the chip and into the picture.
	 *
	 * Fixed at the glyph's own size instead, so the mark is the same on a square badge and a wide
	 * one, and covers what it is crossing out rather than the box that happens to hold it.
	 */
	.crossed {
		display: none;
		position: absolute;
		inset-block-start: 50%;
		inset-inline-start: 50%;
		inline-size: 16px;
		block-size: 16px;
		translate: -50% -50%;
	}

	.answer-never .crossed {
		display: block;
	}

	.crossed::before,
	.crossed::after {
		content: '';
		position: absolute;
		/* Half the bar's own thickness above the middle, so the two cross on the centre line. */
		inset-block-start: calc(50% - 1px);
		inset-inline: 0;
		block-size: 2px;
		border-radius: 1px;
		background: var(--sift-ink);
	}

	.crossed::before {
		rotate: 45deg;
	}

	.crossed::after {
		rotate: -45deg;
	}

	/* The badge has to be the containing block for the X, which is absolute. */
	.tile-badge {
		position: relative;
	}

	/* `answer-key` and not `legend`. The Appearance pane already draws one `.legend` (the
	   sharing marks'), so a second element wearing the same generic name would put two of them on one
	   screen, and the check that reads the sharing legend would stop resolving to one thing. Svelte
	   scopes the styles; it does not scope the name in the DOM. */
	.answer-key {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-4);
		margin: 0;
		padding: 0;
		list-style: none;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	.answer-key li {
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}
</style>
