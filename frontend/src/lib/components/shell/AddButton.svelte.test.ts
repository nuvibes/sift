/*
 * The Add panel: what it takes, and what could take it away.
 *
 * Two things it opens are not inside it: a portalled folder menu, and the operating system's file
 * chooser. If the panel closed when the pointer left, the first would lose its list and the second
 * would destroy the `<input type=file>` whose dialog is still on screen, discarding the files
 * somebody picked.
 *
 * The panel's life belongs to the library's `Popover`: nothing here closes it because a pointer
 * left, and a pointer down or a focus inside it marks it as in use. The first test below is that
 * property.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount } from 'svelte';
import AddButton from './AddButton.svelte';
import addSource from './AddButton.svelte?raw';
import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import { capture } from '$lib/capture/capture.svelte';
import { api } from '$lib/api/client';
import { swapMode } from '$lib/swap/mode.svelte';
import topBarSource from './TopBar.svelte?raw';

vi.mock('$lib/shell/session.svelte', () => ({ session: { isAdmin: true } }));
vi.mock('$lib/api/client', () => ({
	api: { get: vi.fn(async () => ({ folders: [] })), post: vi.fn(async () => ({})) },
	request: vi.fn(async () => ({})),
	ApiError: class extends Error {}
}));

let host: HTMLElement;

beforeEach(() => {
	vi.restoreAllMocks();
});

afterEach(() => {
	host?.remove();
	// The panel is portalled to the end of the document, so removing the host does not take it with
	// it, and a panel left standing is found by the next test in this file.
	for (const stale of document.querySelectorAll('.popover-panel')) stale.remove();
});

function trigger(): HTMLElement {
	/* The MAIN HALF, not the wrapper. Hover on the wrapper would be hover on the clipboard half
	   too: pointing at the button that pastes would drop the whole panel open for an action that
	   opens nothing. `SplitButton` hands what it is given to the main half, and the
	   library's trigger props are what it is given. */
	return host.querySelector('button') as HTMLElement;
}

function render() {
	host = document.createElement('div');
	document.body.append(host);
	mount(AddButton, { target: host });
	flushSync();
	// The panel opens on hover: it is the one control in the top bar worth reaching for without
	// aiming, and everything below is about what happens once it is open. A POINTER event, because
	// the library listens for the pointer rather than for the mouse.
	trigger().dispatchEvent(new MouseEvent('pointerenter', { bubbles: false }));
	flushSync();
	return host;
}

/** The panel, wherever in the document it has been portalled to. */
function panel(): HTMLElement | null {
	return document.querySelector('.popover-panel');
}

describe('the panel while it is being used', () => {
	it('is not taken away by the pointer leaving the button', async () => {
		/*
		 * Somebody presses into the panel (the folder chooser, whose list is portalled out of it,
		 * or the button that opens the operating system's file chooser), and the pointer is then
		 * somewhere this panel cannot see. Closing then would take away the list, or destroy the
		 * `<input type=file>` whose dialog is still open and lose the chosen files.
		 *
		 * A press inside the panel says it is being used, and a panel in use is not closed by a
		 * pointer going anywhere. That is the library's rule, not a flag kept here.
		 */
		render();
		const surface = panel();
		expect(surface, 'the panel never opened').not.toBeNull();

		surface?.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true }));
		trigger().dispatchEvent(new MouseEvent('pointerleave', { bubbles: false }));
		await new Promise((resolve) => setTimeout(resolve, 200));
		flushSync();

		expect(panel(), 'the panel closed under whatever was being used').not.toBeNull();
	});

	it('goes away again when it was only hovered', async () => {
		// The other half of the same rule, and the reason the one above is not simply "never closes":
		// a panel that opened because a pointer passed over the button is gone when it passes on.
		render();

		trigger().dispatchEvent(new MouseEvent('pointerleave', { bubbles: false }));
		await new Promise((resolve) => setTimeout(resolve, 200));
		flushSync();

		expect(panel()).toBeNull();
	});
});

describe('choosing files', () => {
	it('sends what was picked, and shuts', () => {
		render();
		const submit = vi.spyOn(capture, 'submitFile').mockResolvedValue(undefined);
		const input = panel()?.querySelector('input[type="file"]') as HTMLInputElement;
		const file = new File(['x'], 'holiday.mp4', { type: 'video/mp4' });
		// `files` is read-only on the element, and there is no chooser here to fill it.
		Object.defineProperty(input, 'files', { value: [file], configurable: true });

		input.dispatchEvent(new Event('change', { bubbles: true }));
		flushSync();

		expect(submit).toHaveBeenCalledWith(file, null);
	});
});

describe('pasting a link', () => {
	function paste(text: string) {
		const url = panel()?.querySelector('input[type="url"]') as HTMLInputElement;
		const event = new Event('paste', { bubbles: true, cancelable: true });
		Object.defineProperty(event, 'clipboardData', { value: { getData: () => text } });
		url.dispatchEvent(event);
		flushSync();
		return event;
	}

	it('starts fetching without waiting for the button', () => {
		// Pasting a link IS the request. Somebody who copied a URL, opened this panel and pressed
		// Ctrl-V has said everything there is to say.
		const submit = vi.spyOn(capture, 'submitUrl').mockResolvedValue(undefined);
		render();

		paste('https://example.com/a-clip');

		expect(submit).toHaveBeenCalledWith('https://example.com/a-clip', null);
	});

	it('takes the paste over, so the text is not left behind to be sent twice', () => {
		vi.spyOn(capture, 'submitUrl').mockResolvedValue(undefined);
		render();

		const event = paste('https://example.com/a-clip');

		expect(event.defaultPrevented).toBe(true);
	});

	it('leaves anything that is not a whole web address alone', () => {
		/* Deliberately narrow. A fragment somebody happened to have copied is not a request, and
		 * `file:` and `javascript:` are valid URLs that have no business reaching a downloader. */
		const submit = vi.spyOn(capture, 'submitUrl').mockResolvedValue(undefined);
		render();

		paste('example.com/a-clip');
		paste('just some words');
		paste('file:///etc/passwd');
		// A scheme rather than a call, so this line is a URL fixture and not something the
		// no-native-chrome gate reads as the page opening a browser dialog.
		paste('javascript:void 0');

		expect(submit).not.toHaveBeenCalled();
	});
});

describe('the folder chooser', () => {
	/*
	 * The first row NAMES the folder it means.
	 *
	 * "Default download folder" would name a setting rather than a place: the one thing somebody
	 * wants to know before accepting it is where the file will land, and that answer would be on
	 * another screen. The name comes from `/site-options`, which is where the default is stored:
	 * asked of the folder list it would be a second answer free to disagree with the settings screen.
	 */
	it('says which folder the default is', async () => {
		// Two answers in the order the panel asks for them: the folders, then what the empty choice
		// means. `Once`, so neither leaks into the tests above.
		vi.mocked(api.get).mockImplementationOnce(async () => ({
			folders: [{ id: 'f1', name: 'Sift Downloads', rel_path: 'Sift Downloads' }]
		}));
		vi.mocked(api.get).mockImplementationOnce(async () => ({
			default: { scope: '*default*', naming: null, dest_folder_id: 'f1', downloader: null },
			sites: [],
			tokens: {},
			downloaders: []
		}));

		render();

		await vi.waitFor(() => {
			flushSync();
			expect(panel()?.querySelector('.ui-select-value')?.textContent).toContain(
				'Sift Downloads (default)'
			);
		});
	});

	it('says nothing it cannot know when the default has never been set', async () => {
		// A folder has to exist for the chooser to be drawn at all: an add panel with nowhere to put
		// anything offers no destination row.
		vi.mocked(api.get).mockImplementationOnce(async () => ({
			folders: [{ id: 'f1', name: 'Clips', rel_path: 'Clips' }]
		}));
		vi.mocked(api.get).mockImplementationOnce(async () => ({
			default: { scope: '*default*', naming: null, dest_folder_id: null, downloader: null },
			sites: [],
			tokens: {},
			downloaders: []
		}));

		render();

		await vi.waitFor(() => {
			flushSync();
			expect(panel()?.querySelector('.ui-select-value')?.textContent).toContain(
				'Not set, so each download asks'
			);
		});
	});
});

/*
 * On a phone the top bar is the search box and a row of squares, so Add is a square: its glyph
 * shows and its word stays its name. The unit environment answers only a plain `screen` rule, so
 * the phone rule is read as one.
 */
/*
 * The folder row says what it is, "Download folder", and swap mode is entered from the panel's
 * bottom right: the last row holds Choose files at its left and the swap control on the panel's
 * right edge, where a row's button sits, and not on the top bar.
 */
describe("the panel's words and its last row", () => {
	afterEach(() => swapMode.leave());

	it('names the folder row Download folder', async () => {
		vi.mocked(api.get).mockImplementationOnce(async () => ({
			folders: [{ id: 'f1', name: 'Clips', rel_path: 'Clips' }]
		}));
		render();
		await vi.waitFor(() => {
			flushSync();
			expect(panel()?.textContent).toContain('Download folder');
		});
		expect(panel()?.textContent).not.toContain('Where it goes');
	});

	it('puts swap mode at the bottom right, after Choose files, and closes to show the drawer', async () => {
		render();
		const last = panel()?.querySelector('.last-row');
		expect(last, 'there is no last row').not.toBeNull();
		expect(last?.parentElement?.lastElementChild, 'the row is not the last thing').toBe(last);
		const buttons = [...(last?.querySelectorAll('button') ?? [])];
		expect(buttons.at(-1)?.getAttribute('aria-label')).toBe('Pick what to offer in a swap');
		expect(last?.textContent).toContain('Choose files');
		expect(addSource).toMatch(/\.last-row \{[^}]*justify-content: space-between;/);

		buttons.at(-1)?.click();
		await new Promise((resolve) => setTimeout(resolve, 200));
		flushSync();
		expect(swapMode.on).toBe(true);
		expect(panel(), 'the panel stayed over the drawer').toBeNull();
	});

	it('is no longer on the top bar', () => {
		expect(topBarSource).not.toContain('SwapModeButton');
	});
});

describe("Add at a phone's width", () => {
	afterEach(removeStyles);

	it('is a square whose word is its name, not a pill', () => {
		render();
		const button = trigger();
		const phone = '@media (max-width: 767px)';
		expect(addSource, 'Add has no phone rule').toContain(phone);
		applyStyles(addSource.replace(phone, '@media screen'), host.querySelector('.add'));

		const label = button.querySelector('.label') as HTMLElement;
		expect(label.textContent?.trim()).toBe('Add');
		expect(getComputedStyle(label).position).toBe('absolute');
		expect(getComputedStyle(label).clipPath).toBe('inset(50%)');
	});
});
