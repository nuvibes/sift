/*
 * Theater on a phone says it needs a wider window and puts nothing of the wall's on the bar.
 *
 * A wall is several pictures playing at once on one screen, and a phone's own full screen holds
 * one video, so a phone drives the desk's walls from the Remote instead. The refusal is the one
 * every screen too wide for a phone draws (`WIDER_WINDOW_TITLE`), with its own reason under it.
 *
 * Read from the source: the page needs the whole wall to mount, and what is under test is which
 * branch a phone's width takes and what the bar is handed there.
 */
import { expect, it } from 'vitest';

import source from './+page.svelte?raw';

const code = source.replace(/<!--[\s\S]*?-->/g, '').replace(/\/\*[\s\S]*?\*\//g, '');

it('hands the bar nothing on a phone, in the same effect and before the desk tools', () => {
	const guard = /if \(!roomy\) \{\s*screenBar\.publish\(mine, \{\}\);\s*return;\s*\}/.exec(code);
	const desk = code.indexOf('topExtra: tools');
	expect(guard, "a phone's bar is handed the wall's controls").not.toBeNull();
	expect(desk).toBeGreaterThan(-1);
	expect(guard!.index).toBeLessThan(desk);
	expect(code.slice(guard!.index, desk)).not.toContain('$effect(');
});

it('draws the refusal at a phone width, and no wall', () => {
	expect(
		/\{#if !roomy\}\s*<Empty scope="page" title=\{WIDER_WINDOW_TITLE\}>\{PHONE_REFUSAL\}<\/Empty>/.exec(
			code
		),
		'the refusal'
	).not.toBeNull();
	expect(code).toContain('const roomy = $derived(!phoneWidth.yes)');
	expect(code).toContain(
		"Several videos side by side don't fit on a screen this size. Open it on a computer or a tablet."
	);
	expect(code).not.toContain('TheaterPhone');
	expect(code).not.toContain('takeWall');
});
