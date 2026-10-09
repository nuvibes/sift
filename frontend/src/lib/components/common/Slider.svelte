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
	with the arrows and announced, with no script; this file dresses the track and the thumb. */

	/* Every slider in Sift. Chromium has no fill pseudo-element, so the fill is a gradient on the
	   track; the pseudo-element spellings are written twice, as a mixed list is dropped by both. */

	interface Props {
		value: number;
		min?: number;
		max?: number;
		step?: number;
		/** What it is called; required, as a slider has nothing in it to read. */
		label: string;
		/** What its value MEANS, where the number alone does not say it: "40%", "Large". */
		valueText?: string;
		id?: string;
		describedBy?: string;
		disabled?: boolean;
		/** Standing on its end, by writing-mode, so keys and drag follow too. */
		vertical?: boolean;
		/** Extra classes for size and position only, added to this file's. */
		class?: string;
		/** A track that is itself the value (`--hue-spectrum`), drawn with no fill. */
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

	/* How far along it is, as a fraction of the span, snapped to the step as the browser draws the
	   thumb, so the fill and the marks agree with it. */
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
	/* The box is the pointer target; the thin track is drawn inside it. */
	.slider {
		/* The track's thickness and the handle's size from tokens, the centring derived from both, so
		   the timeline's marks can find where the handle goes. */
		--track: var(--slider-track);
		--thumb: var(--slider-thumb);
		/* The groove's paint, named so a caller's `ground` reaches all four track rules. */
		--ground: var(--sift-surface-4);
		inline-size: 100%;
		block-size: 16px;
		margin: 0;
		/* The browser's own furniture goes, or the track below is drawn underneath it. */
		appearance: none;
		background: none;
		cursor: pointer;
	}

	/* A finger's height on a phone; the line stays four pixels. */
	@media (max-width: 767px) {
		.slider:not(.vertical) {
			block-size: var(--touch-target);
		}
	}

	/* Vertical: physical sizes, since writing-mode swaps the logical axes. */
	.slider.vertical {
		writing-mode: vertical-lr;
		direction: rtl;
		width: 16px;
		height: 100%;
	}

	.slider:disabled {
		cursor: not-allowed;
	}

	/* The track with the fill as a gradient; transparent past the level shows the ground. */
	.slider::-webkit-slider-runnable-track {
		block-size: var(--track);
		border-radius: var(--radius-full);
		background:
			linear-gradient(to right, var(--sift-accent) var(--at), transparent var(--at)), var(--ground);
		transition: block-size var(--dur-instant) var(--ease);
	}

	/* Vertical fills upward, in the turned box's axes. */
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

	/* A track that is the answer gets no fill, on both engines. */
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

	/* Thicker under the pointer, to say it can be taken hold of. */
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

	/* The handle: white so it shows on the accent line, centred by a derived negative margin. */
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

	/* Centred across the column the same way. */
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

	/* The ring on the handle, not around the mostly empty box. */
	.slider:focus-visible {
		outline: none;
	}

	.slider:focus-visible::-webkit-slider-thumb {
		box-shadow: var(--focus-ring);
	}

	.slider:focus-visible::-moz-range-thumb {
		box-shadow: var(--focus-ring);
	}

	/* Reduced motion: the same sizes, arriving rather than growing. */
	:global(:root[data-motion='reduce']) .slider::-webkit-slider-runnable-track,
	:global(:root[data-motion='reduce']) .slider::-webkit-slider-thumb,
	:global(:root[data-motion='reduce']) .slider::-moz-range-track {
		transition: none;
	}
</style>
