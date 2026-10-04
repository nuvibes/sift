import { describe, expect, it } from 'vitest';

import source from './StageNotice.svelte?raw';
import button from '$lib/components/common/Button.svelte?raw';

describe('StageNotice', () => {
	/*
	 * On a phone every press on the viewer reaches 44, and so must the mark in the picture's corner.
	 * Its box is the small control's height (32 on every width, since a small press inside something
	 * keeps its size on a phone), and the reach comes from the ring `Button` draws round a SMALL
	 * button at a phone's width. So the mark has to stay a small Button: a bare element, or another size,
	 * would draw no ring.
	 */
	it("sizes the mark from the small control's height and reaches a finger through the small button's ring", () => {
		const rule = source.slice(source.indexOf('.notice :global(.mark) {'));
		expect(rule).toMatch(/^[^}]*inline-size: var\(--control-height-sm\);/);
		expect(rule).toMatch(/^[^}]*block-size: var\(--control-height-sm\);/);
		expect(source).toMatch(/<Button[^>]*size="small"[^>]*class="mark"/);
		const phone = button.slice(button.indexOf('@media (max-width: 767px)'));
		expect(phone).toMatch(/\.btn\.small::after/);
	});
});
