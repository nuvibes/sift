<script lang="ts">
	/*
	 * THE SCRUB LINE: the time so far at the line's start, the timeline, the length at its end.
	 *
	 * One row, at the top of every player bar (the popout player's, Theater's, the Audio player's),
	 * each time centred on the line's height. Rather than one reading beside the buttons
	 * ("1:23 / 4:36"), the clock is split across the line's two ends, where it says where the
	 * playhead is against the whole without a second look, and the buttons' row is left to presses.
	 *
	 * The two times are hidden from a screen reader: the timeline is a slider that already says
	 * where it is and how long the file is (`Scrubber`), so reading them out would be the same fact
	 * twice.
	 */
	import { lengthClock, playheadClock } from '$lib/shell/duration';
	import type { SpriteSheet } from '$lib/player/trickplay';
	import Scrubber from './Scrubber.svelte';

	interface Props {
		position: number;
		duration: number;
		sheet?: SpriteSheet | null;
		sheetUrl?: string | null;
		replays?: readonly number[] | null;
		pointA?: number | null;
		pointB?: number | null;
		onseek: (seconds: number) => void;
		onmark?: (which: 'a' | 'b', seconds: number) => void;
		label?: string;
		disabled?: boolean;
		/** Whether there is a clock worth reading: a picture has no playhead and no length. */
		timed?: boolean;
		/**
		 * Keep the clocks' room with no clock in it, so a bar that drives several things in turn
		 * (Theater's, over a wall of clips and pictures) does not change width under the hand.
		 */
		keepRoom?: boolean;
	}

	let {
		position,
		duration,
		sheet = null,
		sheetUrl = null,
		replays = null,
		pointA = null,
		pointB = null,
		onseek,
		onmark,
		label = 'Position',
		disabled = false,
		timed = true,
		keepRoom = false
	}: Props = $props();

	const clocks = $derived(timed || keepRoom);
</script>

<div class="scrub-line" class:clocked={clocks}>
	{#if clocks}
		<span class="time start" class:held-room={keepRoom} aria-hidden="true"
			>{#if timed}<span class="reading">{playheadClock(position, duration)}</span>{/if}</span
		>
	{/if}
	<Scrubber
		{position}
		{duration}
		{sheet}
		{sheetUrl}
		{replays}
		{pointA}
		{pointB}
		{onseek}
		{onmark}
		{disabled}
		{label}
	/>
	{#if clocks}
		<span class="time end" class:held-room={keepRoom} aria-hidden="true"
			>{#if timed}<span class="reading">{lengthClock(duration)}</span>{/if}</span
		>
	{/if}
</div>

<style>
	/* The timeline takes what the two times leave; each time is centred on the line's height. */
	.scrub-line {
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		align-items: center;
		column-gap: var(--space-3);
		min-inline-size: 0;
	}

	.scrub-line.clocked {
		grid-template-columns: auto minmax(0, 1fr) auto;
	}

	.time {
		font: var(--text-data);
		color: var(--sift-ink-2);
		font-variant-numeric: tabular-nums;
		white-space: nowrap;
	}

	/* The time so far reads from the line's start and the length from its end, so each meets the
	   bar's edge. */
	.time.start {
		text-align: start;
	}

	.time.end {
		text-align: end;
	}

	/* Room for any clip under a hundred minutes, held by an unseen widest reading in the same cell
	   as the real one, so a longer clip or none at all leaves the bar the width it was. */
	.time.held-room {
		display: inline-grid;
	}

	.time.held-room::before {
		content: '00:00';
		visibility: hidden;
		grid-area: 1 / 1;
	}

	.time.held-room > .reading {
		grid-area: 1 / 1;
	}

	.time.end.held-room > .reading {
		justify-self: end;
	}
</style>
