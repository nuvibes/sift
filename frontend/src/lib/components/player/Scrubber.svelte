<script lang="ts">
	/* WHY NOT SHARED: button: the two elements below are loop-point HANDLES on a timeline: a thumb you drag along
	   a track, positioned by a custom property and moved by the arrow keys. They have no label and
	   no glyph, which is the shape the shared button cannot take: it requires either words or an
	   icon with a name, precisely so an icon-only control can never ship unlabelled. A handle is
	   labelled by where it IS. It stays a `<button>` because that is what makes it focusable and
	   gives it Enter and Space for free. */

	/*
	 * The timeline: how far through a file is, the frame the pointer is over, and a loop's two marks.
	 *
	 * ONE of these, used by the player and by every cell of a wall. Two (the player's and the
	 * wall's, drawn separately, styled separately) would drift: different track heights, a handle
	 * on one and not the other, marks of different shapes. Two controls that do the same job in the
	 * same app is the fault this file exists to prevent, and it is worse here than
	 * most because a scrubber is judged entirely on feel.
	 *
	 * Everything it cannot do itself is asked for. It never seeks (only the media element can move
	 * a playhead) and it never decides where a loop's marks belong. It reports, and whoever owns
	 * the file acts.
	 */
	import ScrubPreview from './ScrubPreview.svelte';
	import ReplayCurve from './ReplayCurve.svelte';
	import { Slider } from '$lib/components/common';
	import { alongTrack, fractionAt, thumbWidth } from '$lib/components/common/slider';
	import { SKIP_SECONDS } from '$lib/player/skip';
	import { clock } from '$lib/shell/duration';
	import type { SpriteSheet } from '$lib/player/trickplay';

	interface Props {
		/** Where the playhead is, in seconds. */
		position: number;
		/** How long the file is, in seconds. Zero until the element has said. */
		duration: number;
		/** The scrub strip, when one has been built for this file. */
		sheet?: SpriteSheet | null;
		/** Where that strip is fetched from. Ignored without a sheet. */
		sheetUrl?: string | null;
		/** The loop's ends, in seconds, or null where a mark has not been set. */
		pointA?: number | null;
		pointB?: number | null;
		/** Move the playhead. The element is the only thing that can seek, so it is asked to. */
		onseek: (seconds: number) => void;
		/** A loop mark was dragged or nudged to here. Absent where the marks cannot be moved. */
		onmark?: (which: 'a' | 'b', seconds: number) => void;
		/** What the control is called, where more than one is on screen at a time. */
		label?: string;
		/**
		 * Nothing to move along, so the whole timeline is refused and dimmed.
		 *
		 * A picture has no playhead: a GIF included, because it is drawn in an `<img>` and
		 * an `<img>` can neither say where it is nor be sent anywhere. The bar keeps the timeline
		 * rather than dropping it: the row stays exactly the same shape whatever is playing, which
		 * matters most on a wall where the file underneath changes every few seconds.
		 */
		disabled?: boolean;
		/**
		 * The replay curve for this file, or null where there is nothing worth drawing.
		 *
		 * Handed in rather than fetched, for the reason the scrub strip beside it is: this component
		 * is drawn by the player and by every cell of a wall, and a component that fetched would be
		 * one request per cell for a picture a wall has no room to show. Null is the ordinary answer:
		 * a file watched straight through once has a flat curve, which is true and says nothing.
		 */
		replays?: readonly number[] | null;
	}

	let {
		position,
		duration,
		sheet = null,
		sheetUrl = null,
		pointA = null,
		pointB = null,
		onseek,
		onmark,
		label = 'Position',
		disabled = false,
		replays = null
	}: Props = $props();

	let track = $state<HTMLElement | null>(null);
	/** Where the pointer is on the track, in seconds, or null when it is not on it. */
	let previewAt = $state<number | null>(null);
	let previewX = $state(0);
	/** Which mark is being dragged, or null. */
	let dragging = $state<'a' | 'b' | null>(null);
	/**
	 * Whether the timeline is being attended to: the pointer on it, or the keyboard in it.
	 *
	 * What the replay curve is shown by. Drawn the whole time a file is open, over a video it would
	 * be a grey shape sitting permanently across the bottom of the picture: information
	 * nobody asked for, in front of the thing they did. It answers a question ("where is the part
	 * I keep coming back to") and the moment somebody asks it is the moment they reach for the
	 * bar.
	 *
	 * The keyboard counts, and it is the half that is easy to leave out. Somebody scrubbing with the
	 * arrow keys is doing exactly what somebody dragging is doing, and a curve that only ever
	 * appears under a pointer is a curve they can never see.
	 */
	let attended = $state(false);

	const strip = $derived(sheet && sheetUrl ? sheet : null);

	/* How wide the frame above the timeline is drawn. Known here as well as in the component that
	   draws it, because the frame has to be kept inside the picture and only this end knows how much
	   room there is. */
	const PREVIEW_WIDTH = 160;

	/** How far along the track a moment sits, as a fraction the shared expression can place by.
	 *
	 * A FRACTION rather than a percentage of the track, and that matters.
	 * The playhead is a native range handle: its centre travels from half a thumb in to half a thumb
	 * from the end, never the whole width. A bracket placed at a plain percentage therefore agrees
	 * with the playhead at the midpoint and nowhere else, drifting to half a thumb apart at each end,
	 * and the bracket is drawn beside the handle rather than under it, so it shows.
	 *
	 * `--slider-at` in `app.css` is where that correction is written, once, for everything on this
	 * track. See `Slider`.
	 */
	function alongBy(seconds: number): number {
		if (duration <= 0) return 0;
		return seconds >= duration ? 1 : seconds / span;
	}

	/**
	 * How fine the track's own steps are. The slider is told this, so it is what the HANDLE lands on.
	 *
	 * A tenth of a second, which is the resolution somebody dragging the bar wants. See `stepBy`: the
	 * arrow keys do not use it, because five seconds is what an arrow means everywhere else in Sift.
	 */
	const STEP = 0.1;

	/* The track is the whole tenths that hold the file, so its end is a step the handle can stand on
	   (a range input never rests between steps). Rounded first so 3.0 is not pushed past itself. */
	const span = $derived(duration > 0 ? Math.ceil(Math.round(duration * 1e6) / 1e5) / 10 : 0);

	/**
	 * Where the playhead is, on the track's own steps: one number, driving the fill and the handle.
	 *
	 * A range input sanitises whatever it is handed to its own `step` (12.3456 becomes 12.3, 12.35
	 * becomes 12.4) and draws the handle at the sanitised number, so a fill painted from the raw
	 * playhead would sit up to half a step away in a direction that flips as the position ticks.
	 * Half a tenth of a second is a third of a pixel on a long clip and about ten pixels on a
	 * six-second one, so short files show it worst. (A `transition` on the thumb or the fill plays
	 * no part: neither animates along the track.)
	 *
	 * So the snap happens here, once, and the slider is handed a number it will not have to change.
	 * The rule is the input's own: round to the nearest step, never past the track's end; the end of
	 * the file is the end of the track.
	 *
	 * A wider fix would belong in `Slider`, which owns both the fill and the handle and could snap
	 * for every caller; this file is the one with a continuous number to hand it.
	 */
	const onTheTrack = $derived.by(() => {
		if (duration <= 0) return 0;
		if (position >= duration) return span;
		const stepped = Math.round(Math.max(position, 0) / STEP) * STEP;
		return Math.min(stepped, span);
	});

	/*
	 * Where on the track the pointer is, in seconds.
	 *
	 * Measured against the box the track, the preview and the marks are all placed in, so none of
	 * them can disagree about where a moment sits. Kept in pixels from the start rather than as a
	 * fraction, because the frame is placed against the same track and converting twice lets the
	 * two disagree by a few pixels at the ends.
	 */
	function previewFrom(event: PointerEvent) {
		if (!strip || duration <= 0) return;
		if (!track) return;
		const box = track.getBoundingClientRect();
		if (box.width <= 0) return;
		const across = Math.min(Math.max(event.clientX - box.left, 0), box.width);
		// Read against the HANDLE's travel, not the box, or the frame shown under the pointer is a
		// different moment from the one a press at the same place seeks to, by up to half a thumb
		// at each end, which on a long file is seconds.
		previewAt = Math.min(duration, fractionAt(across, box.width, thumbWidth(track)) * span);
		previewX = Math.min(Math.max(across, PREVIEW_WIDTH / 2), box.width - PREVIEW_WIDTH / 2);
	}

	function endPreview() {
		previewAt = null;
		/* The pointer has left, but a loop marker being dragged has CAPTURED it, so it is still on
		   the timeline in every sense that matters, and taking the curve away mid-drag would be the
		   screen changing under somebody's hand. The drop puts this back to following the pointer. */
		if (dragging === null) attended = false;
	}

	/** Where the pointer is, in seconds, for a mark being dragged. */
	function secondsAt(event: PointerEvent): number | null {
		const box = track?.getBoundingClientRect();
		if (!box || box.width <= 0 || duration <= 0) return null;
		const across = Math.min(Math.max(event.clientX - box.left, 0), box.width);
		return Math.min(duration, (across / box.width) * span);
	}

	function grab(which: 'a' | 'b', event: PointerEvent) {
		if (!onmark) return;
		event.preventDefault();
		dragging = which;
		(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
	}

	function dragMark(event: PointerEvent) {
		if (dragging === null || !onmark) return;
		const seconds = secondsAt(event);
		if (seconds === null) return;
		onmark(dragging, seconds);
	}

	function dropMark(event: PointerEvent) {
		if (dragging === null) return;
		(event.currentTarget as HTMLElement).releasePointerCapture?.(event.pointerId);
		dragging = null;
	}

	/*
	 * The arrows move the playhead by the app's own step.
	 *
	 * A range input answers the arrow keys itself, by its own `step`, and this one's step is a
	 * tenth of a second, the resolution somebody dragging wants; so arrows after clicking the bar
	 * would move a tenth while the same arrow elsewhere moves five. The app's shortcut cannot
	 * rescue it, deliberately: a shortcut does not fire while something is being typed into, every
	 * `<input>` counts, and an exception would make one press move the playhead twice.
	 *
	 * So the widget answers its own keyboard, with the number the shortcut uses: `SKIP_SECONDS`,
	 * one number.
	 *
	 * Up and Down as well as Left and Right, because a range input treats Up as Right and somebody
	 * who learned that on the volume slider will try it here.
	 */
	function stepBy(event: KeyboardEvent) {
		if (disabled || duration <= 0) return;
		const by =
			event.key === 'ArrowRight' || event.key === 'ArrowUp'
				? SKIP_SECONDS
				: event.key === 'ArrowLeft' || event.key === 'ArrowDown'
					? -SKIP_SECONDS
					: 0;
		if (by === 0) return;
		/* Refused, or the input moves by its own step as well and the playhead ends up five seconds
		   and a tenth away. Home, End and the two Page keys are left alone: those are the widget's
		   own and they are already right. */
		event.preventDefault();
		onseek(Math.min(duration, Math.max(0, position + by)));
	}

	/** A second at a time from the keyboard, for anybody without a pointer to drag with. */
	function nudge(which: 'a' | 'b', event: KeyboardEvent) {
		if (!onmark) return;
		const from = which === 'a' ? pointA : pointB;
		if (from === null) return;
		if (event.key === 'ArrowLeft') {
			event.preventDefault();
			onmark(which, Math.max(0, from - 1));
		} else if (event.key === 'ArrowRight') {
			event.preventDefault();
			onmark(which, Math.min(duration, from + 1));
		}
	}
</script>

<!-- The track, the frame that hovers over it, and the loop's two markers. One box, so all three are
     placed against the same rectangle the pointer is read against. -->
<!-- svelte-ignore a11y_no_static_element_interactions: the pointer handlers here only WATCH
     where the pointer is, to draw the frame above; everything that can be operated is the slider
     inside, which carries the whole keyboard on its own. -->
<div
	class="timeline"
	class:off={disabled}
	bind:this={track}
	onpointerenter={() => (attended = true)}
	onpointermove={previewFrom}
	onpointerdown={previewFrom}
	onpointerleave={endPreview}
	onpointercancel={endPreview}
	onfocusin={() => (attended = true)}
	onfocusout={() => (attended = false)}
	onkeydown={stepBy}
>
	<!-- The pointer is watched on the box rather than on the slider, because the box is what the
	     frame above, the two markers and the pointer are all measured against. It also keeps the
	     frame showing while a loop marker is dragged, which is when somebody most wants to see what
	     they are marking.

	     Before the slider in the markup as well as behind it in the paint, so nothing operable is
	     ever underneath it in the tab order or the hit test.

	     Mounted whenever there is a curve and faded rather than added and removed, so it has
	     something to animate from in both directions and the hundred-point path is built once per
	     file. -->
	{#if replays && replays.length > 0}
		<ReplayCurve heat={replays} showing={attended} />
	{/if}

	<Slider
		{label}
		{disabled}
		min={0}
		max={span}
		step={STEP}
		value={onTheTrack}
		valueText={clock(position)}
		oninput={(seconds) => onseek(Math.min(seconds, duration))}
	/>

	{#if strip && sheetUrl && previewAt !== null}
		<ScrubPreview
			sheet={strip}
			url={sheetUrl}
			seconds={previewAt}
			{duration}
			at={previewX}
			label={clock(previewAt)}
		/>
	{/if}

	<!-- The loop's two ends, on the timeline where they were set. Draggable, and moveable a second
	     at a time from the keyboard for anybody without a pointer. -->
	{#if pointA !== null}
		<button
			type="button"
			class="marker start"
			style:--at={alongTrack(alongBy(pointA))}
			aria-label="Loop start, at {clock(pointA)}"
			onpointerdown={(event) => grab('a', event)}
			onpointermove={dragMark}
			onpointerup={dropMark}
			onpointercancel={dropMark}
			onkeydown={(event) => nudge('a', event)}
		></button>
	{/if}
	{#if pointB !== null}
		<button
			type="button"
			class="marker end"
			style:--at={alongTrack(alongBy(pointB))}
			aria-label="Loop end, at {clock(pointB)}"
			onpointerdown={(event) => grab('b', event)}
			onpointermove={dragMark}
			onpointerup={dropMark}
			onpointercancel={dropMark}
			onkeydown={(event) => nudge('b', event)}
		></button>
	{/if}
</div>

<style>
	/* The track and the frame above it share a box, so the preview is positioned against exactly the
	   rectangle the pointer was measured against. */
	.timeline {
		position: relative;
		display: flex;
		align-items: center;
		min-block-size: 14px;
	}

	/*
	 * Nothing to move along, and saying so.
	 *
	 * The same 0.5 the shared button takes when it is disabled and the tile-size slider on the top bar
	 * takes when there is nothing to size: one meaning, one amount, wherever it appears. The input
	 * inside is separately refused; this is only the look, and dimming alone would leave a track
	 * somebody could still drag.
	 */
	.timeline.off {
		opacity: 0.5;
	}

	/*
	 * The track, the fill, the thumb and the growth under the pointer are `Slider`'s, where every
	 * engine-specific rule in the app lives, and it divides by the span, right for a scale not
	 * starting at zero. What is left here is the box the three things share.
	 */

	/*
	 * A loop's ends, drawn as brackets.
	 *
	 * As a bar rather than a dot: it sits on a 4px line, and a dot the same size as the playhead's
	 * would be mistaken for it.
	 */
	.marker {
		position: absolute;
		inset-block-start: 50%;
		/* Where the PLAYHEAD would be at the same moment. See `alongTrack`. A plain
		   percentage of the track would agree with the handle at the midpoint and nowhere else. */
		inset-inline-start: var(--at, 0%);
		translate: -50% -50%;
		z-index: 3;
		inline-size: 8px;
		block-size: 18px;
		padding: 0;
		border: 0;
		border-radius: var(--radius-sm);
		background: var(--sift-accent-text);
		cursor: ew-resize;
		touch-action: none;
	}

	/* The start reads as an opening bracket and the end as a closing one, so a glance says which is
	   which without a label being read out. */
	.marker.start {
		border-start-end-radius: 0;
		border-end-end-radius: 0;
	}

	.marker.end {
		border-start-start-radius: 0;
		border-end-start-radius: 0;
	}

	.marker:focus-visible {
		outline: none;
		box-shadow: var(--focus-ring);
	}
</style>
