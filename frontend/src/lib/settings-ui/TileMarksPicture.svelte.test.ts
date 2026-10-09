/* The picture of a tile that every mark is set from. */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount } from 'svelte';
import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import pressable from '$lib/components/common/Pressable.svelte?raw';
import TileMarksPicture from './TileMarksPicture.svelte';
import source from './TileMarksPicture.svelte?raw';
import {
	ALWAYS,
	MARK_KEYS,
	NEVER,
	ON_HOVER,
	PINNED_MARK,
	type MarkAnswer
} from '$lib/grid/tile-marks.svelte';

/* The store, stood in for. Its own behaviour (what it saves, what it puts back on a refusal) is
   `tile-marks.svelte.test.ts`. */
const answers = vi.hoisted(() => new Map<string, string>());
const set = vi.hoisted(() => vi.fn());

vi.mock('$lib/grid/tile-marks.svelte', async (importOriginal) => {
	const actual = await importOriginal<typeof import('$lib/grid/tile-marks.svelte')>();
	return {
		...actual,
		tileMarks: {
			answer: (key: string) => answers.get(key) ?? actual.ALWAYS,
			set: (key: string, answer: MarkAnswer) => set(key, answer),
			shows: (key: string) => (answers.get(key) ?? actual.ALWAYS) !== actual.NEVER,
			hoverClass: () => ''
		}
	};
});

/** The declarations the words come from. Every label and sentence on this picture is the setting's
 *  own, read out of the panel, so the picture cannot drift from the registry. */
const declarations = {
	entry: (key: string) => ({ label: `The ${key.split('.').pop()} mark`, help: `What ${key} does.` })
};

let host: HTMLDivElement;

beforeEach(() => {
	answers.clear();
	set.mockResolvedValue(undefined);
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	host.remove();
	removeStyles();
	vi.clearAllMocks();
});

function draw() {
	mount(TileMarksPicture, { target: host, props: { declarations } as never });
	flushSync();
	return host;
}

function marks(): HTMLElement[] {
	return [...host.querySelectorAll('[aria-label*="Press to change"]')] as HTMLElement[];
}

it('offers one pressable mark for every declared key, and no more', () => {
	draw();

	expect(marks()).toHaveLength(MARK_KEYS.length);
});

it('says each mark s name and its current answer, for somebody who cannot see the picture', () => {
	answers.set(PINNED_MARK, ON_HOVER);

	draw();

	const said = marks().map((each) => each.getAttribute('aria-label'));
	expect(said).toContain('The pinned mark: only when you point at the tile. Press to change.');
	/* And the ones nobody set read as the shipped answer rather than as blank. */
	expect(said.filter((one) => one?.includes(': always.'))).toHaveLength(MARK_KEYS.length - 1);
});

it('steps always -> only on hover on a press', () => {
	draw();

	const pinned = marks().find((each) => each.getAttribute('aria-label')?.startsWith('The pinned'));
	pinned?.click();

	expect(set).toHaveBeenCalledWith(PINNED_MARK, ON_HOVER);
});

it('steps only on hover -> never, and never -> always, so a press always changes what you see', () => {
	answers.set(PINNED_MARK, ON_HOVER);
	draw();
	marks()
		.find((each) => each.getAttribute('aria-label')?.startsWith('The pinned'))
		?.click();
	expect(set).toHaveBeenLastCalledWith(PINNED_MARK, NEVER);

	host.replaceChildren();
	answers.set(PINNED_MARK, NEVER);
	draw();
	marks()
		.find((each) => each.getAttribute('aria-label')?.startsWith('The pinned'))
		?.click();
	expect(set).toHaveBeenLastCalledWith(PINNED_MARK, ALWAYS);
});

it('takes every word from the declarations rather than writing its own', () => {
	draw();

	/* A label the component invented would still draw; one read from the panel carries the shape
	   the stand-in gave it. */
	for (const each of marks()) {
		expect(each.getAttribute('aria-label')).toMatch(/^The \w+ mark: /);
	}
});

it("lays out each mark's target over Pressable's block, whichever sheet arrives last", () => {
	draw();
	const target = marks()[0];
	/* This file's sheet first, so equal weight would hand the answer to `Pressable`, which is
	   the coin toss the rule must not rest on. */
	applyStyles(source, target.closest('.picture'));
	applyStyles(pressable, target);

	/* Another file's picture, holding something called a target: not this file's to dress. */
	const elsewhere = document.createElement('div');
	elsewhere.className = 'picture';
	elsewhere.innerHTML = '<span class="target">Hint</span>';
	host.append(elsewhere);

	expect(getComputedStyle(target).display).toBe('inline-flex');
	expect(getComputedStyle(elsewhere.firstElementChild as Element).display).not.toBe('inline-flex');
});
