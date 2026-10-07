<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'Slider',
		category: 'control',
		role: 'one value slid along a track',
		basis: 'site:<input type=range>',
		states: ['default', 'spectrum', 'disabled']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	import { alongTrack } from './slider';
	/* WHY NOT BITS-UI: a range input is already a slider. It is draggable with a pointer, steppable
	   with the arrows, correct under a finger, announced with its value and its bounds, and it does
	   all of that with no script at all, which is the same argument this repo makes for building
	   the button on a real <button>. What the library would add here is markup with the behaviour
	   written again in JavaScript; what was missing was never the behaviour, it was one place to
	   dress the track and the thumb. That is this file. */

	/*
	 * Every slider in Sift.
	 *
	 * ## Why this exists
	 *
	 * **On Chromium, a slider only shows how far along it is if its own file draws that.** Firefox
	 * has a pseudo-element for the filled part of the track (`::-moz-range-progress`); Chromium has
	 * none, so the fill has to be painted as a gradient on the track itself. The app's stylesheet
	 * dresses every range input, but it cannot do that half: it does not know the value. So a bare
	 * range input that does not write the gradient itself is a grey groove with a dot on it, in the
	 * browser Sift actually ships in.
	 *
	 * That is not something anybody reports. It reads as "sliders look like that here".
	 *
	 * ## What this owns
	 *
	 * The track, the fill on BOTH engines, the thumb, the growth under the pointer, the vertical
	 * form, disabled, and the focus ring. What it does not own is how long it is: that is a fact
	 * about the space it was put in, so the caller sizes it. See `class`.
	 *
	 * Both pseudo-element spellings are written out twice rather than combined, and that is not
	 * repetition to tidy up: `::-webkit-slider-*` is Chrome, Edge and Safari, `::-moz-range-*` is
	 * Firefox, and a selector list containing both is dropped whole by both.
	 */

	interface Props {
		/** Where it is now. */
		value: number;
		min?: number;
		max?: number;
		step?: number;
		/**
		 * What it is called. Required, because a slider with no label is a control nobody can
		 * identify: there is nothing in it to read.
		 */
		label: string;
		/** What its value MEANS, where the number alone does not say it: "40%", "Large". */
		valueText?: string;
		id?: string;
		describedBy?: string;
		disabled?: boolean;
		/**
		 * Standing on its end.
		 *
		 * `writing-mode` rather than a rotation: the element is genuinely vertical, so the arrow keys,
		 * the drag and the announcement all follow, where a rotated horizontal one keeps behaving
		 * like a horizontal one that happens to be drawn sideways.
		 */
		vertical?: boolean;
		/**
		 * Extra classes from the caller, for SIZE and POSITION only, never for the track or the
		 * thumb. How long a slider is depends on the room it was given; what it looks like does not.
		 *
		 * Written last in the class list, and the element takes no `{...rest}`, so a caller's class
		 * is added to this file's rather than replacing them.
		 */
		class?: string;
		/**
		 * What the track is painted with, where the track ITSELF is the value being chosen.
		 *
		 * Left alone a track is a grey groove with the accent filling in behind the handle, which is
		 * right for every slider that answers "how much": the fill is what says how far along it is.
		 * A HUE slider is not that. Its track is the circle of hues, the handle points at one of
		 * them, and a fill painted over the part already passed would hide the half of the answer
		 * somebody is looking at, so a slider given a ground of its own draws no fill at all.
		 *
		 * Any CSS background, and in practice a token: `--hue-spectrum` is the one that exists.
		 * A caller naming a gradient inline would be naming colours outside the token layer, which
		 * is the one thing `app.css` is for.
		 */
		ground?: string;
		/** Every step of a drag. This is the one a live control follows. */
		oninput: (value: number) => void;
		/** Let go of. For a value that is written down rather than merely followed. */
		onchange?: (value: number) => void;
	}

	let {
		value,
		min = 0,
		max = 100,
		step = 1,
		label,
		valueText,
		id,
		describedBy,
		disabled = false,
		vertical = false,
		class: extra = '',
		ground,
		oninput,
		onchange
	}: Props = $props();

	/*
	 * How far along it is, as a fraction.
	 *
	 * Worked out here rather than by each caller, dividing by the span rather than the maximum so a
	 * scale that does not start at zero is right. A zero span is a slider with one possible value
	 * (a clip whose length is not known yet) and reads as empty: nothing has been played.
	 *
	 * A fraction, not a percentage, because `alongTrack` turns it into a place on the track and
	 * everything else drawn along the same track calls that too. A percentage of the whole track
	 * disagrees with the handle by half a thumb at each end, hidden under the handle for the fill
	 * but visible for marks drawn beside it on the player's timeline.
	 *
	 * The fill is drawn from the value snapped to the step, because that is where the browser draws
	 * the handle: a range input rounds to its `step` (12.3456 -> 12.3 at 0.1) and paints the thumb
	 * there, and a fill from the raw number would sit up to half a step away, flipping sides every
	 * tick. Snapping here means no caller can disagree with the thumb.
	 */
	const snapped = $derived(
		step > 0 ? Math.min(max, min + Math.round((value - min) / step) * step) : value
	);
	const level = $derived(max > min ? (snapped - min) / (max - min) : 0);
</script>

<input
	{id}
	type="range"
	class="slider {extra}"
	class:vertical
	class:spectrum={ground !== undefined}
	style:--ground={ground}
	{min}
	{max}
	{step}
	{value}
	{disabled}
	style:--at={alongTrack(level)}
	aria-label={label}
	aria-valuetext={valueText}
	aria-describedby={describedBy}
	oninput={(event) => oninput(Number(event.currentTarget.value))}
	onchange={(event) => onchange?.(Number(event.currentTarget.value))}
/>

<style>
	/*
	 * The box, which is the POINTER TARGET rather than the line.
	 *
	 * The visible track is four pixels, which is nothing to aim at. The element stays tall enough to
	 * grab and draws the thin part inside itself.
	 */
	.slider {
		/*
		 * How thick the line is, and how big the handle is: named, because three rules depend on
		 * the first of them.
		 *
		 * The handle is centred across the track by a negative margin, there being no alignment
		 * property for a thumb, and that margin is half the difference between the two. A fixed
		 * number would be right for one track size only, and the track grows under the pointer, so
		 * the handle would sit off centre for exactly as long as somebody was looking at it.
		 * Derived, so the two cannot disagree: change the track and the handle follows.
		 *
		 * Read from the app's own tokens rather than written here, because the player's timeline
		 * draws loop brackets and a replay curve along this track and has to place them where this
		 * handle actually goes. See `--slider-at` below and the note beside the tokens.
		 */
		--track: var(--slider-track);
		--thumb: var(--slider-thumb);
		/* What the groove is painted with. A name rather than the token written into the four track
		   rules below, so a caller handing in a ground of its own reaches all four together. See
		   `ground`. */
		--ground: var(--sift-surface-4);
		inline-size: 100%;
		block-size: 16px;
		margin: 0;
		/* The browser's own furniture goes, or the track below is drawn underneath it. */
		appearance: none;
		background: none;
		cursor: pointer;
	}

	/* A finger's height on a phone: the box is the pointer target (above), and a mouse's 16px is
	   not a thumb's. The line stays four pixels, drawn in the middle of the taller box. */
	@media (max-width: 767px) {
		.slider:not(.vertical) {
			block-size: var(--touch-target);
		}
	}

	/*
	 * Standing on its end.
	 *
	 * Physical width and height for every vertical rule below: `writing-mode: vertical-lr` swaps
	 * the axes, so `inline-size` on this element is its height and `block-size` is its width.
	 * Written logically, the volume control would come out as a horizontal bar.
	 */
	.slider.vertical {
		writing-mode: vertical-lr;
		direction: rtl;
		width: 16px;
		height: 100%;
	}

	.slider:disabled {
		cursor: not-allowed;
	}

	/* The track, and the fill painted onto it. The gradient is the only way Chromium has of showing
	   how far along a slider is; `transparent` past the level lets the ground below show through, so
	   there is one colour for the rest of the track rather than two that have to agree. */
	.slider::-webkit-slider-runnable-track {
		block-size: var(--track);
		border-radius: var(--radius-full);
		background:
			linear-gradient(to right, var(--sift-accent) var(--at), transparent var(--at)), var(--ground);
		transition: block-size var(--dur-instant) var(--ease);
	}

	/* Standing on its end, the fill grows upward. `to top` rather than `to right`: the gradient is
	   painted in the box's own axes, and the box is the one that has been turned. */
	.slider.vertical::-webkit-slider-runnable-track {
		width: var(--track);
		height: 100%;
		background:
			linear-gradient(to top, var(--sift-accent) var(--at), transparent var(--at)), var(--ground);
		transition: width var(--dur-instant) var(--ease);
	}

	/* Firefox fills the track itself, so it takes the colour rather than the gradient. */
	.slider::-moz-range-track {
		block-size: var(--track);
		border-radius: var(--radius-full);
		background: var(--ground);
		transition: block-size var(--dur-instant) var(--ease);
	}

	.slider::-moz-range-progress {
		block-size: var(--track);
		border-radius: var(--radius-full);
		background: var(--sift-accent);
	}

	/*
	 * A TRACK THAT IS THE ANSWER, so nothing is painted over it.
	 *
	 * Written after the four rules above rather than folded into them, because it is the exception
	 * and reads as one: the ordinary slider fills in behind its handle, and a slider whose track is
	 * the circle of hues has nothing to fill in: the part already passed is as much of the answer
	 * as the part ahead. Both engines need saying separately: Chromium's fill is a gradient painted
	 * ON the track, Firefox's is an element of its own in front of it.
	 */
	.slider.spectrum::-webkit-slider-runnable-track {
		background: var(--ground);
	}

	.slider.spectrum::-moz-range-progress {
		background: transparent;
	}

	.slider.vertical::-moz-range-track,
	.slider.vertical::-moz-range-progress {
		width: var(--track);
		height: 100%;
	}

	/* Thicker under the pointer, which is the one bit of movement here doing a job: it says the thin
	   line is a thing you can take hold of, and it makes it easier to hit. */
	.slider:hover:not(:disabled),
	.slider:focus-visible {
		--track: 6px;
	}

	.slider:hover:not(:disabled)::-webkit-slider-runnable-track,
	.slider:focus-visible::-webkit-slider-runnable-track {
		block-size: var(--track);
	}

	.slider.vertical:hover:not(:disabled)::-webkit-slider-runnable-track,
	.slider.vertical:focus-visible::-webkit-slider-runnable-track {
		width: var(--track);
	}

	.slider:hover:not(:disabled)::-moz-range-track,
	.slider:focus-visible::-moz-range-track {
		block-size: var(--track);
	}

	/*
	 * The handle.
	 *
	 * White rather than accent: a blue dot on a blue line is a handle nobody can find. `appearance:
	 * none` again on
	 * the thumb, or Chromium keeps its own and ignores the size; the negative margin is what centres
	 * the disc on the track, there being no alignment property for it: half the difference between
	 * the two, DERIVED, so it follows the track thickening under the pointer instead of staying at
	 * the resting figure and leaving the handle a pixel off centre. See the tokens on `.slider`.
	 */
	.slider::-webkit-slider-thumb {
		appearance: none;
		inline-size: var(--thumb);
		block-size: var(--thumb);
		border: 0;
		border-radius: 50%;
		background: var(--sift-ink);
		box-shadow: var(--elev-1);
		margin-block-start: calc((var(--track) - var(--thumb)) / 2);
		transition:
			scale var(--dur-instant) var(--ease),
			margin var(--dur-instant) var(--ease);
	}

	/* Centred ACROSS the column: half the disc, less half the track, exactly as the horizontal one
	   is: it simply uses the other margin, and both are physical for the reason above. */
	.slider.vertical::-webkit-slider-thumb {
		margin-top: 0;
		margin-left: calc((var(--track) - var(--thumb)) / 2);
	}

	.slider:hover:not(:disabled)::-webkit-slider-thumb {
		scale: 1.25;
	}

	.slider::-moz-range-thumb {
		inline-size: var(--thumb);
		block-size: var(--thumb);
		border: 0;
		border-radius: 50%;
		background: var(--sift-ink);
		box-shadow: var(--elev-1);
	}

	/*
	 * The ring goes on the HANDLE, not around the whole control.
	 *
	 * The handle is right in both orientations: the box is a pointer target with a lot of
	 * empty space in it, and a ring around 22px by 104px of mostly nothing reads as a box being
	 * focused rather than as a slider. A ring on the thing that moves reads as the thing that moves.
	 */
	.slider:focus-visible {
		outline: none;
	}

	.slider:focus-visible::-webkit-slider-thumb {
		box-shadow: var(--focus-ring);
	}

	.slider:focus-visible::-moz-range-thumb {
		box-shadow: var(--focus-ring);
	}

	/* The answer without the travel: the track is still thicker under the pointer and the handle is
	   still bigger, they simply arrive rather than growing. */
	:global(:root[data-motion='reduce']) .slider::-webkit-slider-runnable-track,
	:global(:root[data-motion='reduce']) .slider::-webkit-slider-thumb,
	:global(:root[data-motion='reduce']) .slider::-moz-range-track {
		transition: none;
	}
</style>
