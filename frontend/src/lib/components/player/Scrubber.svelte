<script lang="ts">
	/* WHY NOT SHARED: button: the two elements below are loop-point HANDLES on a timeline: a thumb you drag along
	   a track, positioned by a custom property and moved by the arrow keys. They have no label and
	   no glyph, which is the shape the shared button cannot take: it requires either words or an
	   icon with a name, precisely so an icon-only control can never ship unlabelled. A handle is
	   labelled by where it IS. It stays a `<button>` because that is what makes it focusable and
	   gives it Enter and Space for free. */

	/*
	 * The timeline, ONE for the player and every cell of a wall. It never seeks and never places a
	 * loop mark itself: it reports, and whoever owns the file acts.
	 */
	import ScrubPreview from './ScrubPreview.svelte';
	import ReplayCurve from './ReplayCurve.svelte';
	import { Slider } from '$lib/components/common';
	import { alongTrack, fractionAt, thumbWidth } from '$lib/components/common/slider';
	import { SKIP_SECONDS } from '$lib/player/skip';
	import { clock } from '$lib/shell/duration';
	import type { SpriteSheet } from '$lib/player/trickplay';

	interface Props {
		position: number;
		duration: number;
		sheet?: SpriteSheet | null;
		sheetUrl?: string | null;
		pointA?: number | null;
		pointB?: number | null;
		onseek: (seconds: number) => void;
		/** Absent where the marks cannot be moved. */
		onmark?: (which: 'a' | 'b', seconds: number) => void;
		label?: string;
		/** A picture has no playhead: the timeline is kept, dimmed, so the row keeps its shape. */
		disabled?: boolean;
		/** Handed in, not fetched: one request per cell would be wasted on a wall. */
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
	let previewAt = $state<number | null>(null);
	let previewX = $state(0);
	let dragging = $state<'a' | 'b' | null>(null);
	/** The pointer on it, or the keyboard in it: what shows the replay curve. */
	let attended = $state(false);

	const strip = $derived(sheet && sheetUrl ? sheet : null);

	/* Known here too: only this end knows how much room there is. */
	const PREVIEW_WIDTH = 160;

	/**
	 * A FRACTION, placed by `--slider-at` in `app.css`, so a bracket sits under the handle at every
	 * point.
	 */
	function alongBy(seconds: number): number {
		if (duration <= 0) return 0;
		return seconds >= duration ? 1 : seconds / span;
	}

	/** A tenth of a second for a drag; the arrows use `stepBy`. */
	const STEP = 0.1;

	/* Whole tenths, so the end is a step the handle can stand on. */
	const span = $derived(duration > 0 ? Math.ceil(Math.round(duration * 1e6) / 1e5) / 10 : 0);

	/**
	 * The playhead snapped to the track's steps, since a range input draws its handle at the
	 * sanitised value and the fill would sit half a step away.
	 */
	const onTheTrack = $derived.by(() => {
		if (duration <= 0) return 0;
		if (position >= duration) return span;
		const stepped = Math.round(Math.max(position, 0) / STEP) * STEP;
		return Math.min(stepped, span);
	});

	/* Measured against the box the track, preview and marks share. */
	function previewFrom(event: PointerEvent) {
		if (!strip || duration <= 0) return;
		if (!track) return;
		const box = track.getBoundingClientRect();
		if (box.width <= 0) return;
		const across = Math.min(Math.max(event.clientX - box.left, 0), box.width);
		// Against the HANDLE's travel, so the frame shown is the moment a press would seek to.
		previewAt = Math.min(duration, fractionAt(across, box.width, thumbWidth(track)) * span);
		previewX = Math.min(Math.max(across, PREVIEW_WIDTH / 2), box.width - PREVIEW_WIDTH / 2);
	}

	function endPreview() {
		previewAt = null;
		/* A dragged marker has captured the pointer: the curve stays. */
		if (dragging === null) attended = false;
	}

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

	/**
	 * The arrows move by `SKIP_SECONDS`, as the app's shortcut does, which cannot fire inside an
	 * input. Up and Down too, as on the volume slider.
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
		/* Or the input moves its own tenth as well. */
		event.preventDefault();
		onseek(Math.min(duration, Math.max(0, position + by)));
	}

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

<!-- One box for the track, the frame above it and the markers. -->
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
	<!--
	Watched on the box; before the slider so nothing operable is underneath; faded, built once per
	file.
	-->
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
	.timeline {
		position: relative;
		display: flex;
		align-items: center;
		min-block-size: 14px;
	}

	.timeline.off {
		opacity: var(--disabled-opacity);
		--slider-thumb: 0px;
	}

	/* The track itself is `Slider`'s; this is the box the three share. */

	/* Brackets, not dots, which would be mistaken for the playhead. */
	.marker {
		position: absolute;
		inset-block-start: 50%;
		/* Where the PLAYHEAD would be (`alongTrack`). */
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
