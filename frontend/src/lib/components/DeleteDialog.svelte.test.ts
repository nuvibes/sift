import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount } from 'svelte';
import { reactiveProps } from '$lib/design/testing.svelte';
import { forgetDeleteConfirmation } from '$lib/shell/remembered.svelte';
import DeleteDialog from './DeleteDialog.svelte';
import { fileVerbs, type FileVerbHandlers } from '$lib/grid/verbs';

/* The wording is the safety mechanism, so the wording is what is asserted.
 *
 * Not the styling and not the extra click: somebody who wants "get this out of my grid" and gets
 * "your files are gone" is failed by a screen that offers two things without making plain which
 * is which. Every test here is about whether the screen says what will actually happen.
 */

/* The sheet's one question to the server: which of these are pictures inside an archive. Each test
   that hands the sheet its ids says what the server answers; the rest never ask. */
const asked = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock('$lib/api/client', () => ({ api: { post: asked.post } }));

let host: HTMLElement;

afterEach(() => {
	// Whatever a test taught this browser about skipping the second question, forgotten: the flag
	// outlives a component, so one test's tick would otherwise be the next test's starting state.
	forgetDeleteConfirmation();
	host?.remove();
	// The dialog is portalled to the end of the document, so removing the host leaves it behind,
	// and the next test's queries would then find the previous test's markup.
	document.body.innerHTML = '';
});

function open(props: Record<string, unknown> = {}) {
	host = document.createElement('div');
	document.body.append(host);

	const reactive = reactiveProps({
		open: true,
		count: 1,
		canDeleteFromDisk: true,
		onconfirm: vi.fn(),
		...props
	});

	mount(DeleteDialog, { target: host, props: reactive });
	flushSync();
	return reactive;
}

/** Everything the dialog says. Scoped to the dialog, so nothing outside it can satisfy an
 * assertion about the words somebody is being shown. */
/* Everything on screen in a dialog right now.
 *
 * All of them rather than the first, because this component has two: choosing the destructive tier
 * closes the first and opens a second, and for a moment after the click both are in the document.
 * Reading only the first would report the dialog that is on its way out. */
function text(): string {
	return [...document.querySelectorAll('[role="alertdialog"]')]
		.map((element) => element.textContent ?? '')
		.join(' ');
}

/* A tier is a `ChoiceCard` inside a `ChoiceGroup`: the library's radio item, drawn as a button.
 * There is no `<input>` to read, so which one is chosen is what `aria-checked` says. */
function tier(name: string): HTMLButtonElement {
	const card = [...document.querySelectorAll('button.card[role="radio"]')].find((element) =>
		element.textContent?.includes(name)
	);
	if (!card) throw new Error(`no tier called ${name}`);
	return card as HTMLButtonElement;
}

/** Whether that tier is the current choice. */
function chosen(name: string): boolean {
	return tier(name).getAttribute('aria-checked') === 'true';
}

/* The confirm of the dialog in front. The last one, for the same reason `text` reads them all. */
function confirmButton(): HTMLButtonElement {
	const buttons = [...document.querySelectorAll('.confirm')];
	return buttons[buttons.length - 1] as HTMLButtonElement;
}

describe('which tier is the default', () => {
	it('the safe one, before anybody touches anything', () => {
		open();

		expect(chosen('Remove from Sift')).toBe(true);
		expect(chosen('Delete from disk')).toBe(false);
	});

	it('and the button says the safe thing rather than "OK"', () => {
		open();

		expect(confirmButton().textContent?.trim()).toBe('Remove from Sift');
	});

	it('answers in the verb the row that opened it says', () => {
		/* One verb from the press to the answer: the file's row and the tier this opens on are the
		   same word, and the title and the button say it too. */
		const nothing = () => {};
		const handlers = new Proxy({}, { get: () => nothing }) as FileVerbHandlers;
		const row = fileVerbs(
			{ isAdmin: true, canSave: false, showingHidden: false, count: 1, handlers },
			{ label: () => 'Save', icon: () => 'download' }
		).find((verb) => verb.id === 'delete');
		open();

		expect(row?.label).toBe('Remove');
		expect(chosen(`${row?.label} from Sift`)).toBe(true);
		expect(confirmButton().textContent?.trim()).toBe(`${row?.label} from Sift`);
		expect(text()).toContain(`${row?.label} file from Sift?`);
	});

	it('confirming without choosing anything removes from Sift', () => {
		const onconfirm = vi.fn();
		open({ onconfirm });

		confirmButton().click();
		flushSync();

		expect(onconfirm).toHaveBeenCalledWith('sift');
	});

	it('the safe tier is not styled as destructive', () => {
		open();

		expect(confirmButton().className).not.toContain('destructive');
	});
});

describe('what each tier promises', () => {
	it('remove-from-Sift says the file stays and can be brought back', () => {
		open();

		expect(text()).toContain('stays on disk');
		expect(text()).toContain('re-scanning');
	});

	it('delete-from-disk says it is permanent and names what is missing', () => {
		/*
		 * There is no bin, so the dialog must not promise one: a sentence talking about Trash would
		 * be a promise nothing keeps, at the moment it matters most.
		 */
		open();

		tier('Delete from disk').click();
		flushSync();

		expect(text()).toContain('deletes');
		expect(text()).toContain('no Trash');
		expect(text()).not.toContain('moved to Trash');
		expect(text()).not.toContain('days');
	});
});

describe('choosing the destructive tier', () => {
	it('changes what the button says it will do', () => {
		open();

		tier('Delete from disk').click();
		flushSync();

		expect(confirmButton().textContent?.trim()).toBe('Delete from disk');
	});

	it('and turns it red, which in this app means exactly one thing', () => {
		open();

		tier('Delete from disk').click();
		flushSync();

		expect(confirmButton().className).toContain('destructive');
	});

	it('and the title names the count rather than asking "are you sure?"', () => {
		open({ count: 12 });

		tier('Delete from disk').click();
		flushSync();

		expect(text()).toContain('Delete 12 files from disk?');
		expect(text()).not.toContain('Are you sure');
	});

	it('does NOT delete on the first confirm: it asks again', () => {
		/* The step that stands where a bin would. With nothing to restore from, one press of the
		 * mouse would be all the protection there is, and it is not enough for an operation with
		 * no undo. */
		const onconfirm = vi.fn();
		open({ onconfirm });

		tier('Delete from disk').click();
		flushSync();
		confirmButton().click();
		flushSync();

		expect(onconfirm).not.toHaveBeenCalled();
		expect(text()).toContain('Permanently delete');
	});

	it('and reports the destructive mode only after the second answer', () => {
		const onconfirm = vi.fn();
		open({ count: 3, onconfirm });

		tier('Delete from disk').click();
		flushSync();
		confirmButton().click();
		flushSync();

		// The second dialog names the count too. Somebody who arrived here by mis-clicking a
		// selection is being told what they are about to lose, not asked "are you sure?".
		expect(text()).toContain('Permanently delete 3 files?');

		// The first dialog has closed itself by now, so this is the second one's button.
		confirmButton().click();
		flushSync();

		expect(onconfirm).toHaveBeenCalledWith('disk');
	});
});

describe('when deleting from disk is not possible here', () => {
	it('the option is absent rather than present and dead', () => {
		open({ canDeleteFromDisk: false, unavailableReason: 'This folder is read-only.' });

		expect(text()).not.toContain('Delete from disk');
	});

	it('and the reason is given, because a missing control explains nothing', () => {
		open({ canDeleteFromDisk: false, unavailableReason: 'This folder is read-only.' });

		expect(text()).toContain('This folder is read-only.');
	});

	it('the safe tier is still offered, on every folder', () => {
		open({ canDeleteFromDisk: false, unavailableReason: 'This folder is read-only.' });

		expect(chosen('Remove from Sift')).toBe(true);
	});
});

/*
 * The permanent tier, and the box that stops it asking twice.
 *
 * The guard here is two questions, and this is the one of the two that may be switched off: the
 * cards stay, because that is where the choice between "out of Sift" and "off the disk" is made and
 * it is the one that stops a tidy-up deleting files. What may be agreed away is the restatement.
 */
describe('the second question', () => {
	/** The tick row on the permanent-delete dialog. */
	function dontAskAgain(): HTMLElement {
		const row = [...document.querySelectorAll('[role="alertdialog"] button')].find((element) =>
			element.textContent?.includes("Don't ask me again")
		);
		if (!row) throw new Error('no box offering to stop asking');
		return row as HTMLElement;
	}

	/** Get as far as the permanent-delete dialog. */
	function reachIt(props: Record<string, unknown> = {}) {
		const reactive = open({ count: 6, ...props });
		tier('Delete from disk').click();
		flushSync();
		confirmButton().click();
		flushSync();
		return reactive;
	}

	it('offers to stop asking, in words that say what is being agreed to', () => {
		reachIt();
		expect(text()).toContain("I understand this is permanent. Don't ask me again.");
	});

	it('closes the gap on the sentence, not by pulling the row up under it', () => {
		/*
		 * The sheet's scroll region clips anything pulled above it by a negative margin, so the
		 * sentence gives the space up instead, through the class the confirm forwards to the sheet.
		 */
		reachIt();
		/* The class is what can be asserted here: jsdom carries no component stylesheet, so a
		   computed margin would read the same whatever the rule says. */
		const sentences = [...document.querySelectorAll('.consequence')];
		expect(sentences.map((one) => one.className)).toContain('consequence asked-again');
	});

	it('remembers nothing from a box that was ticked and then not confirmed', () => {
		const onconfirm = vi.fn();
		reachIt({ onconfirm });
		dontAskAgain().click();
		flushSync();
		// No confirm. The dialog is abandoned exactly as somebody closing it would abandon it.
		document.body.innerHTML = '';

		// A fresh dialog asks again, because a ticked box on a cancelled question agreed to nothing.
		const second = vi.fn();
		open({ count: 2, onconfirm: second });
		tier('Delete from disk').click();
		flushSync();
		confirmButton().click();
		flushSync();

		expect(text()).toContain('Permanently delete 2 files?');
		expect(second).not.toHaveBeenCalled();
	});

	it('skips itself next time once the box was ticked AND the answer confirmed', () => {
		const onconfirm = vi.fn();
		reachIt({ onconfirm });
		dontAskAgain().click();
		flushSync();
		confirmButton().click();
		flushSync();
		expect(onconfirm).toHaveBeenCalledWith('disk');
		document.body.innerHTML = '';

		const again = vi.fn();
		open({ count: 2, onconfirm: again });
		tier('Delete from disk').click();
		flushSync();
		confirmButton().click();
		flushSync();

		// Straight through: one answer, no restatement.
		expect(again).toHaveBeenCalledWith('disk');
		expect(text()).not.toContain('Permanently delete');
	});

	it('still shows the two cards, which are the guard that was never up for negotiation', () => {
		const onconfirm = vi.fn();
		reachIt({ onconfirm });
		dontAskAgain().click();
		flushSync();
		confirmButton().click();
		flushSync();
		document.body.innerHTML = '';

		open({ count: 2, onconfirm: vi.fn() });

		expect(text()).toContain('Remove from Sift');
		expect(text()).toContain('Delete from disk');
		// And it opens on the safe one, as it always does.
		expect(chosen('Remove from Sift')).toBe(true);
	});
});

describe('what each card says and shows', () => {
	it('says the file goes from the disk AND from Sift', () => {
		open({ count: 1 });
		// Both halves. "This deletes the file from your disk" alone leaves it open to read the safe
		// tier as the one that keeps a copy in Sift, which is the opposite of what it does.
		expect(text()).toContain('This deletes the file from your disk and from Sift.');
		expect(text()).toContain("It can't be undone.");
	});

	it('marks each card with the glyph the app already draws that action with', () => {
		open({ count: 1 });

		// The glyph is a font ligature drawn by `Icon`, so what is checked is that each card has one
		// and that the safe one's is the OUTLINED cancel: a filled circle is this app's mark for a
		// job that failed, and it must not sit beside "Remove from Sift".
		const safe = tier('Remove from Sift').querySelector('.icon');
		const gone = tier('Delete from disk').querySelector('.icon');
		expect(safe).not.toBeNull();
		expect(gone).not.toBeNull();
		expect(safe?.classList.contains('filled')).toBe(false);
		expect(gone?.classList.contains('filled')).toBe(false);
	});
});

describe('pictures inside a ZIP file', () => {
	const WHY =
		"That picture is inside a ZIP file, and Sift doesn't change ZIP files, so it can't be deleted from disk.";

	async function answered(answer: { inside_archives: number; why: string | null }, props = {}) {
		asked.post.mockReset();
		asked.post.mockResolvedValue(answer);
		open({ ids: ['a1'], ...props });
		await new Promise((settle) => setTimeout(settle, 0));
		flushSync();
	}

	it('asks the server about exactly the files the sheet is for', async () => {
		await answered({ inside_archives: 0, why: null }, { ids: ['a1', 'a2'], count: 2 });
		expect(asked.post).toHaveBeenCalledWith('/assets/delete/check', {
			body: { asset_ids: ['a1', 'a2'] }
		});
	});

	it('every one inside: the disk card gives way to the server sentence', async () => {
		await answered({ inside_archives: 1, why: WHY });
		expect(text()).toContain(WHY);
		expect(() => tier('Delete from disk')).toThrow();
		expect(chosen('Remove from Sift')).toBe(true);
	});

	it('and the safe tier says the scan leaves it out, not that a rescan brings it back', async () => {
		await answered({ inside_archives: 1, why: WHY });
		expect(text()).toContain('later scans leave this picture out');
		expect(text()).toContain('Organize > Skipped');
		expect(text()).not.toContain('re-scanning');
	});

	it('some inside: the disk card stays, saying how many are left', async () => {
		await answered({ inside_archives: 1, why: WHY }, { ids: ['a1', 'a2', 'a3'], count: 3 });
		expect(tier('Delete from disk').textContent).toContain(
			'One of these is a picture inside a ZIP file, and it stays.'
		);
	});

	it('an ordinary file changes nothing on the sheet', async () => {
		await answered({ inside_archives: 0, why: null });
		expect(tier('Delete from disk')).toBeTruthy();
		expect(text()).toContain('You can add it back by re-scanning.');
	});

	it('nothing is asked where the disk tier is not offered at all', async () => {
		await answered({ inside_archives: 1, why: WHY }, { canDeleteFromDisk: false });
		expect(asked.post).not.toHaveBeenCalled();
	});
});
