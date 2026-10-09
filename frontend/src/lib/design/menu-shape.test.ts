// SPDX-License-Identifier: AGPL-3.0-or-later
/* Two rules of the menu's shape that no rendered test can see. */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

describe('the menu row', () => {
	it('is concentric with its menu: the row radius is the menu radius minus the inset', () => {
		const css = readFileSync(resolve('src/app.css'), 'utf8');
		expect(css).toMatch(/--menu-row-radius:\s*calc\(var\(--radius-lg\) - var\(--space-1\)\);/);
	});

	it("lets a chooser row's words grow so they sit at the left, beside the glyph", () => {
		const select = readFileSync(resolve('src/lib/components/common/Select.svelte'), 'utf8');
		const rule = select.match(/\.ui-select-item-text \{[^}]*\}/)?.[0] ?? '';
		expect(rule).toMatch(/flex:\s*1/);
	});
});

/* A chooser of menu rows that opens from a control stands in the MENU's box, not a plain
 * panel's. */
describe('the menu shape of a popover', () => {
	const read = (path: string) => readFileSync(resolve(path), 'utf8');

	it('takes the menu corner and the menu inset, the pair the row radius is computed from', () => {
		const popover = read('src/lib/components/common/Popover.svelte');
		expect(popover).toContain("inset={shape === 'menu' ? 'xs' : inset}");
		expect(popover).toContain("corner={shape === 'menu' ? 'lg' : 'md'}");

		const panel = read('src/lib/components/common/Panel.svelte');
		expect(panel).toMatch(/\.inset-xs \{\s*padding: var\(--space-1\);\s*\}/);
		expect(panel).toMatch(/\.corner-lg \{\s*border-radius: var\(--radius-lg\);\s*\}/);
	});

	it('is what the rating chooser opens in', () => {
		const chip = read('src/lib/components/common/RatingChip.svelte');
		const popover = chip.match(/<Popover\b[\s\S]*?>/)?.[0] ?? '';
		expect(popover, 'RatingChip no longer opens a Popover').not.toBe('');
		expect(popover).toContain('shape="menu"');
		expect(popover).not.toMatch(/\binset=/);
	});
});
