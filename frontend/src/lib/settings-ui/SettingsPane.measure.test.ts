/*
 * A pane's prose takes the row help's measure from the frame, so a paragraph a pane did not bound
 * itself stops at a readable width, and one a pane bounded tighter keeps its own.
 */
import { afterEach, expect, it } from 'vitest';

import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import source from './SettingsPane.svelte?raw';
import rowSource from '$lib/components/common/LabelledRow.svelte?raw';

afterEach(() => {
	removeStyles();
	document.body.innerHTML = '';
});

function paragraphIn(frame: string, className = 'lede'): HTMLElement {
	const body = document.createElement('div');
	body.className = frame;
	const pane = document.createElement('div');
	const p = document.createElement('p');
	p.className = className;
	pane.append(p);
	body.append(pane);
	document.body.append(body);
	return p;
}

it('bounds every paragraph in a pane and in a sub-page to the row help measure', () => {
	applyStyles(source);
	const help = /\.help\s*\{[^}]*max-width:\s*([^;]+);/.exec(rowSource)?.[1].trim();
	expect(help).toBe('var(--reading-measure)');

	for (const frame of ['section-body section-stack', 'sub-page section-stack']) {
		const lede = paragraphIn(frame);
		const note = paragraphIn(frame, 'note');
		expect(getComputedStyle(lede).getPropertyValue('max-inline-size')).toBe(help);
		expect(getComputedStyle(note).getPropertyValue('max-inline-size')).toBe(help);
	}
});

it('draws nothing outside settings, and yields to a pane that bounds its own paragraph', () => {
	applyStyles(source);
	expect(getComputedStyle(paragraphIn('page')).maxInlineSize).not.toBe('var(--reading-measure)');

	const own = document.createElement('style');
	own.textContent = '.lede.pane-own { max-inline-size: 60ch; }';
	document.head.append(own);
	const lede = paragraphIn('section-body', 'lede pane-own');
	expect(getComputedStyle(lede).getPropertyValue('max-inline-size')).toBe('60ch');
	own.remove();
});

/*
 * ONE MEASURE: no pane sets a line length of its own on its prose, so every paragraph in Settings
 * wraps at the frame's. A box or a span the frame's paragraph rule cannot reach may repeat the one
 * measure; any other measure in `ch` is a column of a table or a legend, named here.
 */
const PANES = import.meta.glob('./*.svelte', { query: '?raw', import: 'default', eager: true });

/** Measures that bound something other than prose, by file and rule. */
const NOT_PROSE: Readonly<Record<string, readonly string[]>> = {
	// The badge legend's sentence column, and three table cells sized to what they hold.
	'./SupportedSites.svelte': ['.answer-key', '.hosts', '.cookies', '.walls']
};

it('leaves the measure of every pane paragraph to the frame', () => {
	const own: string[] = [];
	for (const [file, text] of Object.entries(PANES)) {
		const style = /<style>([\s\S]*)<\/style>/.exec(text as string)?.[1] ?? '';
		for (const rule of style.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
			const measure = /max-(?:inline-size|width):\s*([\d.]+ch)/.exec(rule[2])?.[1];
			if (measure === undefined || measure === '68ch') continue;
			const selector = rule[1].replace(/\/\*[\s\S]*?\*\//g, '').trim();
			if ((NOT_PROSE[file] ?? []).some((allowed) => selector.startsWith(allowed))) continue;
			own.push(`${file} ${selector} ${measure}`);
		}
	}
	expect(own).toEqual([]);
});
