/* The second question: where does Sift keep its own two folders?
 *
 * The sentence about the OTHER files is what is really being guarded here. Somebody being asked
 * where Sift will "keep your library" reasonably fears their videos are about to be moved (they
 * are not, ever), and that sentence is the difference between finishing setup and cancelling it.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import Page from './+page.svelte';

const SUGGESTED = 'C:\\Users\\someone\\AppData\\Local\\Sift\\data';
const OFFERED = { path: SUGGESTED, existing: false };

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

afterEach(() => {
	if (mounted) void unmount(mounted);
	mounted = null;
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

function button(where: HTMLElement, label: string): HTMLButtonElement | undefined {
	return [...where.querySelectorAll('button')].find((one) => one.textContent?.includes(label)) as
		HTMLButtonElement | undefined;
}

function inTheApp(chooseLibrary = vi.fn().mockResolvedValue({ ok: true, refusal: null })) {
	window.sift = {
		chooseMode: vi.fn(),
		suggestedLibrary: vi.fn().mockResolvedValue(OFFERED),
		chooseLibrary,
		setupBack: vi.fn().mockResolvedValue({ ok: true, refusal: null })
	};
	return chooseLibrary;
}

describe('in a browser', () => {
	it('explains rather than offering buttons nothing could act on', () => {
		const where = render();

		expect(button(where, 'Choose this folder')).toBeUndefined();
		expect(where.querySelector('h1')?.textContent).toContain('Sift app');
	});
});

describe('in the desktop app', () => {
	it('names the folder it is proposing, so the answer is informed', async () => {
		inTheApp();
		const where = render();

		await vi.waitFor(() => expect(where.textContent).toContain(SUGGESTED));
	});

	/* The one line that stops somebody cancelling first run out of fear. */
	it('and says plainly that the media itself is never touched', async () => {
		inTheApp();
		const where = render();

		await vi.waitFor(() =>
			expect(where.textContent).toContain("doesn't affect your existing photos or videos")
		);
	});

	it('takes the suggested folder without opening a dialog', async () => {
		const chooseLibrary = inTheApp();
		const where = render();
		await vi.waitFor(() => expect(where.textContent).toContain(SUGGESTED));

		button(where, 'Choose this folder')?.click();

		await vi.waitFor(() => expect(chooseLibrary).toHaveBeenCalledWith(false));
	});

	it('and asks the shell to open the machine own dialog for a different one', async () => {
		const chooseLibrary = inTheApp();
		const where = render();

		button(where, 'Choose a different folder')?.click();

		await vi.waitFor(() => expect(chooseLibrary).toHaveBeenCalledWith(true));
	});

	/* Nothing to say and nothing to do: somebody closed the picker, and the screen stays exactly
	 * where it was, still offering the suggestion. A message here would report a failure that did
	 * not happen. */
	it('says nothing when the picker was simply closed', async () => {
		inTheApp(vi.fn().mockResolvedValue({ ok: false, refusal: null }));
		const where = render();
		await vi.waitFor(() => expect(where.textContent).toContain(SUGGESTED));

		button(where, 'Choose a different folder')?.click();
		await vi.waitFor(() => expect(button(where, 'Choose this folder')?.disabled).toBe(false));

		expect(where.querySelector('[role="alert"]')).toBeNull();
	});

	it('and shows the shell own words when the folder cannot be written to', async () => {
		inTheApp(vi.fn().mockResolvedValue({ ok: false, refusal: 'Sift cannot write to D:\\x.' }));
		const where = render();
		await vi.waitFor(() => expect(where.textContent).toContain(SUGGESTED));

		button(where, 'Choose this folder')?.click();

		await vi.waitFor(() => expect(where.textContent).toContain('Sift cannot write to D:\\x.'));
	});

	/* A library an earlier installation left is offered as one to carry on with, in those words,
	   and the button says so too: the answer is "keep it", not "use this folder". */
	it('offers a library from before as one to keep', async () => {
		inTheApp();
		window.sift!.suggestedLibrary = vi.fn().mockResolvedValue({ path: SUGGESTED, existing: true });
		const where = render();

		await vi.waitFor(() => expect(where.textContent).toContain('already holds a Sift library'));
		expect(where.textContent).toContain('(Your existing library)');
		expect(button(where, 'Keep this library')).toBeDefined();
		expect(button(where, 'Choose this folder')).toBeUndefined();
	});

	/* Nothing may be settled before the suggestion has arrived, or the press would send an answer
	   about a folder nobody has been shown. */
	it('and cannot be answered before the suggested folder has arrived', () => {
		window.sift = {
			chooseMode: vi.fn(),
			suggestedLibrary: vi.fn(() => new Promise<typeof OFFERED>(() => {})),
			chooseLibrary: vi.fn()
		};
		const where = render();

		expect(button(where, 'Choose this folder')?.disabled).toBe(true);
	});
});

/*
 * Going back to the mode question.
 *
 * Without a way back this screen is a dead end: somebody who chose Server Install by mistake could
 * only answer it or close the window, and closing the window on the second screen of setup leaves
 * an application that opens on the same screen for ever.
 *
 * It goes back through the SHELL rather than through browser history, and that is the property
 * worth holding: the answer that led here is still saved, so a history step would draw the mode
 * question with the mode already chosen and pressing it again would change nothing.
 */
describe('going back', () => {
	it('asks the shell to unsay the answer that led here', async () => {
		inTheApp();
		const where = render();
		await vi.waitFor(() => expect(where.textContent).toContain(SUGGESTED));

		button(where, 'How Sift runs')?.click();

		await vi.waitFor(() => expect(window.sift?.setupBack).toHaveBeenCalled());
	});

	/* The shell is the side that knows why, exactly as with the two answers. A refusal that went
	 * nowhere would leave somebody pressing a button that does nothing and says nothing. */
	it('shows the shell own words when it will not go back', async () => {
		inTheApp();
		window.sift!.setupBack = vi
			.fn()
			.mockResolvedValue({ ok: false, refusal: 'That could not be undone.' });
		const where = render();
		await vi.waitFor(() => expect(where.textContent).toContain(SUGGESTED));

		button(where, 'How Sift runs')?.click();

		await vi.waitFor(() => expect(where.textContent).toContain('That could not be undone.'));
	});
});

/*
 * The wait after a press.
 *
 * Taking a folder starts Sift there, and the first start creates the database, so the answer can
 * be many seconds away. A page that only greys its buttons for that long looks stuck, and somebody
 * cancels setup, so the press is answered immediately: the pressed button turns and a sentence says
 * what is being waited on.
 */
describe('while a press is being answered', () => {
	it('says it is still looking before the suggested folder has arrived', () => {
		window.sift = {
			chooseMode: vi.fn(),
			suggestedLibrary: vi.fn(() => new Promise<typeof OFFERED>(() => {})),
			chooseLibrary: vi.fn()
		};
		const where = render();

		expect(where.textContent).toContain('Checking for a library from before');
	});

	it('turns the pressed button and says what is being checked', async () => {
		inTheApp(vi.fn(() => new Promise<never>(() => {})));
		const where = render();
		await vi.waitFor(() => expect(where.textContent).toContain(SUGGESTED));

		button(where, 'Choose this folder')?.click();
		flushSync();

		expect(button(where, 'Choose this folder')?.getAttribute('aria-busy')).toBe('true');
		expect(button(where, 'Choose a different folder')?.disabled).toBe(true);
		expect(where.querySelector('[role="status"]')?.textContent).toContain('Checking the folder.');
	});

	it('says each step the app reports, in turn', async () => {
		inTheApp(vi.fn(() => new Promise<never>(() => {})));
		const shell: { tell: ((step: 'checking' | 'starting') => void) | null } = { tell: null };
		window.sift!.onSetupProgress = (listen) => {
			shell.tell = listen;
			return () => (shell.tell = null);
		};
		const where = render();
		await vi.waitFor(() => expect(where.textContent).toContain(SUGGESTED));

		button(where, 'Choose a different folder')?.click();
		flushSync();
		const status = () => where.querySelector('[role="status"]')?.textContent ?? '';
		expect(status()).toContain('window that opened');

		shell.tell?.('checking');
		flushSync();
		expect(status()).toContain('Checking the folder.');
		shell.tell?.('starting');
		flushSync();
		expect(status()).toContain('Starting Sift in that folder');
	});

	it('asks for a folder in the window that opened when a different one was asked for', async () => {
		inTheApp(vi.fn(() => new Promise<never>(() => {})));
		const where = render();
		await vi.waitFor(() => expect(where.textContent).toContain(SUGGESTED));

		button(where, 'Choose a different folder')?.click();
		flushSync();

		expect(button(where, 'Choose a different folder')?.getAttribute('aria-busy')).toBe('true');
		expect(button(where, 'Choose this folder')?.getAttribute('aria-busy')).toBeNull();
		expect(where.querySelector('[role="status"]')?.textContent).toContain('window that opened');
	});

	it('goes quiet again when the press comes back without an answer', async () => {
		inTheApp(vi.fn().mockResolvedValue({ ok: false, refusal: null }));
		const where = render();
		await vi.waitFor(() => expect(where.textContent).toContain(SUGGESTED));

		button(where, 'Choose this folder')?.click();
		flushSync();
		expect(where.querySelector('[role="status"]')).not.toBeNull();

		await vi.waitFor(() => expect(button(where, 'Choose this folder')?.disabled).toBe(false));
		expect(where.querySelector('[role="status"]')).toBeNull();
	});
});
