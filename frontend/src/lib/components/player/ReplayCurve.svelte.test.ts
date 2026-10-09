/* The replay curve's peak lands where it belongs and the curve passes through its points. */

import { afterEach, describe, expect, it } from 'vitest';
import { flushSync, mount } from 'svelte';

import ReplayCurve from './ReplayCurve.svelte';

let host: HTMLElement;

afterEach(() => {
	host?.remove();
});

function draw(props: { heat: readonly number[]; label?: string; showing?: boolean }) {
	host = document.createElement('div');
	document.body.append(host);
	mount(ReplayCurve, { target: host, props });
	flushSync();
	return host.querySelector('svg') as SVGSVGElement;
}

function anchors(path: string): [number, number][] {
	const found: [number, number][] = [];
	for (const piece of path.matchAll(/[MLC]([^MLCZ]+)/g)) {
		const numbers = piece[1]
			.trim()
			.split(/[\s,]+/)
			.map(Number);
		found.push([numbers[numbers.length - 2], numbers[numbers.length - 1]]);
	}
	return found;
}

describe('the curve', () => {
	it('draws nothing at all for a file nobody has replayed a slice of', () => {
		// An empty answer draws no path, not a flat line.
		expect(draw({ heat: [] }).querySelector('path')?.getAttribute('d')).toBe('');
	});

	it('is an AREA rather than a line, closed along the bottom', () => {
		const d = draw({ heat: [0.2, 1, 0.2] })
			.querySelector('path')!
			.getAttribute('d')!;

		expect(d.startsWith('M ')).toBe(true);
		expect(d.trimEnd().endsWith('Z')).toBe(true);
	});

	it('passes THROUGH every point rather than near it, so no peak moves', () => {
		/* Catmull-Rom: the tallest point sits at its own slice. */
		const d = draw({ heat: [0, 1, 0] })
			.querySelector('path')!
			.getAttribute('d')!;
		const points = anchors(d);

		expect(points).toContainEqual([1, 0]);
	});

	it('takes a value outside the range it promises as the edge of it', () => {
		/* Normalised by the server; out of range is clamped. */
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
		/* A picture with a name, hidden while not attended to. */
		const showing = draw({ heat: [0, 1], label: 'What you have replayed most' });
		expect(showing.getAttribute('aria-label')).toBe('What you have replayed most');
		expect(showing.getAttribute('aria-hidden')).toBe('false');

		const hidden = draw({ heat: [0, 1], showing: false });
		expect(hidden.getAttribute('aria-hidden')).toBe('true');
		expect(hidden.classList.contains('showing')).toBe(false);
	});

	it('draws a single slice as a block rather than as nothing', () => {
		const d = draw({ heat: [1] })
			.querySelector('path')!
			.getAttribute('d')!;

		expect(d).toBe('M 0 0 L 0 0 L 0 100 L 0 100 Z');
	});
});
