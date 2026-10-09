<script lang="ts">
	/* WHY NOT SHARED: button: the three elements on the timeline below are HANDLES: the two ends of the clip and
	   the playhead you drag to look through it. They carry no words and no glyph, which is the one
	   shape the shared button cannot take: it requires either children or an icon with a name,
	   deliberately, so an icon-only control can never ship without a label. A handle is labelled by
	   where it is on the track. They stay `<button>` because that is what makes them focusable and
	   gives them Enter, Space and the arrow-key nudge. Every OTHER control in this file is the
	   shared button. */

	/*
	 * A video and the cut drawn on it: the real video is the preview, the two marks across the
	 * bottom. A length can be asked for, which keeps the piece draggable whole; dragging an end
	 * goes back to a cut by the marks.
	 */
	import type { Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Button } from '$lib/components/common';
	import { SLOP } from '$lib/components/common/tile-gesture.svelte';
	import { clock } from '$lib/edit/edit.svelte';

	interface Props {
		src: string;
		durationMs: number;
		startMs: number;
		endMs: number;
		/**
		 * `trim` or `clip` follow how the piece was CHOSEN; `gif` is a switch only its control
		 * turns off.
		 */
		kind: 'trim' | 'clip' | 'gif';
		under?: Snippet;
	}

	let {
		src,
		durationMs,
		startMs = $bindable(),
		endMs = $bindable(),
		kind = $bindable(),
		under
	}: Props = $props();

	/** Seconds. */
	const LENGTHS = [5, 10, 15, 30, 60];

	const SHORTEST_MS = 500;
	/**
	 * The narrowest the piece is DRAWN, so a short one can still be caught; what is sent is the
	 * marks.
	 */
	const NARROWEST = 44;
	const NUDGE_MS = 1_000;
	const STRIDE_MS = 10_000;

	let track = $state<HTMLElement | null>(null);
	let video = $state<HTMLVideoElement | null>(null);
	let atMs = $state(0);
	let playing = $state(false);
	/* Measured once: the sheet changes height under the drag. */
	let holding = $state<{
		what: 'start' | 'end' | 'piece' | 'head';
		rect: DOMRect;
		from: number;
		pressedAt: number;
		pressedX: number;
		slid: boolean;
	} | null>(null);
	let asked = $state<number | null>(null);

	const keptMs = $derived(Math.max(0, endMs - startMs));

	function place(at: number): string {
		return durationMs > 0 ? `${(at / durationMs) * 100}%` : '0%';
	}

	const pieceWidth = $derived(
		`max(${NARROWEST}px, ${durationMs > 0 ? (keptMs / durationMs) * 100 : 0}%)`
	);

	function momentAt(event: PointerEvent, rect: DOMRect): number {
		const along = rect.width ? (event.clientX - rect.left) / rect.width : 0;
		return Math.round(Math.min(1, Math.max(0, along)) * durationMs);
	}

	/** The video IS the preview. */
	function show(moment: number): void {
		if (!video || !Number.isFinite(moment)) return;
		atMs = Math.min(Math.max(0, moment), durationMs);
		video.currentTime = atMs / 1000;
	}

	function playClip(): void {
		if (!video) return;
		if (playing) {
			video.pause();
			playing = false;
			return;
		}
		show(startMs);
		playing = true;
		void video.play().catch(() => (playing = false));
	}

	function watchTime(): void {
		if (!video) return;
		atMs = video.currentTime * 1000;
		if (playing && atMs >= endMs) {
			video.pause();
			playing = false;
			show(endMs);
		}
	}

	function take(event: PointerEvent, what: 'start' | 'end' | 'piece' | 'head'): void {
		if (!track || durationMs <= 0) return;
		event.stopPropagation();
		const rect = track.getBoundingClientRect();
		const from = momentAt(event, rect);
		holding = { what, rect, from, pressedAt: from, pressedX: event.clientX, slid: false };
		(event.currentTarget as Element).setPointerCapture(event.pointerId);
		if (what === 'head') {
			show(Math.min(Math.max(startMs, from), endMs));
			return;
		}
		if (what !== 'piece') show(what === 'end' ? endMs : startMs);
	}

	function move(event: PointerEvent): void {
		if (!holding) return;
		const now = momentAt(event, holding.rect);
		if (holding.what === 'head') {
			// It cannot leave the piece.
			show(Math.min(Math.max(startMs, now), endMs));
			return;
		}
		if (holding.what === 'piece') {
			if (!holding.slid && Math.abs(event.clientX - holding.pressedX) <= SLOP) return;
			slide(now - holding.from);
			holding = { ...holding, from: now, slid: true };
			show(startMs);
			return;
		}
		// Dragging an end is a trim, unless a GIF is being made.
		asked = null;
		kind = animating ? 'gif' : 'trim';
		set(holding.what, now);
		show(holding.what === 'end' ? endMs : startMs);
	}

	function release(): void {
		if (holding?.what === 'piece' && !holding.slid) {
			show(Math.min(Math.max(startMs, holding.pressedAt), endMs));
		}
		holding = null;
	}

	function nudge(event: KeyboardEvent, which: 'start' | 'end'): void {
		const by = event.shiftKey ? STRIDE_MS : NUDGE_MS;
		const along = event.key === 'ArrowRight' ? by : event.key === 'ArrowLeft' ? -by : 0;
		if (along === 0) return;
		event.preventDefault();
		asked = null;
		kind = animating ? 'gif' : 'trim';
		set(which, (which === 'start' ? startMs : endMs) + along);
		show(which === 'start' ? startMs : endMs);
	}

	/* The marks cannot cross or meet. */
	function set(which: 'start' | 'end', to: number): void {
		if (which === 'start') {
			startMs = Math.min(Math.max(0, to), Math.max(0, endMs - SHORTEST_MS));
			return;
		}
		endMs = Math.max(Math.min(durationMs, to), Math.min(durationMs, startMs + SHORTEST_MS));
	}

	function slide(byMs: number): void {
		const length = keptMs;
		const start = Math.min(Math.max(0, startMs + byMs), Math.max(0, durationMs - length));
		startMs = start;
		endMs = start + length;
	}

	/** From where the piece already starts; a shorter video takes the whole of itself. */
	function keepFor(seconds: number): void {
		const length = Math.min(seconds * 1000, durationMs);
		const start = Math.min(startMs, Math.max(0, durationMs - length));
		startMs = start;
		endMs = start + length;
		asked = seconds;
		kind = animating ? 'gif' : 'clip';
		show(start);
	}

	/** Read off `kind`, the dialog's, so there is one answer. */
	const animating = $derived(kind === 'gif');

	/** Off goes back to `clip` or `trim`, by how the piece was chosen. */
	function animate(): void {
		kind = animating ? (asked === null ? 'trim' : 'clip') : 'gif';
	}
</script>

<figure class="stage">
	<!-- svelte-ignore a11y_media_has_caption -->
	<video
		bind:this={video}
		{src}
		preload="metadata"
		playsinline
		muted
		controls={false}
		onloadedmetadata={() => show(startMs)}
		ontimeupdate={watchTime}
		onended={() => (playing = false)}
	></video>

	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="timeline"
		bind:this={track}
		onpointermove={move}
		onpointerup={release}
		onpointercancel={() => (holding = null)}
	>
		<!-- Dragging the bright part moves the piece; pressing it looks there. -->
		<div class="gone" style:left="0" style:width={place(startMs)}></div>
		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<div
			class="kept"
			style:left={place(startMs)}
			style:width={pieceWidth}
			onpointerdown={(event) => take(event, 'piece')}
		></div>
		<div
			class="gone"
			style:left={place(endMs)}
			style:width={place(Math.max(0, durationMs - endMs))}
		></div>

		<button
			type="button"
			class="handle"
			data-end="start"
			style:left={place(startMs)}
			aria-label="Where it starts — {clock(startMs)}"
			onpointerdown={(event) => take(event, 'start')}
			onkeydown={(event) => nudge(event, 'start')}
		></button>
		<button
			type="button"
			class="head"
			style:left={place(atMs)}
			aria-label="Look through the clip — {clock(atMs)}"
			onpointerdown={(event) => take(event, 'head')}
		></button>
		<button
			type="button"
			class="handle"
			data-end="end"
			style:left={place(endMs)}
			aria-label="Where it ends — {clock(endMs)}"
			onpointerdown={(event) => take(event, 'end')}
			onkeydown={(event) => nudge(event, 'end')}
		></button>
	</div>
</figure>

{@render under?.()}

<!-- Both rows wrap rather than widen the sheet. -->
<div class="tools">
	<Button size="small" icon={playing ? 'pause' : 'play_arrow'} pressed={playing} onclick={playClip}>
		{playing ? 'Stop' : 'Preview selection'}
	</Button>
	<div class="modes" role="group" aria-label="How long a clip">
		<span class="lengths-label"><Icon name="content_cut" size={18} /> Clip</span>
		{#each LENGTHS as seconds (seconds)}
			<Button size="small" pressed={asked === seconds} onclick={() => keepFor(seconds)}>
				{seconds}s
			</Button>
		{/each}
		<!--
		Create GIF whatever format is written; its length limit is the server's, said in its
		refusal.
		-->
		<Button size="small" icon="gif_box" pressed={animating} onclick={animate}>Create GIF</Button>
	</div>
</div>

<p class="quiet length">
	{clock(keptMs)} long — {clock(startMs)} to {clock(endMs)}.
</p>

<style>
	.stage {
		position: relative;
		margin: 0;
		display: flex;
		flex-direction: column;
		align-items: center;
		background: var(--sift-surface-1);
		border-radius: var(--radius-md);
		overflow: hidden;
	}

	video {
		inline-size: 100%;
		max-block-size: 46vh;
		object-fit: contain;
		background: black;
	}

	.timeline {
		position: absolute;
		inset-block-end: 0;
		inset-inline: 0;
		block-size: 44px;
		touch-action: none;
	}

	.gone {
		position: absolute;
		inset-block: 0;
		background: var(--sift-scrim);
		pointer-events: none;
	}

	.kept {
		position: absolute;
		inset-block: 0;
		border-block-start: 2px solid var(--sift-accent);
		border-block-end: 2px solid var(--sift-accent);
		cursor: grab;
	}

	.head {
		position: absolute;
		inset-block: 0;
		inline-size: 10px;
		translate: -50% 0;
		padding: 0;
		border: 0;
		border-radius: var(--radius-sm);
		background: var(--sift-ink);
		opacity: 0.85;
		cursor: ew-resize;
	}

	.head:focus-visible {
		outline: none;
		box-shadow: var(--focus-ring);
	}

	.handle {
		position: absolute;
		z-index: 1;
		inset-block: 0;
		inline-size: 14px;
		translate: -50% 0;
		padding: 0;
		border: 0;
		border-radius: var(--radius-sm);
		background: var(--sift-accent);
		cursor: ew-resize;
	}

	.handle:focus-visible {
		outline: none;
		box-shadow: var(--focus-ring);
	}

	.tools {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-3);
		margin-block-start: var(--space-3);
	}

	.modes {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-1);
	}

	.lengths-label {
		display: inline-flex;
		align-items: center;
		gap: var(--space-1);
		margin-inline-end: var(--space-1);
		color: var(--sift-ink-2);
		font: var(--text-body-sm);
	}

	.length {
		margin-block-start: var(--space-3);
	}
</style>
