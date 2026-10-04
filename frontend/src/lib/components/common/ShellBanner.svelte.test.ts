/* The quiet line across the top: the shape four notices share.
 *
 * One shape, because the notices are the same kind of object, and copies of a shape in each one
 * drift apart one padding value at a time.
 */

import { afterEach, describe, expect, it } from 'vitest';
import { mount, unmount } from 'svelte';

import ShellBanner from './ShellBanner.svelte';
import ShellBannerProbe from './ShellBannerProbe.test.svelte';

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

function render(props: Record<string, unknown> = {}): HTMLElement {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(ShellBannerProbe, { target: host, props });
	return host;
}

afterEach(() => {
	if (mounted) void unmount(mounted);
	mounted = null;
	host?.remove();
});

describe('what it announces', () => {
	it('is a status, never an alert', () => {
		/* None of these interrupts anything. An assertive announcement over whatever somebody was
		   reading is what a banner about a version number should never do. */
		const where = render();

		expect(where.querySelector('[role="status"]')).toBeTruthy();
		expect(where.querySelector('[role="alert"]')).toBeNull();
	});
});

describe('what it draws', () => {
	it('carries the sentence it was handed', () => {
		const where = render();

		expect(where.textContent).toContain('Something worth knowing.');
	});

	it('carries the action beside it when there is one', () => {
		const where = render({ withAction: true });

		expect(where.querySelector('button')?.textContent).toContain('Do it');
	});

	it('and draws no action area when there is none', () => {
		const where = render();

		expect(where.querySelector('button')).toBeNull();
	});

	it('can be found by name when a test has to tell it from the others', () => {
		const where = render({ testId: 'a-banner' });

		expect(where.querySelector('[data-testid="a-banner"]')).toBeTruthy();
	});
});

describe('the component itself', () => {
	it('is what the harness draws', () => {
		// A guard on the harness rather than a claim about it: a harness that stopped importing the
		// component would go on passing every assertion above against its own markup.
		expect(ShellBannerProbe.toString()).not.toBe(ShellBanner.toString());
		expect(typeof ShellBanner).toBe('function');
	});
});
