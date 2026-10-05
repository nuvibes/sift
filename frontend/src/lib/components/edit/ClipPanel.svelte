<script lang="ts">
	/* WHY NOT SHARED: button: the three elements on the timeline below are HANDLES: the two ends of the clip and
	   the playhead you drag to look through it. They carry no words and no glyph, which is the one
	   shape the shared button cannot take: it requires either children or an icon with a name,
	   deliberately, so an icon-only control can never ship without a label. A handle is labelled by
	   where it is on the track. They stay `<button>` because that is what makes them focusable and
	   gives them Enter, Space and the arrow-key nudge. Every OTHER control in this file is the
	   shared button. */

	/*
	 * A video, and the cut drawn on the video itself.
	 *
	 * The picture is the control, as on a photograph: choosing a moment is looking at it, not
	 * reading a clock. The frames are the real video rather than a thumbnail strip built after
	 * import, so dragging a mark seeks the video under it and a file added a moment ago is as
	 * editable as an old one.
	 *
	 * The two marks and the piece between them sit across the bottom of the picture, where a
	 * player's own progress bar sits and a hand already goes to scrub.
	 *
	 * A length can be asked for rather than drawn. Choosing one sets the piece to exactly that long
	 * and leaves it draggable as a whole ("fifteen seconds of this, from about here"). Dragging
	 * either end by hand goes back to a cut measured by the marks.
	 */
	import type { Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Button } from '$lib/components/common';
	import { SLOP } from '$lib/components/common/tile-gesture.svelte';
	import { clock } from '$lib/edit/edit.svelte';

	interface Props {
		/** Where the video is. */
		src: string;
		/** How long the whole video runs, in milliseconds. */
		durationMs: number;
		/** The two points, in milliseconds. */
		startMs: number;
		endMs: number;
		/**
		 * Which of the two the server is asked for.
		 *
		 * `trim` and `clip` cut the same piece and record it differently, so those two follow how the
		 * piece was CHOSEN rather than being a switch: a length asked for is a clip, a piece dragged
		 * out by its ends is a trim.
		 *
		 * `gif` is the exception and is a switch, because it is not a way of choosing a piece: it
		 * is a different thing to make out of whichever piece was chosen. So it survives dragging
		 * and it survives pressing a length, and only its own control turns it off.
		 */
		kind: 'trim' | 'clip' | 'gif';
		/** Rendered between the picture and the buttons: the name the copy will have. */
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

	/** The lengths worth asking for by name. Seconds. */
	const LENGTHS = [5, 10, 15, 30, 60];

	/** The shortest piece the two marks may leave between them. */
	const SHORTEST_MS = 500;
	/**
	 * The narrowest the bright piece is ever DRAWN, whatever it really holds.
	 *
	 * Five seconds of a long video is a sliver a few pixels wide, and a few pixels is not something
	 * a hand can catch, so the shortest clip would be the one nobody could move. The bar it is
	 * drawn on gives it a floor; what is sent is still the two marks, untouched.
	 */
	const NARROWEST = 44;
	/** How far an arrow key moves a mark, and how far it moves with shift held. */
	const NUDGE_MS = 1_000;
	const STRIDE_MS = 10_000;

	let track = $state<HTMLElement | null>(null);
	let video = $state<HTMLVideoElement | null>(null);
	/** Where the picture is showing from, while somebody is looking through the clip. */
	let atMs = $state(0);
	let playing = $state(false);
	/* What is being dragged, and where the track was when the drag started. Read once: the sheet
	   changes height as answers arrive underneath it, and a rectangle measured mid-drag drifts. */
	let holding = $state<{
		what: 'start' | 'end' | 'piece' | 'head';
		rect: DOMRect;
		from: number;
		/* Where a press on the piece landed: a press that never slides moves the mark there. */
		pressedAt: number;
		pressedX: number;
		slid: boolean;
	} | null>(null);
	/** The length that was asked for, when one was. Cleared the moment a mark is dragged by hand. */
	let asked = $state<number | null>(null);

	const keptMs = $derived(Math.max(0, endMs - startMs));

	function place(at: number): string {
		return durationMs > 0 ? `${(at / durationMs) * 100}%` : '0%';
	}

	/** How wide the bright piece is drawn, never so narrow that it cannot be caught. */
	const pieceWidth = $derived(
		`max(${NARROWEST}px, ${durationMs > 0 ? (keptMs / durationMs) * 100 : 0}%)`
	);

	function momentAt(event: PointerEvent, rect: DOMRect): number {
		const along = rect.width ? (event.clientX - rect.left) / rect.width : 0;
		return Math.round(Math.min(1, Math.max(0, along)) * durationMs);
	}

	/** Show this moment. The video IS the preview, so seeking it is the whole of the feedback. */
	function show(moment: number): void {
		if (!video || !Number.isFinite(moment)) return;
		atMs = Math.min(Math.max(0, moment), durationMs);
		video.currentTime = atMs / 1000;
	}

	/** Play the piece and only the piece: a preview is of what will be saved. */
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
			// Looking through the clip, which is what the bar is for. It cannot leave the piece:
			// dragged past either mark it stops there, because there is nothing else to preview.
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
		// Dragging an end is a cut measured by its ends, whatever length was asked for before,
		// unless a GIF is being made, which dragging does not stop being.
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

	/* The two marks cannot cross, and they cannot meet. Crossed they describe a piece of less than
	   no time, which the server refuses with a sentence about a length: true, and nothing to do
	   with what the person did, which was drag one mark past the other. */
	function set(which: 'start' | 'end', to: number): void {
		if (which === 'start') {
			startMs = Math.min(Math.max(0, to), Math.max(0, endMs - SHORTEST_MS));
			return;
		}
		endMs = Math.max(Math.min(durationMs, to), Math.min(durationMs, startMs + SHORTEST_MS));
	}

	/** The whole piece moved along, keeping its length and staying inside the video. */
	function slide(byMs: number): void {
		const length = keptMs;
		const start = Math.min(Math.max(0, startMs + byMs), Math.max(0, durationMs - length));
		startMs = start;
		endMs = start + length;
	}

	/**
	 * A length asked for by name.
	 *
	 * Kept where the piece already starts, so pressing 15 after pointing at roughly the right
	 * moment gives fifteen seconds from there rather than fifteen seconds from the beginning. A
	 * video shorter than the length asked for takes the whole of itself.
	 */
	function keepFor(seconds: number): void {
		const length = Math.min(seconds * 1000, durationMs);
		const start = Math.min(startMs, Math.max(0, durationMs - length));
		startMs = start;
		endMs = start + length;
		asked = seconds;
		kind = animating ? 'gif' : 'clip';
		show(start);
	}

	/**
	 * Whether the piece is being made as a GIF rather than as a video.
	 *
	 * Read off the kind rather than held beside it, so there is one answer to "is this a GIF"
	 * instead of two that can disagree: the dialog owns `kind` and can set it from elsewhere.
	 */
	const animating = $derived(kind === 'gif');

	/** Make the piece a GIF, or go back to whichever cut the piece was chosen by.
	 *
	 * Off goes back to `clip` when a length was asked for by name and `trim` when the ends were
	 * dragged, which is the same rule those two follow everywhere else here. */
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
		<!-- The part that survives, bright, and the two that do not, dimmed with the same token the
		     crop rectangle uses. Dragging the bright part moves the whole piece; pressing it looks there. -->
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
		<!-- The bar you drag to look through what is inside the clip, the way a phone's editor does.
		     It cannot leave the piece: there is nothing outside it worth previewing. -->
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

<!--
	The controls under the picture. Both rows wrap.

	The dialog is the app's ordinary sheet (420 pixels, narrower on a phone), and this row (a label,
	five lengths and Create GIF) is wider than that. Wrapping rather than widening the sheet: a wider
	sheet would change every other row in the dialog and still not fit on a phone, while wrapping
	holds at every width, and a row of alternatives with a label in front reads the same folded as
	straight.
-->
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
		<!-- Making a GIF out of the piece, rather than a way of choosing the piece. Beside
		     the lengths because it is about the same piece, and pressed-state rather than a third
		     length because it does not change what is marked.

		     It says "Create GIF" whatever is written (GIF, WebP or AVIF, which differ by 35x in size
		     and by nothing else that matters), because the moving-picture kind is called a GIF on every screen;
		     the extension beside the name box comes from the server's own answer and says which file
		     is about to be written. One word for the kind outweighs being exact about the format.

		     How long one may be is the server's number too, and per format, and is not repeated
		     here. Asking for a longer one comes back from the preflight as a sentence naming the
		     limit, in the same place every other refusal about this cut appears: a copy of the
		     number on this side would be a second answer waiting to disagree with the write. -->
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

	/* Across the bottom of the picture, where a player's own bar sits. */
	.timeline {
		position: absolute;
		inset-block-end: 0;
		inset-inline: 0;
		block-size: 44px;
		touch-action: none;
	}

	/* Only a dimming: a press on the piece drawn wider than it is goes through to the piece. */
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

	/* Narrower than a mark and a different colour, because it changes nothing: it only looks. */
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

	/* Above the mark, which opens on the start handle and would otherwise cover it. */
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

	/* Wraps, so Create GIF lands on a second line rather than being cut off by the edge of the
	   sheet. See the note in the markup for why this and not a wider sheet. */
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

	/* Only the GAP is this panel's: the ink and the face are the `.quiet` utility's. The length
	   line is held off the stage above it. */
	.length {
		margin-block-start: var(--space-3);
	}
</style>
