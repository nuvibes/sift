/*
 * Theater's own controls on the top bar: what acts on the whole wall, and nothing about one cell.
 * A cell's Screenshot is in that cell's drawer, first, where the popout player's drawer keeps it.
 */

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { afterEach, describe, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import TheaterTools from './TheaterTools.svelte';
import { showing, Wall } from '$lib/theater/wall.svelte';

let host: HTMLElement;
let drawn: ReturnType<typeof mount> | null = null;

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	showing.wall = null;
	host?.remove();
});

function draw(): Wall {
	const wall = new Wall();
	showing.wall = wall;
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(TheaterTools, { target: host });
	flushSync();
	return wall;
}

function labels(): string[] {
	return [...host.querySelectorAll('button')].map((one) => one.getAttribute('aria-label') ?? '');
}

describe('the Theater tools on the top bar', () => {
	it('draw the silence alone: no Screenshot of a cell, nothing sent to a phone', () => {
		const wall = draw();
		expect(labels()).toEqual(['Unmute everything']);

		wall.focused = 2;
		flushSync();
		expect(labels().some((one) => one.startsWith('Screenshot'))).toBe(false);
	});

	it("leave Screenshot to the cell's drawer, first, as the popout player's drawer has it", () => {
		const here = dirname(fileURLToPath(import.meta.url));
		const controls = readFileSync(join(here, 'CellControls.svelte'), 'utf8');
		const tray = controls.slice(controls.indexOf('{#snippet tray()}'));
		const first = tray.indexOf('<ScreenshotButton');
		expect(first, "the cell's drawer lost its Screenshot").toBeGreaterThan(-1);
		expect(tray.indexOf('<Tooltip')).toBeGreaterThan(first);
	});
});
