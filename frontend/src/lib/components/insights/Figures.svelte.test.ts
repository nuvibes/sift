/* Insights' figures count up from zero when a period's answer arrives, and at no other time: the
 * one count in the app that moves. The cell is as wide as the final figure throughout, and reduced
 * motion draws the figure immediately. */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import { motion } from '$lib/shell/motion.svelte';
import { reactiveProps, words } from '$lib/design/testing.svelte';
import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import { clockTime } from '$lib/shell/when';
import Figures from './Figures.svelte';
import source from '$lib/components/charts/FigureCard.svelte?raw';
import figuresSource from './Figures.svelte?raw';
import type { Figure } from './figures';

/* Frames are run by hand, each at a time the test names. */
let frames: FrameRequestCallback[];
let host: HTMLElement;
let drawn: Record<string, unknown> | undefined;

beforeEach(() => {
	frames = [];
	vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
		frames.push(callback);
		return frames.length;
	});
	vi.stubGlobal('cancelAnimationFrame', () => {
		frames = [];
	});
	motion.preference = 'full';
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = undefined;
	host?.remove();
	vi.unstubAllGlobals();
	removeStyles();
});

/** Run every frame waiting, all at the one moment `ms`. */
function frameAt(ms: number): void {
	for (const next of frames.splice(0)) next(ms);
	flushSync();
}

/** A figure as the server sends one, with no caption under it. */
function figure(label: string, value: number, unit: Figure['unit']): Figure {
	return {
		label,
		value,
		unit,
		hidden_part: 0,
		caption: [],
		defines: [],
		said: '',
		hidden_said: '',
		trend: []
	};
}

function sessions(value: number): Figure {
	return figure('Sessions', value, 'count');
}

function draw(figures: Figure[]) {
	const props = reactiveProps({ figures });
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(Figures, { target: host, props });
	flushSync();
	return props;
}

const counted = () => words(host.querySelector('.value .count'));

describe('a period arriving', () => {
	it('counts each figure up from zero, and ends at its value', () => {
		draw([sessions(1240)]);
		expect(counted()).toBe('0');

		frameAt(1000);
		frameAt(1300);
		const halfway = Number(counted().replace(',', ''));
		expect(halfway).toBeGreaterThan(0);
		expect(halfway).toBeLessThan(1240);

		frameAt(1600);
		expect(counted()).toBe('1,240');
	});

	it('is as wide as the final figure from the first frame', () => {
		draw([sessions(1240)]);
		const value = host.querySelector('.value') as HTMLElement;
		applyStyles(source, value);

		expect(counted()).toBe('0');
		// The final figure is drawn, unseen, in the same grid cell as the count.
		expect(value.getAttribute('data-final')).toBe('1,240');
		expect(getComputedStyle(value).display).toBe('grid');
	});

	it('counts again when the next period arrives, which draws its cards anew', () => {
		draw([sessions(1240)]);
		frameAt(1000);
		frameAt(1600);
		expect(counted()).toBe('1,240');

		if (drawn) unmount(drawn);
		host.remove();
		draw([sessions(310)]);
		expect(counted()).toBe('0');
		frameAt(2000);
		frameAt(2600);
		expect(counted()).toBe('310');
	});

	it('draws a figure changed by a re-read immediately, and an unchanged one stays still', () => {
		const props = draw([sessions(1240), figure('Hours', 12, 'count')]);
		frameAt(1000);
		frameAt(1600);
		expect(counted()).toBe('1,240');

		props.figures = [sessions(1241), figure('Hours', 12, 'count')];
		flushSync();
		expect(counted(), 'a re-read counted the figure up from zero again').toBe('1,241');
		expect(frames, 'a re-read started a count').toHaveLength(0);
	});

	it('draws a time of day immediately, because it is not a quantity', () => {
		draw([figure('Usual start', 430, 'minute_of_day')]);

		expect(counted()).toBe(clockTime('07:10'));
		expect(frames).toHaveLength(0);
	});
});

describe('when motion is off', () => {
	it('draws the figure immediately', () => {
		motion.preference = 'reduce';
		draw([sessions(1240)]);

		expect(counted()).toBe('1,240');
		expect(frames).toHaveLength(0);
	});
});

it('shares the row between the cards it has, leaving no room for a card that is not there', () => {
	draw([sessions(1240), sessions(12), sessions(3)]);
	const row = host.querySelector('.figures') as HTMLElement;
	applyStyles(figuresSource, row);
	expect(getComputedStyle(row).gridTemplateColumns).toContain('auto-fit');
});
