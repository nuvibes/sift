<script lang="ts">
	/*
	 * The shape of what somebody keeps coming back to, drawn above a scrubber.
	 *
	 * A line across the width of the file whose height at each point is how much of this account's
	 * watching went there. A file watched straight through once is flat; one with a moment replayed
	 * ten times has a hill over that moment, and the hill is the answer: "the good bit is about two
	 * thirds of the way in" is a thing a library can tell you rather than a thing you have to
	 * remember.
	 *
	 * ## Whose history it is
	 *
	 * One account's own. The sites draw this from millions of strangers and call it "most
	 * replayed"; a self-hosted library has one person in it, so this is that person's history with
	 * that file. Which is more useful rather than less: a stranger's peak is a guess about what you
	 * will want, and your own is a record of what you did.
	 *
	 * ## Why SVG, and why one path
	 *
	 * The curve is a hundred numbers that change only when a file is opened. A path is one element,
	 * one repaint and no layout, where a hundred divs would be a hundred boxes the browser has to
	 * place every time the bar is drawn: on top of a video, several times a second, on the surface
	 * with the least frame budget in the application.
	 *
	 * `preserveAspectRatio="none"` because the curve is a graph and not a picture: it is stretched to
	 * whatever width the scrubber is and its proportions mean nothing. The viewBox is the data's own
	 * units, so nothing here has to know how wide anything ended up.
	 */
	interface Props {
		/**
		 * One value per slice of the file, 0 to 1, tallest slice at 1.
		 *
		 * Already normalised by the server, so this component divides nothing: three readers each
		 * dividing by their own idea of the tallest point is three answers to one question.
		 */
		heat: readonly number[];
		/** What the curve is of, for anyone who cannot see it. */
		label?: string;
		/**
		 * Whether the timeline is being attended to, and the curve should be on screen.
		 *
		 * Asked for rather than decided here, because the answer is about the SCRUBBER (where the
		 * pointer is, whether the keyboard is in it, whether a marker is being dragged) and this
		 * component is a shape on top of one. It would have to reach outside itself to find out, and
		 * would then be a second place that knew what "being used" means for a control it is not.
		 */
		showing?: boolean;
	}

	let { heat, label = 'What you have replayed most', showing = true }: Props = $props();

	/** The height of the box the curve is drawn in, in the viewBox's own units. */
	const TALL = 100;

	/*
	 * The curve as a filled path, smoothed.
	 *
	 * Smoothed rather than drawn as the raw hundred steps, and this is a reading decision rather than
	 * a decorative one: the underlying numbers are quantised twice over (into a hundred slices, and
	 * into quarter-second ticks) so the raw line is a comb of single-slice spikes that says
	 * "somebody paused here" as loudly as it says "somebody watched this ten times". A curve through
	 * the points shows the shape of the attention, which is the only thing anybody reads it for.
	 *
	 * Catmull-Rom converted to cubic Beziers: it passes THROUGH every point rather than near it, so
	 * no peak is invented and none is flattened away. A plain quadratic smoothing would move the
	 * maximum off the moment it belongs to, which on a scrubber means pointing at the wrong second.
	 */
	const path = $derived.by(() => {
		const points = heat;
		if (points.length === 0) return '';
		const wide = points.length - 1;
		const y = (at: number) => TALL - Math.max(0, Math.min(1, points[at] ?? 0)) * TALL;
		if (points.length === 1) return `M 0 ${y(0)} L ${wide} ${y(0)} L ${wide} ${TALL} L 0 ${TALL} Z`;

		let d = `M 0 ${y(0)}`;
		for (let at = 0; at < wide; at += 1) {
			/*
			 * The two neighbours on either side, held at the ends, which makes the first and last
			 * segments curve like the rest instead of arriving as straight lines.
			 *
			 * All four go through `y`, where the 0-to-1 range is held, so a value outside the range
			 * cannot put one anchor at the box's edge and the rest of the curve off it. The server
			 * normalises, so nothing shipped produces one, which is exactly when a half-applied
			 * guard would go unnoticed.
			 */
			const y0 = y(Math.max(0, at - 1));
			const y1 = y(at);
			const y2 = y(at + 1);
			const y3 = y(Math.min(wide, at + 2));
			// The sixth is the standard Catmull-Rom tension. Written out rather than named, because
			// it is one number in one formula and a constant beside it would be a second place to
			// look for something that cannot be changed independently.
			d += ` C ${at + 1 / 3} ${y1 + (y2 - y0) / 6}, ${at + 2 / 3} ${y2 - (y3 - y1) / 6}, ${at + 1} ${y2}`;
		}
		// Closed along the bottom, so it is an area rather than a line. A line over a video is a
		// scratch; an area reads as a quantity.
		return `${d} L ${wide} ${TALL} L 0 ${TALL} Z`;
	});
</script>

<!-- `aria-hidden` and a title do not go together, so this is a picture with a name. It is genuinely
     informative: it is the only thing on screen saying which part of a file has been watched most.
     And a reader that cannot see it should be told the name rather than nothing. -->
<svg
	class="curve"
	class:showing
	viewBox="0 0 {Math.max(1, heat.length - 1)} {TALL}"
	preserveAspectRatio="none"
	role="img"
	aria-label={label}
	aria-hidden={!showing}
>
	<path d={path} />
</svg>

<style>
	.curve {
		position: absolute;
		/* Sits ON the timeline's box and above the track, the way the sites draw it: the curve and
		   the bar it describes are one control, and a gap between them would read as two. */
		inset-inline-start: 0;
		bottom: 50%;
		block-size: 26px;
		/*
		 * The width is stated; `inset-inline: 0` is not enough on its own. An `<svg>` is a replaced
		 * element with an intrinsic aspect ratio from its viewBox, and for an absolutely positioned
		 * replaced element with `width: auto` the ratio wins: the used width is the height times
		 * the ratio and both offsets are dropped (a 26px tall curve with a hundred-slice viewBox
		 * would be drawn about 26px wide). Invisible to reading and obvious to measuring.
		 */
		inline-size: 100%;
		/* Behind everything that can be operated (the slider, the loop markers, the scrub frame)
		   and out of the pointer's way entirely. It is a picture of what happened; there is nothing
		   here to press, and a hit target over a scrubber is a seek that goes to the wrong second. */
		z-index: 0;
		pointer-events: none;
		/* The paint has to be allowed outside the box it is placed in. See `bottom: 50%`, which puts
		   most of the curve above the timeline's own rectangle. */
		overflow: visible;
		/*
		 * Shown when the bar is being used, and not otherwise. See `showing`.
		 *
		 * Faded rather than removed, so it arrives and leaves instead of appearing. Opacity alone,
		 * with nothing else animated: it is composited, so it costs no layout on the one surface in
		 * the application with the least frame budget: a video, several times a second.
		 *
		 * `--dur-fast` rather than `--dur-slow`. This follows the pointer, and a third of a second
		 * behind a pointer is a thing you notice arriving late; the stepped zoom is slow because it
		 * is a jump between two places and the eye has to be carried across.
		 */
		opacity: 0;
		transition: opacity var(--dur-fast) var(--ease);
	}

	.curve.showing {
		opacity: 1;
	}

	/* Mixed from the ink rather than given a colour of its own, which is what makes it survive both
	   themes without a second rule: `--foreground` already flips, and the mix follows it. A fixed
	   grey would be the one that has to be written twice and then only maintained once.

	   No stroke. A stroke on a shape stretched this far horizontally is drawn at the stretched width
	   too, so the line would be hairline across the top and heavy down the sides. */
	.curve path {
		fill: color-mix(in oklab, var(--foreground) 30%, transparent);
		stroke: none;
	}
</style>
