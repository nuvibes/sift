/* The connect screen: the one screen in Sift with no server behind it. */

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

function button(where: HTMLElement, label: string): HTMLButtonElement | undefined {
	return [...where.querySelectorAll('button')].find((one) => one.textContent?.includes(label)) as
		HTMLButtonElement | undefined;
}

describe('in a browser', () => {
	it('explains rather than offering a form nothing could act on', () => {
		const where = render();

		expect(where.querySelector('input')).toBeNull();
		expect(where.querySelector('h1')?.textContent).toContain('Sift app');
	});

	it('says it is already running in the note the other door uses, not a paragraph of its own', () => {
		/* `/start` says this in a note with its mark, so `/connect` does too rather than in a
		   plain grey paragraph: one fact, one shape, on two screens of the same kind. */
		const where = render();

		expect(where.querySelector('p.note')?.textContent).toContain('already running');
	});
});

describe('in the desktop client', () => {
	function inTheApp(
		saveServer = vi.fn().mockResolvedValue(null),
		last: string | null = null,
		more: { problem?: string | null; servers?: { label: string; origin: string }[] } = {}
	): ReturnType<typeof vi.fn> {
		window.sift = {
			saveServer,
			lastServer: vi.fn().mockResolvedValue(last),
			connectState: vi.fn().mockResolvedValue({
				last,
				problem: more.problem ?? null,
				servers: more.servers ?? []
			}),
			forgetServer: vi.fn().mockResolvedValue([])
		};
		return saveServer;
	}

	/* The saved address stopped answering, so the shell sent the window here instead of putting
	 * up an error box with a stack trace in it. The screen says why, in the shell's words. */
	it('says why the saved address did not answer', async () => {
		inTheApp(vi.fn(), 'http://10.0.0.5:5171', {
			problem: 'Sift could not reach http://10.0.0.5:5171. Nothing answered at that address.'
		});
		const where = render();

		await vi.waitFor(() => {
			flushSync();
			expect(where.querySelector('[role="alert"]')?.textContent).toContain(
				'could not reach http://10.0.0.5:5171'
			);
		});
	});

	/* The way back from a server that has moved or gone: the list it was on, edited here. */
	it('lists the saved addresses, fills one in, and forgets one', async () => {
		inTheApp(vi.fn(), null, {
			servers: [
				{ label: 'http://10.0.0.5:5171', origin: 'http://10.0.0.5:5171' },
				{ label: 'http://10.0.0.9:5171', origin: 'http://10.0.0.9:5171' }
			]
		});
		const where = render();
		await vi.waitFor(() => {
			flushSync();
			expect(where.querySelectorAll('.saved li')).toHaveLength(2);
		});

		button(where, 'http://10.0.0.9:5171')?.click();
		flushSync();
		expect((where.querySelector('input') as HTMLInputElement).value).toBe('http://10.0.0.9:5171');

		(where.querySelector('[aria-label="Forget http://10.0.0.5:5171"]') as HTMLElement).click();
		await vi.waitFor(() => {
			flushSync();
			expect(window.sift?.forgetServer).toHaveBeenCalledWith('http://10.0.0.5:5171');
			expect(where.querySelectorAll('.saved li')).toHaveLength(0);
		});
	});

	/* Back to the mode question, which is what makes choosing Client Install undoable. */
	it('offers a way back to the mode question', async () => {
		inTheApp();
		const setupBack = vi.fn().mockResolvedValue({ ok: true, refusal: null });
		window.sift = { ...window.sift, chooseMode: vi.fn(), setupBack };
		const where = render();

		const back = [...where.querySelectorAll('button')].find((one) =>
			one.textContent?.includes('How Sift runs')
		) as HTMLButtonElement | undefined;
		back?.click();

		await vi.waitFor(() => expect(setupBack).toHaveBeenCalled());
	});

	it('and draws none in a shell that cannot answer setup questions', () => {
		inTheApp();
		const where = render();

		const back = [...where.querySelectorAll('button')].find((one) =>
			one.textContent?.includes('How Sift runs')
		);
		expect(back).toBeUndefined();
	});

	it('asks for an address', () => {
		inTheApp();
		const where = render();

		expect(where.querySelector('input')).not.toBeNull();
	});

	it('hands what was typed to the shell', async () => {
		const saveServer = inTheApp();
		const where = render();

		const box = where.querySelector('input') as HTMLInputElement;
		box.value = 'http://10.0.0.5:5171';
		box.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		button(where, 'Connect')?.click();

		await vi.waitFor(() => expect(saveServer).toHaveBeenCalledWith('http://10.0.0.5:5171'));
	});

	/* The refusal comes back as WORDS from the shell, because the shell is the side that knows: it
	 * normalised the address, tried to reach it, and saw the answer. */
	it('shows the shell own words when the address will not do', async () => {
		inTheApp(vi.fn().mockResolvedValue('Nothing answered at that address.'));
		const where = render();

		const box = where.querySelector('input') as HTMLInputElement;
		box.value = 'http://10.0.0.5:5171';
		box.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		button(where, 'Connect')?.click();

		await vi.waitFor(() =>
			expect(where.querySelector('[role="alert"]')?.textContent).toContain(
				'Nothing answered at that address.'
			)
		);
	});

	/* Somebody who mistyped a port should be correcting four characters, not typing it all again. */
	it('starts from the address that was tried last', async () => {
		inTheApp(vi.fn().mockResolvedValue(null), 'http://10.0.0.5:5171');
		const where = render();

		await vi.waitFor(() => {
			flushSync();
			expect((where.querySelector('input') as HTMLInputElement).value).toBe('http://10.0.0.5:5171');
		});
	});

	it('does nothing at all when the box is empty', async () => {
		const saveServer = inTheApp();
		const where = render();

		button(where, 'Connect')?.click();
		await Promise.resolve();

		expect(saveServer).not.toHaveBeenCalled();
	});

	/* The rules of a window onto another computer: everything that computer's app can do, acting
	   there, with the one exception Windows makes. */
	it('says a client can do everything, except approve Windows own prompt', () => {
		inTheApp();
		const where = render();

		const words = (where.textContent ?? '').replace(/\s+/g, ' ');
		expect(words).toContain('do everything the Sift app on that computer can');
		expect(words).toContain('a restart');
		expect(words).toContain(
			"The one exception is Windows' own permission prompt, which is approved on the computer running Sift."
		);
	});
});
