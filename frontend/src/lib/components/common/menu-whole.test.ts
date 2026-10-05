/*
 * A chooser's side comes from the room around its press, never from how many rows it holds.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';

import { chooserSide, rootLength } from './menu-whole';

const room = (top: number, ceiling = 480) => ({
	top,
	bottom: top + 32,
	window: 1000,
	chrome: 0,
	ceiling
});

describe('chooserSide', () => {
	it('opens below when the room below holds the whole ceiling', () => {
		expect(chooserSide(room(400))).toBe('bottom');
	});

	it('opens above when the room below is short and there is more above', () => {
		expect(chooserSide(room(785))).toBe('top');
	});

	it('stays below when the room below is short but still the larger side', () => {
		expect(chooserSide(room(300))).toBe('bottom');
	});

	it("counts the window's title strip out of the room above", () => {
		expect(chooserSide({ ...room(500), chrome: 60 })).toBe('bottom');
		expect(chooserSide({ ...room(500), chrome: 0 })).toBe('top');
	});

	it('compares the two sides when the ceiling cannot be read', () => {
		expect(chooserSide(room(785, Infinity))).toBe('top');
		expect(chooserSide(room(100, Infinity))).toBe('bottom');
	});
});

describe('the shared ceiling', () => {
	afterEach(() => document.documentElement.style.removeProperty('--menu-max-height'));

	it('is the window, so a list is as tall as its rows where the window has the room', () => {
		const tokens = readFileSync(resolve('src/app.css'), 'utf8');
		expect(/--menu-max-height:\s*([^;]+);/.exec(tokens)?.[1]).toBe('100dvh');
	});

	it('reads as no ceiling to the side a chooser takes, and a pixel token as itself', () => {
		document.documentElement.style.setProperty('--menu-max-height', '100dvh');
		expect(rootLength('--menu-max-height', Infinity)).toBe(Infinity);
		document.documentElement.style.setProperty('--menu-max-height', '480px');
		expect(rootLength('--menu-max-height', Infinity)).toBe(480);
	});
});
