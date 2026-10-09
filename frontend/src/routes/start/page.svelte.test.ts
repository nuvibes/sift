/* The first question: does this computer hold the library, or look at one somebody else holds? */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import Page from './+page.svelte';

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

afterEach(() => {
	if (mounted) void unmount(mounted);
	mounted = null;
	/* Removed as well as unmounted: a host left in the document is a screen the NEXT test's query
	 * finds, and then one test presses the last test's button. */
	host?.remove();
	delete window.sift;
});

function render(): HTMLElement {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(Page, { target: host });
	flushSync();
	return host;
}

/* A card BY ITS NAME, not by anything its sentence happens to mention. */
function card(where: HTMLElement, label: string): HTMLButtonElement | undefined {
	return [...where.querySelectorAll('button')].find((one) =>
		one.querySelector('.name')?.textContent?.trim().startsWith(label)
	) as HTMLButtonElement | undefined;
}

function inTheApp(chooseMode = vi.fn().mockResolvedValue({ ok: true, refusal: null })) {
	window.sift = { chooseMode };
	return chooseMode;
}

describe('in a browser', () => {
	it('explains rather than offering choices nothing could act on', () => {
		const where = render();

		expect(card(where, 'This device')).toBeUndefined();
		expect(where.querySelector('h1')?.textContent).toContain('Sift app');
	});
});

describe('in the desktop app', () => {
	it('offers the two ways to run, by the names they are called', () => {
		inTheApp();
		const where = render();

		expect(card(where, 'This device')).toBeDefined();
		expect(card(where, 'Another device')).toBeDefined();
	});

	/* Not decoration. A name on its own tells somebody which words to use; it does not tell them
	   which one they want, and that is the whole reason this screen exists rather than a switch. */
	it('and says what each one means rather than only naming it', () => {
		inTheApp();
		const where = render();

		expect(card(where, 'This device')?.textContent).toContain('Keeps your library on this device');
		expect(card(where, 'Another device')?.textContent).toContain('runs on a different device');
	});

	it('tells the shell which was chosen', async () => {
		const chooseMode = inTheApp();
		const where = render();

		card(where, 'Another device')?.click();

		await vi.waitFor(() => expect(chooseMode).toHaveBeenCalledWith('client'));
	});

	/* Running the library here is what almost everybody wants, and the card says so. */
	it('and marks running the library here as the one most people want', () => {
		inTheApp();
		const where = render();

		expect(card(where, 'This device')?.textContent).toContain('most common');
	});

	it('shows the shell own words when an answer will not take', async () => {
		const chooseMode = inTheApp(
			vi.fn().mockResolvedValue({ ok: false, refusal: 'That could not be saved.' })
		);
		const where = render();

		card(where, 'This device')?.click();

		await vi.waitFor(() => expect(where.textContent).toContain('That could not be saved.'));
		expect(chooseMode).toHaveBeenCalled();
	});

	/* Pressing twice must not send two answers: the second would arrive while the shell is
	   already loading the next screen, and the mode it names could be the other one. */
	it('and a second press while the first is in flight is ignored', async () => {
		let settle: (value: { ok: boolean; refusal: string | null }) => void = () => {};
		const chooseMode = vi.fn(
			() => new Promise<{ ok: boolean; refusal: string | null }>((done) => (settle = done))
		);
		window.sift = { chooseMode };
		const where = render();

		card(where, 'This device')?.click();
		flushSync();
		card(where, 'Another device')?.click();

		expect(chooseMode).toHaveBeenCalledTimes(1);
		settle({ ok: true, refusal: null });
	});
});
