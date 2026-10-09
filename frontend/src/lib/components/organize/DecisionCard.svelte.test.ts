/*
 * A card that opens something: a press anywhere on it that is not a control or a link does what
 * its activation does, and the whole card answers the pointer.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createRawSnippet, flushSync, mount, unmount } from 'svelte';
import { goto } from '$app/navigation';
import DecisionCard from './DecisionCard.svelte';
import source from './DecisionCard.svelte?raw';

vi.mock('$app/navigation', () => ({ goto: vi.fn() }));

const inside = createRawSnippet(() => ({
	render: () =>
		'<div><p class="words">Three faces</p><a class="link" href="/x">Open</a><button class="act">Yes</button></div>'
}));

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

function draw(opens?: string | (() => void)): HTMLElement {
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(DecisionCard, { target: host, props: { children: inside, opens } });
	flushSync();
	return host;
}

const press = (on: Element | null, init: MouseEventInit = {}) =>
	on?.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, ...init }));

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
	vi.mocked(goto).mockClear();
	vi.unstubAllGlobals();
});

describe('a card that opens something', () => {
	it('goes where it opens from a press on its empty ground or its words', () => {
		draw('/organize/faces-to-name/1');
		press(host.querySelector('.panel'));
		press(host.querySelector('.words'));
		expect(goto).toHaveBeenCalledTimes(2);
		expect(goto).toHaveBeenCalledWith('/organize/faces-to-name/1');
	});

	it('leaves a press on its buttons and links to them', () => {
		draw('/organize/faces-to-name/1');
		host.querySelector('.link')?.addEventListener('click', (event) => event.preventDefault());
		press(host.querySelector('.act'));
		press(host.querySelector('.link'));
		expect(goto).not.toHaveBeenCalled();
	});

	it("hands the press to the card's own link to the same place, with its keys", () => {
		draw('/x');
		const link = host.querySelector('.link') as HTMLElement;
		const heard: MouseEvent[] = [];
		link.addEventListener('click', (event) => {
			heard.push(event);
			event.preventDefault();
		});
		press(host.querySelector('.words'), { shiftKey: true });
		expect(heard).toHaveLength(1);
		expect(heard[0].shiftKey).toBe(true);
		expect(goto).not.toHaveBeenCalled();
	});

	it('runs a call it is handed, and nothing for a press that is not the main button', () => {
		const opened = vi.fn();
		draw(opened);
		press(host.querySelector('.act'));
		expect(opened).not.toHaveBeenCalled();
		press(host.querySelector('.words'), { button: 1 });
		expect(opened).not.toHaveBeenCalled();
		press(host.querySelector('.words'));
		expect(opened).toHaveBeenCalledTimes(1);
	});

	it('opens a new tab where the press asks for one, and nothing over selected words', () => {
		const open = vi.fn();
		vi.stubGlobal('open', open);
		draw('/people/2');
		press(host.querySelector('.words'), { ctrlKey: true });
		expect(open).toHaveBeenCalledWith('/people/2', '_blank');
		vi.stubGlobal('getSelection', () => 'Three');
		press(host.querySelector('.words'));
		expect(goto).not.toHaveBeenCalled();
	});

	it('is no press and draws no hover where it opens nothing', () => {
		draw();
		press(host.querySelector('.words'));
		expect(goto).not.toHaveBeenCalled();
		expect(host.querySelector('.card.opens')).toBeNull();
	});

	it('answers the pointer with the state layer over the whole card', () => {
		draw('/x');
		expect(host.querySelector('.card.opens')).not.toBeNull();
		const style = /<style>([\s\S]*)<\/style>/.exec(source)?.[1] ?? '';
		expect(style).toMatch(
			/\.card\.opens:hover :global\(\.panel\) \{\s*background: var\(--sift-card-hover-layer\), var\(--sift-card-fill\);/
		);
		expect(style).toMatch(/\.card\.opens \{\s*cursor: pointer;/);
	});

	it('lifts under the pointer, as an entity card does, and settles under a press', () => {
		const style = /<style>([\s\S]*)<\/style>/.exec(source)?.[1] ?? '';
		expect(style).toMatch(
			/\.card\.opens:hover \{\s*transform: translateY\(var\(--lift-y\)\);\s*box-shadow: var\(--elev-tile-lift\);/
		);
		expect(style).toMatch(
			/\.card\.opens:active:not\(:has\(a:active, button:active\)\) \{\s*transform: none;/
		);
	});
});
