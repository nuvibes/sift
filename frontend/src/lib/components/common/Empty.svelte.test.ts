/*
 * The sentence is a sentence, and a link inside it is part of it.
 *
 * `.said` must not be a flex container: that would blockify every child and split a mid-sentence
 * link (as on `/organize/tagger`) and the words around it into separate boxes, with a stranded full
 * stop.
 *
 * jsdom applies none of a component's CSS, so the rule is read rather than measured, and the DOM
 * half is asserted beside it, because the rule matters only while the markup is one paragraph of
 * inline content.
 */
import { readFileSync } from 'node:fs';
import { createRawSnippet, flushSync, mount, unmount } from 'svelte';
import { afterEach, describe, expect, it } from 'vitest';

import Empty from './Empty.svelte';
import source from './Empty.svelte?raw';
import { words } from '$lib/design/testing.svelte';
import { applyStyles, removeStyles } from '$lib/design/testing-styles';

let host: HTMLElement;
let mounted: ReturnType<typeof mount> | null = null;

afterEach(() => {
	if (mounted) unmount(mounted);
	mounted = null;
	host?.remove();
	removeStyles();
});

/** A sentence with a link in the middle of it, which is the shape five call sites have. */
const SENTENCE = createRawSnippet(() => ({
	render: () =>
		'<span>Nothing is waiting. Switch matching on in <a href="/settings">Settings</a>.</span>'
}));

function draw(props: Record<string, unknown> = {}) {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(Empty, { target: host, props: { children: SENTENCE, ...props } });
	flushSync();
	return host.querySelector('.said') as HTMLElement;
}

describe('a link in the middle of the sentence', () => {
	it('is inside the paragraph, with the words on both sides of it', () => {
		const said = draw();

		expect(said.querySelector('a')).not.toBeNull();
		expect(words(said)).toBe('Nothing is waiting. Switch matching on in Settings.');
	});

	it('is the same one sentence in the quiet form', () => {
		const said = draw({ quiet: true });

		expect(words(said)).toBe('Nothing is waiting. Switch matching on in Settings.');
	});
});

describe('the rule that lays it out', () => {
	const rule = (() => {
		const source = readFileSync('src/lib/components/common/Empty.svelte', 'utf8');
		const found = source.match(/\n\t\.said \{([^}]*)\}/);
		if (!found) throw new Error('there is no .said rule in Empty.svelte');
		return found[1];
	})();

	it('does not blockify what is inside the sentence', () => {
		// Flex and grid both do it, and either would put the link back on a line of its own. The
		// layout that wants a column is the box AROUND the words, which is `.empty`.
		expect(rule).not.toMatch(/display:\s*(inline-)?(flex|grid)/);
	});

	it('leaves the spinner its room without a container to do it', () => {
		// A `Spinner` is an inline-block sitting on the text's own baseline, so a margin is the
		// whole of what the row needs.
		const source = readFileSync('src/lib/components/common/Empty.svelte', 'utf8');
		expect(source).toMatch(/\.said :global\(\.spinner\) \{\s*margin-inline-end/);
	});
});

describe('the scope says what the empty thing is', () => {
	/** The whole state, drawn with its own stylesheet in the document. */
	function drawn(props: Record<string, unknown>) {
		draw({ title: 'No results for this search', ...props });
		const box = host.querySelector('.empty') as HTMLElement;
		applyStyles(source, box);
		return {
			box,
			glyph: box.querySelector('.glyph'),
			title: box.querySelector('.title'),
			align: getComputedStyle(box).textAlign
		};
	}

	it('draws a page as the glyph, the display line and the sentence, centred', () => {
		const page = drawn({ scope: 'page' });

		expect(page.glyph).not.toBeNull();
		expect(page.title?.textContent).toBe('No results for this search');
		expect(page.align).toBe('center');
	});

	it('draws a block as the sentence alone, where it stands', () => {
		const block = drawn({ scope: 'block' });

		expect(block.glyph).toBeNull();
		expect(block.title).toBeNull();
		expect(block.align).toBe('start');
	});

	it('reads the older quiet flag as a block, and nothing said as a page', () => {
		expect(drawn({ quiet: true }).align).toBe('start');
		removeStyles();
		unmount(mounted!);
		mounted = null;
		host.remove();
		expect(drawn({}).glyph).not.toBeNull();
	});

	it('sets the page line in the display face', () => {
		const rule = source.match(/\n\t\.title \{([^}]*)\}/)?.[1] ?? '';
		expect(rule).toMatch(/font:\s*var\(--text-display\);/);
	});
});
