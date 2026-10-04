import { afterEach, describe, expect, it } from 'vitest';
import { mount } from 'svelte';

import Placeholder from './Placeholder.svelte';

/*
 * The screen a route resolves to before the screen behind it exists.
 *
 * Worth a test for the reason the component's own note gives: every route in the app resolves from
 * the day the shell exists, even where nothing is built there yet, because a route that does not
 * resolve is a route somebody invents a second time, in a different shape, and then two of them have
 * to be reconciled. So what it says has to be a heading somebody can land on and a sentence saying
 * there is nothing here YET, rather than a blank page that reads as a broken link.
 */

let host: HTMLElement;

afterEach(() => {
	host?.remove();
});

function draw(title: string): HTMLElement {
	host = document.createElement('div');
	document.body.append(host);
	mount(Placeholder, { target: host, props: { title } });
	return host;
}

describe('a route with nothing behind it yet', () => {
	it('lands on a heading that says which route it is', () => {
		draw('Loops');

		const heading = host.querySelector('h1');
		expect(heading?.textContent).toBe('Loops');
	});

	it('says there is nothing here YET, rather than nothing at all', () => {
		draw('Loops');

		// "Nothing here yet" and not "Not found": the difference is whether somebody thinks they
		// followed a broken link or arrived somewhere unbuilt.
		expect(host.textContent).toContain('Nothing here yet.');
	});
});
