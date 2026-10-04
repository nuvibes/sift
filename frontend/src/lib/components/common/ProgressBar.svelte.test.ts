import { afterEach, describe, expect, it } from 'vitest';
import { flushSync, mount } from 'svelte';
import { reactiveProps } from '$lib/design/testing.svelte';
import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import ProgressBar from './ProgressBar.svelte';
import source from './ProgressBar.svelte?raw';

/* Two states that look alike and are not: nothing yet, and no idea.
 *
 * A download at 0% and a download whose size the server has not said yet are both an empty bar. One
 * of them is going to move and the other one cannot, and someone watching deserves to know which.
 */

let host: HTMLElement;

afterEach(() => {
	host?.remove();
});

function render(props: { value?: number | null; max?: number; label: string; paused?: boolean }) {
	host = document.createElement('div');
	document.body.append(host);

	const all = reactiveProps(props);
	mount(ProgressBar, { target: host, props: all });
	flushSync();

	return {
		props: all,
		track: host.querySelector('.track') as HTMLElement,
		fill: host.querySelector('.fill') as HTMLElement
	};
}

describe('a known amount', () => {
	it('says how far along it is', () => {
		const { track } = render({ value: 40, label: 'Downloading' });

		expect(track.getAttribute('aria-valuenow')).toBe('40');
	});

	it('fills to that share of the track', () => {
		const { fill } = render({ value: 40, label: 'Downloading' });

		expect(fill.style.inlineSize).toBe('40%');
	});

	it('is named, so it is not just a rectangle', () => {
		const { track } = render({ value: 10, label: 'Downloading holiday.mp4' });

		expect(track.getAttribute('aria-label')).toBe('Downloading holiday.mp4');
	});
});

describe('a value out of range', () => {
	it('cannot escape its track', () => {
		// The number comes from a job's own reckoning of its progress. A fill wider than the bar does
		// not stop at the edge: it paints across whatever is next to it.
		const { fill } = render({ value: 150, label: 'Downloading' });

		expect(fill.style.inlineSize).toBe('100%');
	});

	it('cannot go backwards past empty', () => {
		const { fill } = render({ value: -20, label: 'Downloading' });

		expect(fill.style.inlineSize).toBe('0%');
	});
});

describe('an unknown amount', () => {
	it('claims no position at all', () => {
		// Not zero. Zero is a claim, and it is one this bar cannot make: saying 0% of an unknown
		// total is telling someone something nobody knows.
		const { track } = render({ value: null, label: 'Starting' });

		expect(track.hasAttribute('aria-valuenow')).toBe(false);
		expect(track.getAttribute('data-indeterminate')).toBe('');
	});

	it('sweeps instead, and does not have a width forced onto it', () => {
		// The sweep sets its own width in the stylesheet. An inline width here would win over that
		// and the animation would run on a bar of the wrong size, or of no size at all.
		const { fill } = render({ value: null, label: 'Starting' });

		expect(fill.classList.contains('indeterminate')).toBe(true);
		expect(fill.style.inlineSize).toBe('');
	});
});

describe('when the size becomes known', () => {
	it('stops sweeping and starts reporting', () => {
		const { props, track, fill } = render({ value: null, label: 'Downloading' });
		expect(fill.classList.contains('indeterminate')).toBe(true);

		props.value = 25;
		flushSync();

		expect(fill.classList.contains('indeterminate')).toBe(false);
		expect(track.getAttribute('aria-valuenow')).toBe('25');
	});
});

describe('a figure that goes down', () => {
	/* A job that starts over, or whose estimate of its own size grew, reports a lower share. The bar
	   jumps there: a fill seen draining reads as work being undone. */
	afterEach(() => removeStyles());

	it('jumps to it rather than easing back, and eases forwards again after', () => {
		const { props, fill } = render({ value: 60, label: 'Downloading' });
		applyStyles(source, fill);
		expect(getComputedStyle(fill).transition).toContain('inline-size');

		props.value = 20;
		flushSync();
		expect(fill.classList.contains('back')).toBe(true);
		expect(getComputedStyle(fill).transition).toBe('none');

		props.value = 40;
		flushSync();
		expect(fill.classList.contains('back')).toBe(false);
		expect(getComputedStyle(fill).transition).toContain('inline-size');
	});
});

describe('when the work is held', () => {
	/* The fill is how much is on disk. A held bar keeps it, in a colour that is plainly not the
	   colour of work in flight, and above all it does not move: an animation over a row whose
	   word says "Paused" says the opposite of the word. */
	it('holds still, in grey, at the place it had reached', () => {
		const { track, fill } = render({ value: 62, label: 'Paused', paused: true });

		expect(track.classList.contains('paused')).toBe(true);
		expect(fill.classList.contains('indeterminate')).toBe(false);
		expect(fill.style.inlineSize).toBe('62%');
	});

	it('does not sweep even where the size is unknown', () => {
		// The sweep is for work that IS happening with no figure to show for it yet. Held, there is
		// no work happening at all, so the one thing the bar must not do is imply there is.
		const { fill } = render({ value: null, label: 'Paused', paused: true });

		expect(fill.classList.contains('indeterminate')).toBe(false);
	});
});
