/*
 * The Settings panel's way out stands in a corner box the panel owns.
 *
 * Positioned on the button itself, it would be placed against the tooltip's wrapper (an inline box
 * with a position of its own and no width, at the panel's start), so it would sit outside the
 * panel's left edge, clipped: no visible close on any window, which on a phone is a pane that
 * cannot be left.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';

const source = readFileSync('src/lib/components/SettingsModal.svelte', 'utf8');
const style = source.slice(source.indexOf('<style>'));

describe('the close corner', () => {
	it('is a box of the panel, holding the tooltip and its button', () => {
		const markup = source.slice(source.lastIndexOf('</script>'), source.indexOf('<style>'));
		expect(markup).toMatch(
			/<div class="close-slot">\s*<Tooltip[\s\S]*?class="close"[\s\S]*?<\/Tooltip>\s*<\/div>/
		);
	});

	it('is what is positioned, never the button inside the tooltip', () => {
		expect(style).toMatch(/\.close-slot \{\s*position: absolute;/);
		expect(style).not.toMatch(/:global\(\.close\) \{[^}]*position:/);
	});

	it("is square: the shared button's box, never a circle", () => {
		const close = style.match(/\.close-slot :global\(\.close\) \{[^}]*\}/)?.[0] ?? '';
		expect(close).toContain('border:');
		expect(close).not.toMatch(/border-radius/);
		/* Its corner is the card's less the slot's inset, so the two curves are concentric. */
		expect(style).toMatch(
			/\.close-slot \{[^}]*--radius-md: calc\(var\(--radius-xl\) - var\(--space-4\)\)/
		);
		const markup = source.slice(source.lastIndexOf('</script>'), source.indexOf('<style>'));
		expect(markup).not.toMatch(/shape="circle"/);
	});
});
