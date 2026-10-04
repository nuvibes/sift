/*
 * The box everything else stands in, and the one question its tone answers.
 *
 * `caution` has to be distinguishable: a tone drawing the same ground and edge as the ordinary
 * panel would do nothing, and the dialogs using it would lose their caution box.
 */

import { afterEach, describe, expect, it } from 'vitest';
import { mount, unmount } from 'svelte';

import PanelProbe from './PanelProbe.test.svelte';
import source from './Panel.svelte?raw';

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

function render(props: Record<string, unknown> = {}): HTMLElement {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(PanelProbe, { target: host, props });
	return host;
}

/** A panel's own classes, without the compiler's scoping hash. */
function tones(where: HTMLElement): string[] {
	const panel = where.querySelector('.panel');
	expect(panel).not.toBeNull();
	return [...(panel?.classList ?? [])].filter((name) => !name.startsWith('svelte-'));
}

afterEach(() => {
	if (mounted) void unmount(mounted);
	mounted = null;
	host?.remove();
});

describe('the box', () => {
	it('is drawn, with its contents', () => {
		expect(render({ words: 'Nothing leaves this machine.' }).textContent).toContain(
			'Nothing leaves this machine.'
		);
	});

	it('stands raised unless it is told otherwise', () => {
		expect(tones(render())).toContain('raised');
	});
});

describe('the caution tone', () => {
	/* The whole point of it: the box itself says "read this twice", so a caution must not come out
	   wearing the ordinary panel's ground. */
	it('is its own ground, not the raised one', () => {
		const own = tones(render({ tone: 'caution' }));

		expect(own).toContain('caution');
		expect(own).not.toContain('raised');
		expect(own).not.toContain('recessed');
	});

	/* And it keeps everything else a panel decides. A tone that quietly took the corner or the inset
	   with it would be a second panel rather than a third ground, which is exactly what this was
	   built instead of. */
	it('and keeps the corner and the inset every other panel has', () => {
		const caution = tones(render({ tone: 'caution' }));

		expect(caution).toContain('corner-lg');
		expect(caution).toContain('inset-md');
	});

	/* The edge carries the colour as well as the ground, so a caution asked for no edge must still
	   lose it: the two are answered by different props and `edgeless` is the later rule. */
	it('and an edgeless caution still has no edge', () => {
		const own = tones(render({ tone: 'caution', edge: false }));

		expect(own).toContain('caution');
		expect(own).toContain('edgeless');
	});
});

describe("the card's light", () => {
	/* A panel standing on the page wears the light; one floating over the screen keeps the flat
	   tone, because a floating thing with a light of its own reads as a second lamp. The rule keys
	   on `floating`, so the class is what has to be right. */
	it('is worn by a raised panel standing on the page', () => {
		expect(tones(render())).not.toContain('floating');
	});

	it('is not worn by a panel floating over the screen, or one lifted off it', () => {
		expect(tones(render({ floating: true }))).toContain('floating');
		expect(tones(render({ elevated: true }))).toContain('floating');
	});

	it('is the ground of the standing form, and the floating form keeps the flat tone', () => {
		const style = /<style>([\s\S]*)<\/style>/.exec(source)?.[1] ?? '';
		expect(style).toMatch(/\.raised \{\s*background: var\(--sift-surface-2\);/);
		expect(style).toMatch(
			/\.raised:not\(\.floating\) \{\s*border-color: transparent;\s*background: var\(--sift-card\);/
		);
		expect(style).toMatch(
			/\.raised\.edgeless:not\(\.floating\) \{\s*background: var\(--sift-card-fill\);/
		);
	});
});
