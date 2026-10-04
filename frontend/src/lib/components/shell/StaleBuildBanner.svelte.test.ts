import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount } from 'svelte';

import { build } from '$lib/shell/build.svelte';

import StaleBuildBanner from './StaleBuildBanner.svelte';

/*
 * The banner that says the server changed under a running window.
 *
 * Not on the design gallery: it is drawn only when a live server has moved, a condition the
 * gallery cannot produce and should not fake, so this is the only place its two states are looked
 * at. And both matter: it CANNOT be dismissed, which is the difference from the update banner beside
 * it, so a version of it that drew when nothing had changed would be a sentence nobody could get rid
 * of, on a window that was perfectly current.
 */

let host: HTMLElement;

beforeEach(() => {
	build.stale = false;
});

afterEach(() => {
	host?.remove();
	build.stale = false;
	vi.restoreAllMocks();
});

function draw(): HTMLElement {
	host = document.createElement('div');
	document.body.append(host);
	mount(StaleBuildBanner, { target: host });
	flushSync();
	return host;
}

describe('a window running a version the server no longer serves', () => {
	it('says nothing at all while the window is current', () => {
		draw();

		expect(host.textContent?.trim(), 'a current window was told to reload').toBe('');
	});

	it('says what happened and offers the one thing left to do', () => {
		build.stale = true;
		draw();

		expect(host.textContent).toContain('Sift has been updated');
		expect(host.querySelector('button')?.textContent?.trim()).toContain('Reload');
	});

	it('reloads through the store rather than touching the window itself', () => {
		/* The shell OFFERS a reload; it never takes one. A window that reloaded itself while
		   somebody was halfway through typing a filter would have traded one surprise for a worse
		   one, so the press goes through `build.reload`, which is the one place that decides. */
		const reload = vi.spyOn(build, 'reload').mockImplementation(() => {});
		build.stale = true;
		draw();

		host.querySelector('button')?.click();

		expect(reload).toHaveBeenCalledTimes(1);
	});
});
