<script lang="ts">
	/* What this account keeps coming back to, as one SVG path stretched to the scrubber. */
	interface Props {
		/** Normalised by the server, 0 to 1. */
		heat: readonly number[];
		label?: string;
		showing?: boolean;
	}

	let { heat, label = 'What you have replayed most', showing = true }: Props = $props();

	const TALL = 100;

	/* Smoothed through every point (Catmull-Rom), so no peak moves off its second. */
	const path = $derived.by(() => {
		const points = heat;
		if (points.length === 0) return '';
		const wide = points.length - 1;
		const y = (at: number) => TALL - Math.max(0, Math.min(1, points[at] ?? 0)) * TALL;
		if (points.length === 1) return `M 0 ${y(0)} L ${wide} ${y(0)} L ${wide} ${TALL} L 0 ${TALL} Z`;

		let d = `M 0 ${y(0)}`;
		for (let at = 0; at < wide; at += 1) {
			const y0 = y(Math.max(0, at - 1));
			const y1 = y(at);
			const y2 = y(at + 1);
			const y3 = y(Math.min(wide, at + 2));
			d += ` C ${at + 1 / 3} ${y1 + (y2 - y0) / 6}, ${at + 2 / 3} ${y2 - (y3 - y1) / 6}, ${at + 1} ${y2}`;
		}
		return `${d} L ${wide} ${TALL} L 0 ${TALL} Z`;
	});
</script>

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
		inset-inline-start: 0;
		bottom: 50%;
		block-size: 26px;
		/* Stated: a replaced `<svg>` with `width: auto` takes its width from the viewBox ratio. */
		inline-size: 100%;
		z-index: 0;
		pointer-events: none;
		overflow: visible;
		opacity: 0;
		transition: opacity var(--dur-fast) var(--ease);
	}

	.curve.showing {
		opacity: 1;
	}

	.curve path {
		fill: color-mix(in oklab, var(--foreground) 30%, transparent);
		stroke: none;
	}
</style>
