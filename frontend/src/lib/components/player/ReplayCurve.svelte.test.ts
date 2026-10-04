/* The shape of what somebody keeps coming back to, drawn above a scrubber.
 *
 * A line across the width of the file whose height at each point is how much of THIS account's
 * watching went there. The peak is the answer: "the good bit is about two thirds of the way in" is
 * a thing a library can tell you rather than a thing you have to remember, so the assertions here
 * are about where the peak lands and about the curve passing through its points rather than near
 * them. A smoothing that moved the maximum off the moment it belongs to would, on a scrubber, be
 * pointing at the wrong second.
 */

import { afterEach, describe, expect, it } from 'vitest';
import { flushSync, mount } from 'svelte';

import ReplayCurve from './ReplayCurve.svelte';

let host: HTMLElement;

afterEach(() => {
	host?.remove();
});

/* The component's own props rather than a bag of unknowns: `heat` is required, and a `Record` hides
   that from the type checker until somebody forgets it. */
function draw(props: { heat: readonly number[]; label?: string; showing?: boolean }) {
	host = document.createElement('div');
	document.body.append(host);
	mount(ReplayCurve, { target: host, props });
	flushSync();
	return host.querySelector('svg') as SVGSVGElement;
}

/** Every point the path is anchored at, as [x, y] pairs. */
function anchors(path: string): [number, number][] {
	const found: [number, number][] = [];
	for (const piece of path.matchAll(/[MLC]([^MLCZ]+)/g)) {
		const numbers = piece[1]
			.trim()
			.split(/[\s,]+/)
			.map(Number);
		// A cubic names two control points before the anchor it ends on; M and L name only theirs.
		found.push([numbers[numbers.length - 2], numbers[numbers.length - 1]]);
	}
	return found;
}

describe('the curve', () => {
	it('draws nothing at all for a file nobody has replayed a slice of', () => {
		// An empty answer is a real one (a file opened once and watched straight through) and it
		// draws as no path rather than as a flat line pretending to be data.
		expect(draw({ heat: [] }).querySelector('path')?.getAttribute('d')).toBe('');
	});

	it('is an AREA rather than a line, closed along the bottom', () => {
		/* A line over a video is a scratch; an area reads as a quantity. */
		const d = draw({ heat: [0.2, 1, 0.2] })
			.querySelector('path')!
			.getAttribute('d')!;

		expect(d.startsWith('M ')).toBe(true);
		expect(d.trimEnd().endsWith('Z')).toBe(true);
	});

	it('passes THROUGH every point rather than near it, so no peak moves', () => {
		/* Catmull-Rom rather than a plain quadratic smoothing, and this is the reason: the tallest
		   point has to sit at the slice it belongs to. The viewBox is the data's own units, so slice
		   1 of three is x = 1 and a height of 1 is y = 0. */
		const d = draw({ heat: [0, 1, 0] })
			.querySelector('path')!
			.getAttribute('d')!;
		const points = anchors(d);

		expect(points).toContainEqual([1, 0]);
	});

	it('takes a value outside the range it promises as the edge of it', () => {
		/* Already normalised by the server, so nothing here divides: three readers each dividing by
		   their own idea of the tallest point is three answers to one question. What arrives out of
		   range is clamped rather than drawn off the top of the box. */
		const d = draw({ heat: [2, -1] })
			.querySelector('path')!
			.getAttribute('d')!;

		expect(d).toContain('M 0 0');
		expect(anchors(d)[1]).toEqual([1, 100]);
	});

	it('stretches to whatever width it is given, because it is a graph and not a picture', () => {
		const svg = draw({ heat: [0, 0.5, 1, 0.5] });

		expect(svg.getAttribute('preserveAspectRatio')).toBe('none');
		expect(svg.getAttribute('viewBox')).toBe('0 0 3 100');
	});

	it('is named for anyone who cannot see it, and hidden from them while it is not on screen', () => {
		/* `aria-hidden` and a title do not go together, so it is a picture with a name: it is the
		   only thing on screen saying which part of a file has been watched most. Hidden while the
		   bar is not attended to, because a shape nobody can see is not worth announcing. */
		const showing = draw({ heat: [0, 1], label: 'What you have replayed most' });
		expect(showing.getAttribute('aria-label')).toBe('What you have replayed most');
		expect(showing.getAttribute('aria-hidden')).toBe('false');

		const hidden = draw({ heat: [0, 1], showing: false });
		expect(hidden.getAttribute('aria-hidden')).toBe('true');
		expect(hidden.classList.contains('showing')).toBe(false);
	});

	it('draws a single slice as a block rather than as nothing', () => {
		/* One slice is a file short enough that the whole of it is one bucket. There is no curve to
		   draw through one point, and the honest shape is the whole width at that height. */
		const d = draw({ heat: [1] })
			.querySelector('path')!
			.getAttribute('d')!;

		expect(d).toBe('M 0 0 L 0 0 L 0 100 L 0 100 Z');
	});
});
