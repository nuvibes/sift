<script lang="ts" module>
	/** What each handle is called out loud. The two-letter names are for the stylesheet. */
	const LONG: Record<string, string> = {
		nw: 'top left corner',
		n: 'top edge',
		ne: 'top right corner',
		e: 'right edge',
		se: 'bottom right corner',
		s: 'bottom edge',
		sw: 'bottom left corner',
		w: 'left edge'
	};
</script>

<script lang="ts">
	/* WHY NOT SHARED: button: the nine elements on the picture below are GRIPS: the corners and edges of the crop
	   rectangle and its middle, positioned by percentage and dragged. They carry no words and no glyph, which is
	   the one shape the shared button cannot take, and for good reason: it demands a label from an
	   icon-only control. A grip is labelled by where it is. They stay `<button>` for the focus and
	   the arrow-key nudge. */

	/*
	 * A rectangle over a picture, and the picture itself as the control.
	 *
	 * The one crop control in the app, drawn by the picture editor (`PicturePanel`, with its shapes
	 * and turns around it) and by a cover's framing step (`entity/CoverFramer`, locked to the box's
	 * shape).
	 *
	 * Everything outside the rectangle is dimmed. Its eight edges and corners are grips, and its
	 * middle is a ninth that moves the whole thing. A shape, when given, holds the rectangle to it
	 * on every drag and key; the arithmetic is `lib/edit/geometry.ts`, shared by both screens.
	 *
	 * Every grip is a real button and answers the arrow keys, because a rectangle that can only be
	 * dragged is one some people cannot draw at all. One press moves an edge a hundredth of the
	 * picture, a twentieth with Shift: a share rather than a pixel count, since two pixels of a
	 * four-thousand-pixel photograph is a fifth of a pixel on screen.
	 *
	 * Where the element is, is read once when a drag starts and held: the sheet grows or shrinks as
	 * the answer lines under the picture change, so reading it afresh on every movement drifts and
	 * the crop ends somewhere other than where the pointer was let go.
	 *
	 * Nothing here decides what the rectangle is for. It is in the units of `frame` (the editor's
	 * are the photograph's pixels, the cover's are ten-thousandths of its picture), and whoever
	 * holds it turns it into what they save.
	 */
	import type { Snippet } from 'svelte';
	import {
		GRIPS,
		dragged,
		shaped,
		gripAt,
		gripShift,
		moved,
		type Box,
		type Frame,
		type Grip
	} from '$lib/edit/geometry';

	interface Props {
		/** The picture's size as it is seen, in whatever units `box` is in. */
		frame: Frame;
		/** The rectangle to keep, in `frame`'s units. */
		box: Box;
		/** Width over height the rectangle is held to, or null for any shape. */
		ratio?: number | null;
		/** What the rectangle is called out loud: "Move the top left corner of the rectangle". */
		called?: string;
		/** The picture, filling the stage. Handed the size it is drawn at, for a picture that is
		 *  positioned in pixels (a tile of a clip's strip). */
		picture: Snippet<[{ width: number; height: number }]>;
	}

	let { frame, box = $bindable(), ratio = null, called = 'rectangle', picture }: Props = $props();

	/** One arrow press, as a share of the picture; with Shift, the larger one. */
	const NUDGE = 0.01;
	const STRIDE = 0.05;

	/** The grips, and the middle that moves the whole rectangle. */
	const HANDLES: Grip[] = [...GRIPS, 'move'];

	let surface = $state<HTMLElement | null>(null);
	let drawnWidth = $state(0);
	let drawnHeight = $state(0);
	/* What was grabbed, where the pointer was when it was grabbed, and where the rectangle was.
	   Held for the whole drag, so a sheet that changes height under the pointer cannot move it. */
	let holding = $state<{
		grip: Grip;
		from: { x: number; y: number };
		box: Box;
		rect: DOMRect;
	} | null>(null);

	function pointIn(event: PointerEvent, rect: DOMRect): { x: number; y: number } {
		const across = rect.width ? (event.clientX - rect.left) / rect.width : 0;
		const down = rect.height ? (event.clientY - rect.top) / rect.height : 0;
		return { x: across * frame.width, y: down * frame.height };
	}

	function take(event: PointerEvent, grip: Grip): void {
		if (!surface || frame.width === 0 || frame.height === 0) return;
		event.stopPropagation();
		const rect = surface.getBoundingClientRect();
		holding = { grip, from: pointIn(event, rect), box, rect };
		(event.currentTarget as Element).setPointerCapture(event.pointerId);
	}

	function move(event: PointerEvent): void {
		if (!holding) return;
		const now = pointIn(event, holding.rect);
		if (holding.grip === 'move') {
			box = moved(holding.box, { x: now.x - holding.from.x, y: now.y - holding.from.y }, frame);
			return;
		}
		box = shaped(dragged(holding.box, holding.grip, now, frame), holding.grip, frame, ratio);
	}

	function release(): void {
		holding = null;
	}

	/** An edge nudged by the keyboard, in the same arithmetic a drag uses. The middle moves the lot. */
	function nudge(event: KeyboardEvent, grip: Grip): void {
		const share = event.shiftKey ? STRIDE : NUDGE;
		const sign = (more: string, less: string) =>
			event.key === more ? 1 : event.key === less ? -1 : 0;
		const across = sign('ArrowRight', 'ArrowLeft') * share * frame.width;
		const down = sign('ArrowDown', 'ArrowUp') * share * frame.height;
		if (across === 0 && down === 0) return;
		event.preventDefault();
		if (grip === 'move') {
			box = moved(box, { x: across, y: down }, frame);
			return;
		}
		const at = gripAt(grip);
		box = shaped(
			dragged(
				box,
				grip,
				{ x: box.left + at.x * box.width + across, y: box.top + at.y * box.height + down },
				frame
			),
			grip,
			frame,
			ratio
		);
	}

	function percent(value: number, of: number): string {
		return of > 0 ? `${(value / of) * 100}%` : '0%';
	}

	function spoken(grip: Grip): string {
		return grip === 'move' ? `Move the ${called}` : `Move the ${LONG[grip]} of the ${called}`;
	}
</script>

<!-- The stage carries the picture's own shape, and the picture fills it exactly. That is
     load-bearing rather than tidy: every pointer position is read as a fraction of this element, so
     any letterboxing inside it is a rectangle drawn in one place and cut in another. Left to
     `contain` inside a fixed height, a drag across the middle of a photograph would come out close
     enough to look right, and wrong in both directions. -->
<figure
	class="stage"
	style:aspect-ratio={`${frame.width} / ${frame.height}`}
	style:inline-size={`min(100%, calc(18rem * ${frame.height ? frame.width / frame.height : 1}))`}
	bind:this={surface}
	bind:clientWidth={drawnWidth}
	bind:clientHeight={drawnHeight}
	onpointermove={move}
	onpointerup={release}
	onpointercancel={release}
>
	{@render picture({ width: drawnWidth, height: drawnHeight })}

	<!-- Drawn as a hole rather than as a box: everything outside the rectangle is dimmed by a
	     very large shadow spreading outwards, which is what makes the kept part read as the kept
	     part. The dimming is a token, so it follows the theme. -->
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="marquee"
		style:left={percent(box.left, frame.width)}
		style:top={percent(box.top, frame.height)}
		style:width={percent(box.width, frame.width)}
		style:height={percent(box.height, frame.height)}
		onpointerdown={(event) => take(event, 'move')}
	>
		{#each HANDLES as grip (grip)}
			<button
				type="button"
				class="grip"
				data-grip={grip}
				style:left={`${gripAt(grip).x * 100}%`}
				style:top={`${gripAt(grip).y * 100}%`}
				style:--shift-x={gripShift(grip).x}
				style:--shift-y={gripShift(grip).y}
				aria-label={spoken(grip)}
				onpointerdown={(event) => take(event, grip)}
				onkeydown={(event) => nudge(event, grip)}
			></button>
		{/each}
	</div>
</figure>

<style>
	/* Clipped, and that is load-bearing twice over: it keeps a turned picture inside the rounded
	   corners, and it keeps the dimming inside the frame: the dimming is a shadow spreading
	   outwards from the rectangle, and unclipped it would cover the whole sheet. */
	.stage {
		position: relative;
		margin: 0;
		margin-inline: auto;
		overflow: hidden;
		border-radius: var(--radius-lg);
		background: var(--sift-surface-3);
		touch-action: none;
		user-select: none;
	}

	.marquee {
		position: absolute;
		box-sizing: border-box;
		border: 1px solid var(--sift-ink);
		box-shadow: 0 0 0 9999px var(--sift-crop-veil);
		cursor: move;
	}

	/* Big enough to take hold of with a finger, drawn small enough not to cover the picture: the
	   button is the hit area and the square in the middle of it is what is seen. */
	.grip {
		position: absolute;
		display: block;
		inline-size: 22px;
		block-size: 22px;
		padding: 0;
		/* Held inside the rectangle rather than straddling its edge. Centred on the edge, a corner
		   handle on a rectangle covering the whole picture loses half of itself to the frame's own
		   clipping, and that is the state the editor opens in. */
		translate: var(--shift-x, -50%) var(--shift-y, -50%);
		border: 0;
		border-radius: var(--radius-sm);
		/* Nothing drawn. The outline of the rectangle is the control, and nine squares on top of a
		   photograph are nine things covering the part of it somebody is trying to aim at. They are
		   still here, still the right size to grab, and still the only way to crop from a keyboard,
		   which is why they are invisible rather than gone. */
		background: transparent;
		cursor: grab;
	}

	.grip:focus-visible {
		outline: none;
		box-shadow: var(--focus-ring);
	}

	.grip[data-grip='move'] {
		cursor: move;
	}

	.grip[data-grip='nw'],
	.grip[data-grip='se'] {
		cursor: nwse-resize;
	}

	.grip[data-grip='ne'],
	.grip[data-grip='sw'] {
		cursor: nesw-resize;
	}

	.grip[data-grip='n'],
	.grip[data-grip='s'] {
		cursor: ns-resize;
	}

	.grip[data-grip='e'],
	.grip[data-grip='w'] {
		cursor: ew-resize;
	}
</style>
