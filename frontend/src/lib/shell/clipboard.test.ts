/* Reading the clipboard, and above all WHERE reading it is possible at all.
 *
 * Ctrl-V is a separate question and always works: a paste event carries its own data, needs no
 * permission and no secure context. This file is about the BUTTON, which has to ask, and asking
 * is exactly what a browser on a plain-http LAN is not allowed to do. That is how most people reach
 * a self-hosted Sift, so the button being absent there is the correct behaviour rather than a gap.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { canReadClipboard, copyText, readClipboard } from './clipboard';

afterEach(() => {
	delete window.sift;
	vi.restoreAllMocks();
	vi.unstubAllGlobals();
});

function pretendSecureBrowser(items: unknown[]) {
	vi.stubGlobal('isSecureContext', true);
	vi.stubGlobal('navigator', { clipboard: { read: vi.fn(async () => items) } });
}

describe('canReadClipboard', () => {
	it('is true in the desktop client, whatever the connection is', () => {
		vi.stubGlobal('isSecureContext', false);
		window.sift = { readClipboard: async () => ({ text: '', image: null }) };

		expect(canReadClipboard()).toBe(true);
	});

	it('is true over HTTPS in a browser', () => {
		pretendSecureBrowser([]);
		expect(canReadClipboard()).toBe(true);
	});

	/* The case the capability gate exists for: a self-hosted Sift on a home network. */
	it('is false over plain http in a browser', () => {
		vi.stubGlobal('isSecureContext', false);
		vi.stubGlobal('navigator', {});

		expect(canReadClipboard()).toBe(false);
	});
});

describe('readClipboard', () => {
	it('hands back a link the desktop client read from the operating system', async () => {
		window.sift = { readClipboard: async () => ({ text: 'https://example.com/a', image: null }) };

		expect(await readClipboard()).toEqual({ text: 'https://example.com/a', file: null });
	});

	/* A raw bitmap is the ONE thing arriving with no name to keep, so it is the one thing that gets
	 * an invented one: a timestamp, so two of them sort in the order they were taken. */
	it('names a pasted picture by when it was pasted', async () => {
		window.sift = {
			readClipboard: async () => ({ text: '', image: 'data:image/png;base64,AAAA' })
		};
		vi.stubGlobal(
			'fetch',
			vi.fn(async () => ({
				blob: async () => new Blob([new Uint8Array([1])], { type: 'image/png' })
			}))
		);

		const { file } = await readClipboard();

		expect(file?.name).toMatch(/^Pasted \d{4}-\d{2}-\d{2} \d{2}-\d{2}-\d{2}\.png$/);
		expect(file?.type).toBe('image/png');
	});

	it('answers nothing at all in a plain-http browser rather than throwing', async () => {
		vi.stubGlobal('isSecureContext', false);
		vi.stubGlobal('navigator', {});

		expect(await readClipboard()).toEqual({ text: '', file: null });
	});

	/* Present but refused: a permission somebody declined, or a document without focus. The caller
	 * says "nothing to add", which is what it looks like from the outside anyway. */
	it('answers nothing when the browser refuses the read', async () => {
		vi.stubGlobal('isSecureContext', true);
		vi.stubGlobal('navigator', {
			clipboard: {
				read: vi.fn(async () => {
					throw new Error('NotAllowedError');
				})
			}
		});

		expect(await readClipboard()).toEqual({ text: '', file: null });
	});

	/* A picture on the clipboard reaches the desktop client as a data URL, and it has to be
	 * DECODED, not fetched: Sift's own Content-Security-Policy sets `default-src 'self'`,
	 * `connect-src` inherits it, and a data URL is not `'self'`. So a fetch of it is refused
	 * before it starts, and a pasted screenshot would read as "there is nothing on the clipboard
	 * Sift can add".
	 *
	 * `fetch` is made to throw here because that is what the policy does to it, and because a
	 * test that let jsdom's fetch succeed would pass against the fetching version: jsdom has no
	 * Content-Security-Policy. The assertion is that the picture arrives ANYWAY.
	 */
	it('turns a pasted picture into a file with no network request at all', async () => {
		const fetching = vi.fn(async () => {
			throw new TypeError('Failed to fetch');
		});
		vi.stubGlobal('fetch', fetching);
		// A one-pixel PNG, base64, exactly as the shell hands one over.
		const png =
			'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';
		window.sift = { readClipboard: vi.fn(async () => ({ text: '', image: png })) };

		const { file } = await readClipboard();

		expect(file).not.toBeNull();
		expect(file?.type).toBe('image/png');
		expect(file?.size).toBeGreaterThan(0);
		expect(fetching).not.toHaveBeenCalled();
	});

	it('names a pasted picture for when it was taken, not with a counter', async () => {
		const png =
			'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';
		window.sift = { readClipboard: vi.fn(async () => ({ text: '', image: png })) };

		const { file } = await readClipboard();

		expect(file?.name).toMatch(/^Pasted \d{4}-\d{2}-\d{2} \d{2}-\d{2}-\d{2}\.png$/);
	});

	it('answers no picture for a data URL that is not base64, rather than a broken file', async () => {
		window.sift = {
			readClipboard: vi.fn(async () => ({ text: '', image: 'data:image/png,not-base64' }))
		};

		expect((await readClipboard()).file).toBeNull();
	});

	it('prefers a picture over text when the clipboard holds both', async () => {
		pretendSecureBrowser([
			{
				types: ['text/plain', 'image/png'],
				getType: async (type: string) =>
					type === 'image/png'
						? new Blob([new Uint8Array([1])], { type: 'image/png' })
						: new Blob(['https://example.com/a'], { type: 'text/plain' })
			}
		]);

		const { file, text } = await readClipboard();

		expect(file).not.toBeNull();
		expect(text).toBe('');
	});
});

/*
 * TWO THINGS A REAL BROWSER DOES HERE AND JSDOM DOES NOT, and the second one is the whole test.
 *
 * `document.execCommand` does not exist in jsdom at all (not a stub answering false: the property
 * is absent), so it is defined rather than spied on. It is also why the restore below has to live
 * in a `finally`: without this the call throws, and a restore written after it would never run.
 *
 * `select()` on a textarea MOVES FOCUS to it in every browser, and in jsdom it does not. That gap
 * is exactly the behaviour being guarded against, so leaving it unmodelled is a test that passes
 * whether or not the guard is there. Modelled here, in the one place, rather than by a test
 * reaching inside the module.
 */
function pretendCopyWorks(): void {
	Object.defineProperty(document, 'execCommand', {
		configurable: true,
		writable: true,
		value: () => true
	});
	vi.spyOn(HTMLTextAreaElement.prototype, 'select').mockImplementation(function (
		this: HTMLTextAreaElement
	) {
		this.focus();
	});
}

describe('copying over a connection with no clipboard object at all', () => {
	/* THE PLAIN-HTTP PATH, which is how most people reach a self-hosted Sift. `navigator.clipboard`
	 * is absent outside a secure context, so the textarea fallback is not an edge case here, it is
	 * the only path. What is pinned is what that fallback does to FOCUS, because it borrows it. */
	it('gives the focus back to whatever had it', async () => {
		/* Focus is given back. `select()` moves focus to the holder and removing the holder
		   drops it on `<body>`, so a control that copies something would quietly deselect
		   itself, and the tooltip over it, which hides on `focusout`, would be torn down by the
		   copy and never say "Copied". */
		vi.stubGlobal('isSecureContext', false);
		vi.stubGlobal('navigator', {});
		pretendCopyWorks();
		const presser = document.createElement('button');
		document.body.append(presser);
		presser.focus();

		const landed = await copyText('a-picture.jpg');

		expect(landed).toBe(true);
		expect(document.activeElement).toBe(presser);
		presser.remove();
	});

	it('leaves the focus alone when what had it has gone from the page', async () => {
		// A control inside a menu that closed on the press. Focusing a detached element does
		// nothing but leave `activeElement` on `<body>` anyway, so it is not attempted.
		vi.stubGlobal('isSecureContext', false);
		vi.stubGlobal('navigator', {});
		pretendCopyWorks();
		const gone = document.createElement('button');
		document.body.append(gone);
		gone.focus();
		gone.remove();

		expect(await copyText('a-picture.jpg')).toBe(true);
	});
});
