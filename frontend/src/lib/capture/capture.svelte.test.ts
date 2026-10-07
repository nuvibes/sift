import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { capture } from './capture.svelte';
import { setCsrfToken } from '../api/client';
import { toasts } from '../shell/toasts.svelte';
import { imports } from '../library/imports.svelte';

/* Adding things. What the server decides (link versus bytes) is tested where the server is; what
 * is tested here is that this gathers what the browser gives and calls the right endpoint with it,
 * and that a refusal becomes a toast rather than an unhandled rejection.
 */

const originalFetch = globalThis.fetch;

type FetchCall = [URL | RequestInfo, RequestInit];

function respondWith(body: unknown = {}, status = 202) {
	const mock = vi.fn(
		async (): Promise<Response> =>
			new Response(JSON.stringify(body), {
				status,
				headers: { 'content-type': 'application/json' }
			})
	);
	globalThis.fetch = mock as unknown as typeof fetch;
	return {
		call(index = 0): FetchCall {
			const calls = mock.mock.calls as unknown as FetchCall[];
			if (!calls[index]) throw new Error(`fetch was not called ${index + 1} time(s)`);
			return calls[index];
		},
		get count() {
			return mock.mock.calls.length;
		}
	};
}

function url(call: FetchCall): string {
	return String(call[0]);
}

function form(call: FetchCall): FormData {
	return call[1].body as FormData;
}

function transfer(parts: { url?: string; files?: File[] }): DataTransfer {
	return {
		getData: (type: string) =>
			type === 'text/plain' || type === 'text/uri-list' ? (parts.url ?? '') : '',
		files: (parts.files ?? []) as unknown as FileList
	} as unknown as DataTransfer;
}

const aFile = (name = 'clip.png') =>
	new File([new Uint8Array([1, 2, 3])], name, { type: 'image/png' });

beforeEach(() => {
	setCsrfToken('a-token');
	capture.imports = [];
	toasts.clear();
	vi.stubGlobal('location', new URL('http://localhost:5171/browse'));
});

afterEach(() => {
	globalThis.fetch = originalFetch;
	vi.unstubAllGlobals();
});

describe('a link typed into the panel', () => {
	it('is posted to the url endpoint as a download', async () => {
		const fetched = respondWith({ download_id: 'd1' });
		await capture.submitUrl('https://example.com/a', 'folder-1');

		expect(url(fetched.call())).toBe('http://localhost:5171/api/capture/import/url');
		expect(JSON.parse(fetched.call()[1].body as string)).toEqual({
			url: 'https://example.com/a',
			dest_folder_id: 'folder-1'
		});
	});
});

describe('a file from the picker', () => {
	it('is posted as multipart to the file endpoint and becomes a pending import', async () => {
		const fetched = respondWith({ job_id: 'j1' });
		await capture.submitFile(aFile(), null);

		const call = fetched.call();
		expect(url(call)).toBe('http://localhost:5171/api/capture/import/file');
		expect(form(call).get('file')).toBeInstanceOf(File);
		// A placeholder appears immediately, keyed by the job that will settle it.
		expect(capture.imports).toEqual([{ id: 'j1', name: 'clip.png', toast: expect.any(Number) }]);
	});
});

describe('a drop', () => {
	it('sends a link and any bytes together, and lets the server prefer the link', async () => {
		const fetched = respondWith({ download_id: 'd1' });
		await capture.handleDrop(transfer({ url: 'https://example.com/a', files: [aFile()] }), null);

		const call = fetched.call();
		expect(url(call)).toBe('http://localhost:5171/api/capture/import/clipboard');
		expect(form(call).get('url')).toBe('https://example.com/a');
		expect(form(call).get('origin')).toBe('drop');
	});

	it('imports each file when several are dropped with no link', async () => {
		const fetched = respondWith({ job_id: 'j1' });
		await capture.handleDrop(transfer({ files: [aFile('a.png'), aFile('b.png')] }), null);

		expect(fetched.count).toBe(2);
	});

	it('does nothing when there is neither a link nor a file', async () => {
		const fetched = respondWith();
		await capture.handleDrop(transfer({}), null);

		expect(fetched.count).toBe(0);
	});
});

describe('a paste', () => {
	it('is ignored when the clipboard holds nothing usable', async () => {
		const fetched = respondWith();
		await capture.handlePaste(transfer({}), null);

		expect(fetched.count).toBe(0);
	});

	it('sends what it has to the clipboard endpoint as a paste', async () => {
		const fetched = respondWith({ job_id: 'j1' });
		await capture.handlePaste(transfer({ files: [aFile()] }), null);

		expect(form(fetched.call()).get('origin')).toBe('paste');
	});
});

describe('when the server refuses', () => {
	it('shows a toast rather than throwing', async () => {
		respondWith({ detail: 'nope' }, 403);
		await capture.submitFile(aFile(), null);

		expect(toasts.items.some((toast) => toast.tone === 'error')).toBe(true);
		expect(capture.imports).toEqual([]);
	});
});

describe('settling a placeholder', () => {
	it('forgets the one whose tile now exists', () => {
		capture.imports = [
			{ id: 'j1', name: 'a' },
			{ id: 'j2', name: 'b' }
		];
		capture.settled('j1');

		expect(capture.imports).toEqual([{ id: 'j2', name: 'b' }]);
	});

	it('says where a file already is when it landed once', () => {
		/* A file the library already has is not copied again; the import's note says where it is,
		   and that sentence is what the drop answers with. */
		imports.page = {
			jobs: [{ id: 'j1', type: 'import', state: 'done', note: 'Already here: Photos > Summer' }]
		} as unknown as typeof imports.page;
		capture.imports = [{ id: 'j1', name: 'a' }];

		capture.settled('j1');
		capture.settled('j1');

		expect(toasts.items.map((toast) => toast.message)).toEqual(['Already here: Photos > Summer']);
		imports.page = null;
	});

	it('takes the Importing toast away when the answer says where the file already is', () => {
		imports.page = {
			jobs: [{ id: 'j1', type: 'import', state: 'done', note: 'Already here: Photos > Summer' }]
		} as unknown as typeof imports.page;
		const toast = toasts.show('Importing\u2026');
		capture.imports = [{ id: 'j1', name: 'a', toast }];

		capture.settled('j1');

		expect(toasts.items.map((one) => one.message)).toEqual(['Already here: Photos > Summer']);
		imports.page = null;
	});

	it('says nothing for a file that landed as a new one', () => {
		imports.page = {
			jobs: [{ id: 'j1', type: 'import', state: 'done', note: null }]
		} as unknown as typeof imports.page;
		capture.imports = [{ id: 'j1', name: 'a' }];

		capture.settled('j1');

		expect(toasts.items).toEqual([]);
		imports.page = null;
	});
});

describe('the Paste button', () => {
	afterEach(() => {
		delete window.sift;
		vi.unstubAllGlobals();
	});

	it('sends a link the clipboard was holding', async () => {
		window.sift = { readClipboard: async () => ({ text: 'https://example.com/a', image: null }) };
		const sent = stubSend();

		await capture.pasteFromClipboard();

		expect(sent()?.get('url')).toBe('https://example.com/a');
		expect(sent()?.get('origin')).toBe('paste');
	});

	/* Nothing usable is an ordinary outcome, not a failure: somebody presses the button with a row
	 * of spreadsheet cells on the clipboard. It says so rather than appearing to do something. */
	it('says so when there is nothing on the clipboard it can take', async () => {
		window.sift = { readClipboard: async () => ({ text: '', image: null }) };
		const sent = stubSend();

		await capture.pasteFromClipboard();

		expect(sent()).toBeNull();
		expect(toasts.items.some((toast) => toast.message.includes('nothing on the clipboard'))).toBe(
			true
		);
	});

	it('sends the destination folder it was given', async () => {
		window.sift = { readClipboard: async () => ({ text: 'https://example.com/a', image: null }) };
		const sent = stubSend();

		await capture.pasteFromClipboard('folder-1');

		expect(sent()?.get('dest_folder_id')).toBe('folder-1');
	});
});

/** Catch the FormData the store would have posted, without a server. */
function stubSend(): () => FormData | null {
	let body: FormData | null = null;
	vi.stubGlobal(
		'fetch',
		vi.fn(async (_url: string, options: { body?: BodyInit }) => {
			body = (options?.body as FormData) ?? null;
			return new Response(JSON.stringify({ job_id: 'j1' }), {
				status: 200,
				headers: { 'content-type': 'application/json' }
			});
		})
	);
	return () => body;
}
