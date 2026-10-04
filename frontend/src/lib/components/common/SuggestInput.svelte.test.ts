/* A box that completes from the library's own vocabulary, and never refuses what was typed.
 *
 * The list is a suggestion, never a set of valid answers: a network Sift has never heard of is a
 * perfectly good thing to type: it is how the second site on that network comes to have one to
 * complete from. So every assertion here is about the box helping and then getting out of the way.
 *
 * Two of them are about the ROLES, which are hand-written here because the library's combobox
 * models a value chosen FROM a list. Hand-written roles are exactly what a component library exists
 * to stop being hand-written, so the least this owes is a test that they are the ones ARIA asks
 * for: a `searchbox` where a `combobox` belongs is a control assistive technology announces as
 * something it is not, and it is invisible on screen.
 */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

const mocks = vi.hoisted(() => ({ suggestionsFor: vi.fn() }));

vi.mock('$lib/search/search.svelte', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/search/search.svelte')>()),
	suggestionsFor: mocks.suggestionsFor
}));

import SuggestInput from './SuggestInput.svelte';

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;
let typed: string[] = [];
let submitted: string[] = [];

beforeEach(() => {
	vi.clearAllMocks();
	vi.useFakeTimers();
	mocks.suggestionsFor.mockResolvedValue([]);
	typed = [];
	submitted = [];
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
	document.body.innerHTML = '';
	vi.useRealTimers();
});

function draw(over: Record<string, unknown> = {}): HTMLInputElement {
	drawn = mount(SuggestInput, {
		target: host,
		props: {
			id: 'network',
			value: '',
			suggests: 'site',
			ariaLabel: 'Network',
			oninput: (one: string) => typed.push(one),
			onsubmit: (one: string) => submitted.push(one),
			...over
		} as never
	}) as Record<string, unknown>;
	flushSync();
	return host.querySelector('input') as HTMLInputElement;
}

/** Type into the box the way a person does: the value first, then the event. */
async function type(box: HTMLInputElement, what: string): Promise<void> {
	box.value = what;
	box.dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();
	await vi.advanceTimersByTimeAsync(200);
	flushSync();
}

/* The library portals the list to the document, so it is looked for there rather than in the host. */
function options(): HTMLElement[] {
	return [...document.querySelectorAll('[role="option"]')] as HTMLElement[];
}

it('is announced as a combobox, which is what owning a list of suggestions makes it', () => {
	// Not a searchbox. `getByRole('searchbox')` matches nothing on a box that owns a listbox, and
	// a spec written against the wrong one finds nothing.
	const box = draw();

	expect(box.getAttribute('role')).toBe('combobox');
	expect(box.getAttribute('aria-autocomplete')).toBe('list');
	expect(box.getAttribute('aria-expanded')).toBe('false');
});

it('says which list it controls, so the two are announced as one control', async () => {
	// The id is the library's now, not one this box invents from its own: what is asserted is
	// the relationship: with a list up, the box names an element that is there and is a listbox.
	mocks.suggestionsFor.mockResolvedValue([{ value: 'Northlight' }]);
	const box = draw();
	await type(box, 'Nor');

	expect(box.getAttribute('role')).toBe('combobox');
	expect(box.getAttribute('aria-expanded')).toBe('true');
	expect(document.querySelector('[role="listbox"]')).not.toBeNull();
});

it('tells the caller every keystroke, whether or not anything is suggested', async () => {
	const box = draw();

	await type(box, 'Nor');

	expect(typed).toEqual(['Nor']);
});

it('asks nothing at all for one letter', async () => {
	// One letter matches most of a library. A request per first keystroke is a request per field
	// somebody tabs through.
	const box = draw();

	await type(box, 'V');

	expect(mocks.suggestionsFor).not.toHaveBeenCalled();
	expect(options()).toHaveLength(0);
});

it('asks once per pause rather than once per keystroke', async () => {
	const box = draw();

	box.value = 'Qu';
	box.dispatchEvent(new Event('input', { bubbles: true }));
	box.value = 'Nor';
	box.dispatchEvent(new Event('input', { bubbles: true }));
	box.value = 'Quil';
	box.dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();
	await vi.advanceTimersByTimeAsync(200);

	expect(mocks.suggestionsFor).toHaveBeenCalledTimes(1);
	expect(mocks.suggestionsFor).toHaveBeenCalledWith('site', 'Quil');
});

it('offers what the library already has under that vocabulary', async () => {
	mocks.suggestionsFor.mockResolvedValue([
		{ value: 'Northlight' },
		{ value: 'Northlight Media Group' }
	]);
	const box = draw();

	await type(box, 'Nor');

	expect(options().map((one) => one.textContent?.trim())).toEqual([
		'Northlight',
		'Northlight Media Group'
	]);
	expect(box.getAttribute('aria-expanded')).toBe('true');
});

it('leaves out the completion that is exactly what is already typed', async () => {
	// The box already says it. A list whose only row repeats the box is a list that opened for
	// nothing and covered the field under it.
	mocks.suggestionsFor.mockResolvedValue([{ value: 'northlight' }]);
	const box = draw();

	await type(box, 'Northlight');

	expect(options()).toHaveLength(0);
	expect(box.getAttribute('aria-expanded')).toBe('false');
});

it('never offers a name it is told to leave out, such as the record being edited', async () => {
	// A Site's Part of must not offer the Site itself, which the save could only refuse.
	mocks.suggestionsFor.mockResolvedValue([
		{ value: 'Northlight' },
		{ value: 'Northlight Media Group' }
	]);
	const box = draw({ leaveOut: ['northlight media group'] });

	await type(box, 'Nor');

	expect(options().map((one) => one.textContent?.trim())).toEqual(['Northlight']);
});

it('offers at most eight, because a list longer than the form is not a completion', async () => {
	mocks.suggestionsFor.mockResolvedValue(
		Array.from({ length: 20 }, (_, at) => ({ value: `Northlight ${at}` }))
	);
	const box = draw();

	await type(box, 'Nor');

	expect(options()).toHaveLength(8);
});

it('draws no list at all when a completion cannot be fetched', async () => {
	// Everything here can be typed by hand, so a completion that fails is a box with no
	// completions and never an error message over a form somebody is filling in.
	mocks.suggestionsFor.mockRejectedValue(new Error('no'));
	const box = draw();

	await type(box, 'Nor');

	expect(options()).toHaveLength(0);
	expect(box.getAttribute('aria-expanded')).toBe('false');
});

it('ignores a slow answer for a word that is no longer being typed', async () => {
	// The generation counter. Without it the answer for "Nor" lands under "Northlight Media" and offers
	// completions of a word nobody is looking at.
	let settle!: (value: { value: string }[]) => void;
	mocks.suggestionsFor
		.mockReturnValueOnce(new Promise((resolve) => (settle = resolve)))
		.mockResolvedValue([{ value: 'Northlight Media Group' }]);
	const box = draw();

	box.value = 'Nor';
	box.dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();
	await vi.advanceTimersByTimeAsync(200);
	await type(box, 'Northlight Med');
	settle([{ value: 'Northlight' }]);
	await vi.advanceTimersByTimeAsync(0);
	flushSync();

	expect(options().map((one) => one.textContent?.trim())).toEqual(['Northlight Media Group']);
});

it('takes a suggestion as both what was typed and what was submitted', async () => {
	// Picking one is a whole answer, not a keystroke: the caller decides what submitting means and
	// the box has to say the value twice for it to be both.
	mocks.suggestionsFor.mockResolvedValue([{ value: 'Northlight' }]);
	const box = draw();
	await type(box, 'Nor');

	// The library's row takes a pointer, not a bare click: the press is what it listens for.
	options()[0].dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
	options()[0].dispatchEvent(new PointerEvent('pointerup', { bubbles: true }));
	options()[0].click();
	flushSync();

	expect(typed.at(-1)).toBe('Northlight');
	expect(submitted).toEqual(['Northlight']);
	expect(options()).toHaveLength(0);
});

it('submits what is TYPED on Enter, never what happens to be offered', async () => {
	// The list is a suggestion and not a set of valid answers. A network Sift has never heard of is
	// how the second site on it comes to have one to complete from.
	mocks.suggestionsFor.mockResolvedValue([{ value: 'Northlight' }]);
	const box = draw({ value: 'Quilx' });
	await type(box, 'Quilx');

	box.dispatchEvent(
		new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })
	);
	flushSync();

	expect(submitted).toEqual(['Quilx']);
	expect(options()).toHaveLength(0);
});

/** Type two letters, then arrow onto a row, and report which row the highlight reached. */
async function arrowOntoARow(box: HTMLInputElement): Promise<string> {
	box.dispatchEvent(
		new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true })
	);
	flushSync();
	const reached = document
		.querySelector('[role="option"][data-highlighted]')
		?.getAttribute('data-value');
	// A test that arrowed onto nothing would pass the next assertion by accident.
	expect(reached).toBeTruthy();
	return reached as string;
}

it('takes the row the arrows reached, because the library never reports it', async () => {
	/*
	 * Two letters, ArrowDown, Enter puts the row's text in the box and closes the list without
	 * `onValueChange` firing, so the row is read off the highlight the box publishes to assistive
	 * technology.
	 */
	mocks.suggestionsFor.mockResolvedValue([
		{ value: 'Northlight' },
		{ value: 'Northlight Media Group' }
	]);
	const box = draw();
	await type(box, 'Nor');
	const reached = await arrowOntoARow(box);

	box.dispatchEvent(
		new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })
	);
	flushSync();

	// Both, because picking a row is a whole answer and not a keystroke.
	expect(typed.at(-1)).toBe(reached);
	expect(submitted).toEqual([reached]);
	expect(options()).toHaveLength(0);
});

it('defuses the Enter that follows a row being taken, so the form under it cannot submit', async () => {
	/*
	 * The second Enter is the half of this that reaches the record. The box sits inside a `<form>`,
	 * and an unprevented Enter in a form field is the submit (the default action of the key), so
	 * preventing it is what stops it. With `navigated` left true and no list, a guard of `Enter &&
	 * !navigated` would not fire, nothing would prevent the default, and the whole record would be
	 * saved while somebody was still filling one field in.
	 */
	mocks.suggestionsFor.mockResolvedValue([
		{ value: 'Northlight' },
		{ value: 'Northlight Media Group' }
	]);
	const box = draw();
	await type(box, 'Nor');
	await arrowOntoARow(box);
	box.dispatchEvent(
		new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })
	);
	flushSync();

	const again = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true });
	box.dispatchEvent(again);
	flushSync();

	expect(again.defaultPrevented).toBe(true);
	expect(options()).toHaveLength(0);
});

it('forgets the arrows when the list is dismissed, so the next Enter is what was typed', async () => {
	// Escape takes the list away, and with it the memory that anybody reached for a row. What is in
	// the box is what Enter means again: the whole point of the box is that what is typed wins.
	mocks.suggestionsFor.mockResolvedValue([{ value: 'Northlight' }]);
	const box = draw({ value: 'Quilx' });
	await type(box, 'Quilx');
	await arrowOntoARow(box);

	box.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
	flushSync();
	box.dispatchEvent(
		new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })
	);
	flushSync();

	expect(submitted).toEqual(['Quilx']);
	expect(options()).toHaveLength(0);
});

it('closes the list on Escape and keeps the keystroke to itself', async () => {
	// A window-level handler above this closes the whole sheet. Dismissing a completion list should
	// never close the form under it, so the keystroke stops here while the list is up.
	mocks.suggestionsFor.mockResolvedValue([{ value: 'Northlight' }]);
	const box = draw();
	await type(box, 'Nor');
	let reachedTheWindow = 0;
	const listener = () => (reachedTheWindow += 1);
	window.addEventListener('keydown', listener);
	try {
		box.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
		flushSync();

		expect(options()).toHaveLength(0);
		expect(reachedTheWindow).toBe(0);
	} finally {
		window.removeEventListener('keydown', listener);
	}
});

it('lets Escape past when there is no list to dismiss', async () => {
	// Swallowed only while the list is up. Otherwise the box would eat the keystroke that closes
	// the sheet it is in, from a field that looks like an ordinary text box.
	const box = draw();
	let reachedTheWindow = 0;
	const listener = () => (reachedTheWindow += 1);
	window.addEventListener('keydown', listener);
	try {
		box.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
		flushSync();

		expect(reachedTheWindow).toBe(1);
	} finally {
		window.removeEventListener('keydown', listener);
	}
});

it('keeps the list up when the box merely loses focus, and takes a row that is pressed', async () => {
	// A blur alone is not a dismissal: the library closes the list on a press outside it or on
	// Escape, so a click on a row, which blurs the box first, lands on a row that is still there.
	mocks.suggestionsFor.mockResolvedValue([{ value: 'Northlight' }]);
	const box = draw();
	await type(box, 'Nor');

	box.dispatchEvent(new FocusEvent('blur'));
	flushSync();
	expect(options()).toHaveLength(1);
});
