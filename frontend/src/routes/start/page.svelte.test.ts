/* The first question: does this computer hold the library, or look at one somebody else holds?
 *
 * What these tests are about: the two answers reach the shell, the words are the ones the person
 * asked for, and a browser (which cannot answer at all) says so rather than drawing two cards
 * that do nothing.
 */

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

/*
 * A card BY ITS NAME, not by anything its sentence happens to mention.
 *
 * A card's own note can name the OTHER card, so matching the whole card's text can match both, and
 * only the order in the markup would decide which came back. `ChoiceCard` puts the name in a
 * `.name` of its own, with the aside after it, so the name is a thing that can be asked for
 * exactly.
 */
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

	/* Running the library here is what almost everybody wants, and the card says so. Asserted
	   because a first screen that steers somebody into client mode by accident is a first screen
	   that leaves them with an application pointed at nothing. */
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

	/* Pressing twice must not send two answers: the second would arrive while the shell is already
	   loading the next screen, and the mode it names could be the other one. */
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
