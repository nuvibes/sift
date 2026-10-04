/*
 * Swap mode's drawer on a phone: it rises from the foot, half the screen, and leaves the wall its
 * whole width to pick from.
 *
 * At a phone's width, beside the page at the right it would take nearly the whole width and leave
 * the wall too narrow for a file to be pressed. Read from the source, because what is under
 * test is which side the drawer is handed and the height its box sets, neither of which a unit
 * environment lays out.
 */
import { expect, it } from 'vitest';

import source from './SwapDrawer.svelte?raw';

const code = source.replace(/<!--[\s\S]*?-->/g, '').replace(/\/\*[\s\S]*?\*\//g, '');

it('rises from the foot at a phone width and stands at the right on a wider window', () => {
	expect(code).toMatch(/<Drawer[^>]*\bbeside\b[^>]*side=\{phoneWidth\.yes \? 'bottom' : 'right'\}/);
});

it('is half the screen from the foot, so the wall above it can still be picked from', () => {
	const phone = code.slice(code.indexOf('@media (max-width: 767px)'));
	expect(phone).toMatch(/^[^}]*\.swap-drawer \{\s*--drawer-height: 50%;/);
});

/*
 * Each group (the picks of one kind, and starting the swap) is a section of its own in a column with
 * a gap between them. A heading owns only the space under it, so laid out loose in the drawer each
 * heading would stand on the chips above it with no room between.
 */
it('stands each group apart from the one above it', () => {
	// The heading says the exchange under Exchange, the swap otherwise; either way it heads a section.
	expect(code).toMatch(
		/<div class="groups">[\s\S]*<section>\s*<SectionHeading\s*>\{direction === 'both' \? 'Start the exchange' : 'Start the swap'\}/
	);
	expect(code).toMatch(/\{#each groups as group \(group\.kind\)\}\s*<section>/);
	expect(code).toMatch(
		/\.groups \{\s*display: flex;\s*flex-direction: column;\s*gap: var\(--space-6\);/
	);
});
