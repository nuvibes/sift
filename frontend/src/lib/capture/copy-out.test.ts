import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
	copyOut,
	copyOutAction,
	dragOut,
	draggingOut,
	saveActionLabel,
	saveAsset,
	saveToDevice,
	type Copyable
} from './copy-out';
import { toasts } from '$lib/shell/toasts.svelte';
import { noServerAt } from '../../test-setup';

/* Left unanswered on purpose: the size check a copy makes before it starts: nothing here is about the answer. */
noServerAt('/api/x.mp4', '/api/x.png', '/api/assets/a2/save-to-device');

/* Getting a file out of Sift. The desktop drag is the desktop client's to prove; what is tested
 * here is that a plain browser falls back honestly (a still to the clipboard where the clipboard
 * exists, a download everywhere else), and never fakes the drag it cannot do, nor leaves with
 * nothing when the clipboard is out of reach.
 */

const image: Copyable = { id: 'a1', kind: 'image', src: '/api/x.png', filename: 'x.png' };
const video: Copyable = { id: 'a2', kind: 'video', src: '/api/x.mp4', filename: 'x.mp4' };

function pretendSecure() {
	vi.stubGlobal('isSecureContext', true);
	vi.stubGlobal('ClipboardItem', class {} as unknown as typeof ClipboardItem);
	const write = vi.fn(async () => undefined);
	vi.stubGlobal('navigator', { clipboard: { write } });
	const blob = new Blob([new Uint8Array([1])], { type: 'image/png' });
	vi.stubGlobal(
		'fetch',
		vi.fn(async () => ({ ok: true, blob: async () => blob }))
	);
}

afterEach(() => {
	delete window.sift;
	toasts.clear();
	vi.restoreAllMocks();
	vi.unstubAllGlobals();
});

describe('what the MENU action will actually do', () => {
	/* A drag is deliberately not one of the answers, even in the desktop client. An
	 * operating-system drag runs inside the mouse gesture and ends when the button comes up, so a
	 * menu item, which is a click, would start a drag that instantly drops wherever the pointer
	 * happens to be. Taking a file out by dragging is `dragOut`, on a real dragstart. */
	it('is never a drag, even with the desktop client present', () => {
		window.sift = { startDrag: async () => 'dragged' };
		expect(copyOutAction('video')).toBe('download');
	});

	it('is a clipboard copy for a still in a secure context', () => {
		pretendSecure();
		expect(copyOutAction('image')).toBe('copy');
	});

	it('is a download for a still where the clipboard is out of reach (plain http)', () => {
		// No clipboard, and not a secure context: exactly what a self-hosted Sift over http looks
		// like. The still cannot be copied, so the honest action is a download, not a copy that fails.
		vi.stubGlobal('isSecureContext', false);
		expect(copyOutAction('image')).toBe('download');
	});

	it('is a download for anything that is not a still', () => {
		expect(copyOutAction('video')).toBe('download');
		expect(copyOutAction('gif')).toBe('download');
	});
});

describe('the menu label for a selection', () => {
	it('groups the count the way the selection bar does', () => {
		// "Save 1000 to device" under a bar reading "1,000 files selected" would be two spellings.
		expect(saveActionLabel('video', 1000)).toBe('Save 1,000 to device');
		expect(saveActionLabel('video', 2)).toBe('Save 2 to device');
	});
});

describe('in the desktop client', () => {
	/* `copyOut` is the MENU. It downloads even here, for the reason above. */
	it('still downloads from a menu rather than starting a drag', async () => {
		const startDrag = vi.fn(async () => 'dragged');
		window.sift = { startDrag };
		const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

		await copyOut(video);

		expect(startDrag).not.toHaveBeenCalled();
		expect(click).toHaveBeenCalledOnce();
	});
});

describe('dragging out of the window', () => {
	function dragEvent() {
		const event = new Event('dragstart') as DragEvent;
		vi.spyOn(event, 'preventDefault');
		return event;
	}

	/* In a browser the browser's own drag must be left alone: cancelling it and doing nothing
	 * would take away the one thing a browser CAN do with a dragged picture. */
	it('does nothing at all in a browser', () => {
		const event = dragEvent();

		expect(dragOut(event, { id: 'a2', filename: 'x.mp4' })).toBe(false);
		expect(event.preventDefault).not.toHaveBeenCalled();
	});

	it('takes the drag over from the browser in the desktop client', async () => {
		const startDrag = vi.fn(async () => 'dragged');
		window.sift = { startDrag };
		const event = dragEvent();

		expect(dragOut(event, { id: 'a2', filename: 'x.mp4' })).toBe(true);
		expect(event.preventDefault).toHaveBeenCalledOnce();
		await vi.waitFor(() => expect(startDrag).toHaveBeenCalledWith('a2'));
	});

	/* THE FLAG THE DROP OVERLAY READS, and why it cannot be a `dragstart`/`dragend` pair.
	 *
	 * Windows keeps delivering an outgoing drag to this page while the pointer is still over the
	 * window, as `dragenter` carrying `Files`, which is exactly what somebody bringing a file
	 * IN looks like. So dragging a clip towards another application would raise a full-window
	 * "Drop to add" over the library on the way out, offering to import the file it was already
	 * sending.
	 *
	 * `dragend` is no help: `dragOut` prevents the `dragstart`'s default, which means the browser
	 * drag never begins and no `dragend` is ever coming. The operating system's drag does have an
	 * end, though (`startDrag` settles when Windows' own drag loop finishes), so that is what
	 * lowers it.
	 */
	it('says a drag is in the air from the gesture until Windows is finished with it', async () => {
		let finish: (outcome: string) => void = () => {};
		window.sift = { startDrag: () => new Promise<string>((resolve) => (finish = resolve)) };

		expect(draggingOut()).toBe(false);
		dragOut(dragEvent(), { id: 'a2', filename: 'x.mp4' });
		expect(draggingOut(), 'the overlay would arm over an outgoing drag').toBe(true);

		finish('dragged');
		await vi.waitFor(() => expect(draggingOut()).toBe(false));
	});

	it('lowers it even when the drag could not be started at all', async () => {
		// Otherwise one failed drag leaves the window refusing every drop IN for the rest of the
		// session.
		window.sift = {
			startDrag: async () => {
				throw new Error('the addon is not there');
			}
		};

		dragOut(dragEvent(), { id: 'a2', filename: 'x.mp4' });
		await vi.waitFor(() => expect(draggingOut()).toBe(false));
	});

	/* There is no "drag it again", and nothing should say there is.
	 *
	 * A file on another machine is one gesture, with the receiving application reading the file
	 * as it arrives. So a successful drag says NOTHING: the file is on its way to wherever it
	 * was dropped, and a message telling somebody to repeat a gesture they have just completed is
	 * worse than silence.
	 */
	it('says nothing at all when the drag was taken', async () => {
		window.sift = { startDrag: async () => 'dragged' };
		dragOut(dragEvent(), { id: 'a2', filename: 'x.mp4' });

		await vi.waitFor(() => expect(window.sift).toBeDefined());
		expect(toasts.items.some((toast) => /ready|again/i.test(toast.message))).toBe(false);
	});

	it('says so when there is nothing to drag', async () => {
		window.sift = { startDrag: async () => 'unavailable' };
		dragOut(dragEvent(), { id: 'a2', filename: 'x.mp4' });

		await vi.waitFor(() => expect(toasts.items.some((toast) => toast.tone === 'error')).toBe(true));
	});
});

describe('in a secure browser', () => {
	beforeEach(pretendSecure);

	it('copies a still to the clipboard', async () => {
		await copyOut(image);

		expect(navigator.clipboard.write).toHaveBeenCalledOnce();
		expect(toasts.items.some((toast) => toast.tone === 'success')).toBe(true);
	});

	it('downloads the still instead when the copy will not go, rather than failing', async () => {
		vi.mocked(navigator.clipboard.write).mockRejectedValueOnce(new Error('denied'));
		const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
		await copyOut(image);

		expect(click).toHaveBeenCalledOnce();
		expect(toasts.items.some((toast) => toast.tone === 'error')).toBe(false);
	});

	it('downloads anything that is not a still', async () => {
		const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
		await copyOut(video);

		expect(click).toHaveBeenCalledOnce();
		expect(toasts.items.length).toBe(1);
	});
});

describe('over plain http, with no clipboard', () => {
	it('downloads a still rather than trying a clipboard that is not there', async () => {
		vi.stubGlobal('isSecureContext', false);
		const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
		await copyOut(image);

		expect(click).toHaveBeenCalledOnce();
	});
});

describe('what leaves carries no location', () => {
	/* Copy image takes the copy the server's door makes, not `/stream`: the original, copied into
	 * a chat window, would carry where it was taken. */
	it('copies a still from the outgoing door, never the stream', async () => {
		pretendSecure();
		await saveAsset({ id: 'a1', media_type: 'image/jpeg', original_filename: 'x.jpg' });

		expect(vi.mocked(fetch).mock.calls[0]?.[0]).toBe('/api/assets/a1/outgoing');
		expect(navigator.clipboard.write).toHaveBeenCalledOnce();
	});

	/* The HEAD before a save has to say when the save would refuse the file, or the browser
	 * shows a failed download with nothing of Sift's to say why. */
	it("says Sift couldn't take the location out, and starts no download", async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn(async () => ({ ok: false, status: 422 }))
		);
		const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
		await saveToDevice({ id: 'a1', media_type: 'image/jpeg', original_filename: 'x.jpg' });

		expect(click).not.toHaveBeenCalled();
		expect(toasts.items.map((toast) => toast.message)).toContain(
			"Sift couldn't take the location out of x.jpg, so it wasn't saved"
		);
	});
});
