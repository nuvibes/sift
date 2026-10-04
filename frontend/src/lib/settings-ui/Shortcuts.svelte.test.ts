/* The Settings section that lists every key the application answers to.
 *
 * The reason it is generated rather than written is in the component: a hand-typed list says the
 * numbers go "1 to 4" while the code takes 1 to 9. So what is worth pinning here is exactly that
 * generation: every declared shortcut reaches the page, under the heading its own declaration
 * names, and an area with nothing in it gets no heading at all. A test that named the areas by
 * hand would be the same hand-written list this section exists to replace, so nothing below
 * spells one: they are read from the declarations and compared against what was drawn.
 */

import { afterEach, beforeEach, expect, it } from 'vitest';
import { flushSync, mount } from 'svelte';
import Shortcuts from './Shortcuts.svelte';
import { SHORTCUTS, shortcutsByArea } from '$lib/shell/shortcuts';

let host: HTMLDivElement;

beforeEach(() => {
	host = document.createElement('div');
	document.body.append(host);
	mount(Shortcuts, { target: host });
	flushSync();
});

afterEach(() => host.remove());

function headings(): string[] {
	return [...host.querySelectorAll('h2')].map((each) => each.textContent ?? '');
}

it('heads one group per area that has a key in it, in the declared order', () => {
	const expected = shortcutsByArea().map((group) => group.area);

	expect(headings()).toEqual(expected);
	expect(expected.length).toBeGreaterThan(0);
});

it('draws a heading for each group it is given, and does no filtering of its own', () => {
	/* Which areas appear is `shortcutsByArea`'s decision, not this component's: it leaves out an
	   area with nothing in it, so there is never a heading over an empty list. That belongs to
	   `shortcuts.test.ts`, which can now genuinely exercise it: the function takes the list it groups
	   and a test hands it one with an area missing. What IS this file's to check is that the page is
	   one heading per group and nothing else. */
	expect(headings()).toHaveLength(shortcutsByArea().length);
	expect(host.querySelectorAll('dl')).toHaveLength(shortcutsByArea().length);
});

it('draws every declared shortcut exactly once, key and sentence together', () => {
	// The key's own words: the shared column's unseen sizer in the first cell is not a key.
	const keys = [...host.querySelectorAll('dt')].map((each) => {
		const copy = each.cloneNode(true) as Element;
		copy.querySelector('.sizer')?.remove();
		return copy.textContent?.trim();
	});
	const sentences = [...host.querySelectorAll('dd')].map((each) => each.textContent);

	expect(keys).toHaveLength(SHORTCUTS.length);
	for (const one of SHORTCUTS) {
		expect(keys).toContain(one.shown);
		expect(sentences).toContain(one.does);
	}
});

it('says what the section is and that a key only answers where it belongs', () => {
	/* No title of its own: the frame draws every section's title from its declaration, so a pane
	   that wrote one would be the second copy. The search result for the keys lands on the lede. */
	expect(host.querySelector('h1')).toBeNull();
	expect(host.querySelector('[id="shortcuts.keys"]')?.textContent).toContain(
		'only answers on the screen'
	);
});
