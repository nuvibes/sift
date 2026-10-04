import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createRawSnippet, flushSync, mount } from 'svelte';
import type { SvelteMap } from 'svelte/reactivity';
import { reactiveProps } from '$lib/design/testing.svelte';
import { ApiError } from '$lib/api/client';
import type { Choice } from './PickDialog.svelte';
import { toasts } from '$lib/shell/toasts.svelte';
import PickDialog from './PickDialog.svelte';

/*
 * The memory of what this account reaches for, stood in for.
 *
 * Mocked because the real module writes the pick to the account over the network on every press;
 * `frequent.svelte.test.ts` holds the record itself to its word. And reactive, like the real one:
 * `noteUse` writes state that `remembered` reads, so a tracked read re-runs on every press. A plain
 * array would hide that coupling, so a `SvelteMap` gives the stand-in the one property the reset
 * depends on.
 */
const MEMORY = vi.hoisted(() => ({
	noted: vi.fn(),
	/* Built by the factory below rather than here, because a `SvelteMap` cannot be reached from a
	   `vi.hoisted` block: that runs before this file's imports do. */
	record: undefined as unknown as SvelteMap<string, { id: string; name: string }>
}));

vi.mock('$lib/search/frequent.svelte', async () => {
	const { SvelteMap } = await import('svelte/reactivity');
	MEMORY.record = new SvelteMap<string, { id: string; name: string }>();
	return {
		noteUse: (kind: string, choice: { id: string; name: string }) => {
			MEMORY.noted(kind, choice);
			MEMORY.record.set(choice.id, choice);
		},
		recallPicks: () => Promise.resolve(),
		remembered: () => [...MEMORY.record.values()],
		/* The real number, not a stand-in. The sheet draws `PICK_PAGE` rows and says how many it is
		   holding back, so a mock that invented its own value would be testing a ceiling nothing ships,
		   and leaving it out is an error at the point of drawing, not a default. */
		PICK_PAGE: 60
	};
});

/** Seed what this account is remembered to reach for, most reached for first. */
function remembers(...held: { id: string; name: string }[]): void {
	MEMORY.record.clear();
	for (const one of held) MEMORY.record.set(one.id, one);
}

/*
 * Picking SEVERAL things, which is the whole point of this box.
 *
 * A box that applied one answer the moment a row was pressed would make filing a clip under two
 * people: open, pick, wait, find the clip again, open, pick. Every assertion here is about the box
 * holding a set and handing it over once, and about the two ways that can quietly go wrong: a
 * press that acts immediately, and a confirm that fires with nothing ticked.
 */

let host: HTMLElement;

afterEach(() => {
	host?.remove();
	toasts.clear();
	// Portalled to the end of the document, so removing the host leaves the sheet behind and the
	// next test's queries would find the last test's markup.
	document.body.innerHTML = '';
});

const CHOICES = [
	{ id: 'a', name: 'Alice' },
	{ id: 'b', name: 'Bianca' },
	{ id: 'c', name: 'Carol' }
];

function open(props: Record<string, unknown> = {}) {
	host = document.createElement('div');
	document.body.append(host);

	const reactive = reactiveProps({
		open: true,
		title: 'Assign 3 files to a person',
		subject: 'This files them under everyone you tick.',
		choices: CHOICES,
		confirmLabel: (many: number) => (many === 1 ? 'Assign this person' : `Assign these ${many}`),
		onpick: vi.fn(),
		...props
	});

	mount(PickDialog, { target: host, props: reactive });
	flushSync();
	return reactive;
}

/** One row of the list, by the name on it. */
function row(name: string): HTMLButtonElement {
	const found = [...document.querySelectorAll<HTMLButtonElement>('.rows button')].find((button) =>
		button.textContent?.includes(name)
	);
	if (!found) throw new Error(`no row called ${name}`);
	return found;
}

function confirm(): HTMLButtonElement {
	const found = document.querySelector<HTMLButtonElement>('.buttons .confirm');
	if (!found) throw new Error('the sheet has no confirm button');
	return found;
}

function press(button: HTMLElement) {
	button.dispatchEvent(new MouseEvent('click', { bubbles: true }));
	flushSync();
}

/** The sheet this test just opened. They are portalled to the document, so the last one is it. */
function sheet(): Element {
	const all = document.querySelectorAll('.pick-sheet');
	const found = all[all.length - 1];
	if (!found) throw new Error('no sheet is open');
	return found;
}

/** The search box, which is the only input on the sheet. */
function box(): HTMLInputElement {
	const found = document.querySelector<HTMLInputElement>('.pick-sheet input');
	if (!found) throw new Error('the sheet has no search box');
	return found;
}

/** Type into it the way a person does: one value, one input event. */
function type(text: string) {
	const input = box();
	input.value = text;
	input.dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();
}

/** Every name on the list, in the order it is drawn. */
function names(): string[] {
	/* The name's own box, not the whole row: a row also holds a tick and, on a list of a kind the
	   app draws walls of, a picture whose fallback is a letter, which would come back as part of
	   the text. */
	return [...document.querySelectorAll('.rows .name')].map((one) => (one.textContent ?? '').trim());
}

/** What each row's box says: a full tick, a half tick, or nothing. */
function boxes(): string[] {
	return [...document.querySelectorAll('.rows .tick .box')].map((box) =>
		box.classList.contains('on') ? 'on' : box.classList.contains('partly') ? 'partly' : 'off'
	);
}

/** What each row announces to a screen reader, which must be the same three answers. */
function pressed(): (string | null)[] {
	return [...document.querySelectorAll('.rows button')].map((button) =>
		button.getAttribute('aria-pressed')
	);
}

/** Open the sheet with the marks a selection of files would produce, and wait for them to land. */
async function openMarked(
	already: Record<string, 'all' | 'some' | 'none'>,
	over: Record<string, unknown> = {}
) {
	const onunpick = vi.fn();
	const props = open({ already: () => Promise.resolve(already), onunpick, ...over });
	await vi.waitFor(() => expect(boxes()).not.toEqual(['off', 'off', 'off']));
	flushSync();
	/* Joined onto the reactive object rather than returned beside it, so a test can read the sheet's
	   `open` and the removal it was handed from one thing, and so the mock is TYPED, which it is
	   not when it arrives through the untyped bag of overrides `open` takes. */
	return Object.assign(props, { onunpick });
}

describe('a row that cannot be chosen here', () => {
	it('says why on itself, wears the refused mark, and a press ticks nothing', () => {
		// A swap's chooser: a person kept local is listed, so nobody wonders where she went, and
		// never ticked, the rule the walls hold for her card.
		const props = open({
			choices: [...CHOICES, { id: 'j', name: 'Julia', refused: "Kept local, so it isn't offered" }]
		});
		const julia = row('Julia');
		expect(julia.textContent).toContain("Kept local, so it isn't offered");
		expect(julia.getAttribute('aria-disabled')).toBe('true');
		expect(julia.classList.contains('refused')).toBe(true);

		press(julia);
		press(row('Alice'));
		press(confirm());

		expect(props.onpick).toHaveBeenCalledWith([{ id: 'a', name: 'Alice' }]);
	});
});

describe('a row whose person Sift recognizes less surely', () => {
	it('says so under the name with the band, and is still chosen by a press', () => {
		// The export's and a swap's facial fingerprints sheets: under twenty confirmed faces the row
		// wears the band the person's page draws, and the words, and ticks as any other.
		const said = 'Only 3 confirmed faces: Sift recognizes them less surely';
		const props = open({
			choices: [...CHOICES, { id: 'j', name: 'Julia', strength: { band: 'weak', said } }]
		});
		const julia = row('Julia');
		expect(julia.querySelector('.thin')?.textContent?.trim()).toBe(said);
		expect(julia.querySelector('.thin')?.getAttribute('data-band')).toBe('weak');
		expect(row('Alice').querySelector('.thin')).toBeNull();

		press(julia);
		press(confirm());

		expect(props.onpick).toHaveBeenCalledWith([
			{ id: 'j', name: 'Julia', strength: { band: 'weak', said } }
		]);
	});
});

describe('picking several', () => {
	it('does nothing to a row that is merely ticked', () => {
		// Pressing a name must not BE the action: anybody who meant to look at the list would
		// already have filed their clips under whichever name they pressed first.
		const props = open();

		press(row('Alice'));

		expect(props.onpick).not.toHaveBeenCalled();
		expect(props.open).toBe(true);
	});

	it('hands over everything ticked, once', () => {
		const props = open();

		press(row('Alice'));
		press(row('Carol'));
		press(confirm());

		expect(props.onpick).toHaveBeenCalledTimes(1);
		expect(props.onpick).toHaveBeenCalledWith([
			{ id: 'a', name: 'Alice' },
			{ id: 'c', name: 'Carol' }
		]);
		expect(props.open).toBe(false);
	});

	it('lets a tick be taken back', () => {
		const props = open();

		press(row('Alice'));
		press(row('Bianca'));
		press(row('Alice'));
		press(confirm());

		expect(props.onpick).toHaveBeenCalledWith([{ id: 'b', name: 'Bianca' }]);
	});

	it('will not finish with nothing ticked', () => {
		// Disabled rather than absent: a button that appears when you are half way through says
		// nothing about what it was waiting for.
		const props = open();

		expect(confirm().disabled).toBe(true);
		press(confirm());

		expect(props.onpick).not.toHaveBeenCalled();
		expect(props.open).toBe(true);
	});

	it('says on the button how many it is about to act on', () => {
		open();
		press(row('Alice'));
		expect(confirm().textContent?.trim()).toBe('Assign this person');
		press(row('Bianca'));
		expect(confirm().textContent?.trim()).toBe('Assign these 2');
	});

	it('tells the caller BEFORE it closes', () => {
		/*
		 * The order, asserted directly, because the order is the whole of it.
		 *
		 * Closing runs the caller's `open` setter. A caller whose setter clears the thing it is
		 * acting on (the mark being tagged) would have that cleared before its callback ran, and
		 * the callback's own guard would turn the write into a silent no-op: sheet shuts, no
		 * request, nothing on screen to say so.
		 *
		 * Swap the two lines in `finish` back and this goes red, which is the point of asserting the
		 * sheet's state from INSIDE the callback rather than after it.
		 */
		let openWhenTold: unknown;
		const props = open({
			onpick: vi.fn(() => {
				openWhenTold = props.open;
			})
		});

		press(row('Alice'));
		press(confirm());

		expect(props.onpick).toHaveBeenCalledTimes(1);
		expect(openWhenTold).toBe(true);
		expect(props.open).toBe(false);
	});

	it('making one FINISHES the sheet, rather than leaving it waiting', async () => {
		/*
		 * Making something is the answer to this sheet's question: somebody who typed a name
		 * nothing matched and pressed Create has said which one they mean. So the make is followed
		 * by the write it was for (`POST /faces/name` after `POST /people`), never by a confirm
		 * press nobody knows is owed.
		 */
		const props = open({
			choices: [] as Choice[],
			createLabel: 'Create',
			oncreate: async (name: string) => {
				props.choices = [...(props.choices as Choice[]), { id: 'p9', name }];
				return { id: 'p9', name };
			}
		});

		const box = document.querySelector<HTMLInputElement>('.pick-sheet input');
		box!.value = 'Orla Fennimore';
		box!.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();

		press(document.querySelector<HTMLButtonElement>('.make')!);
		await vi.waitFor(() => expect(props.onpick).toHaveBeenCalled());

		expect(props.onpick).toHaveBeenCalledWith([{ id: 'p9', name: 'Orla Fennimore' }]);
		expect(props.open).toBe(false);
	});

	it('sends the new one ONCE, though the store hands it back as well', async () => {
		/*
		 * Every store behind this sheet puts a newly made row straight into the list it hands back,
		 * so for a moment the same row is on both sides of the join: in `everything` and in
		 * `justMade`. A duplicate key would take the whole sheet down, and applying it twice would
		 * be wrong, so the dedupe is by id, and this says so.
		 */
		const props = open({
			choices: [] as Choice[],
			createLabel: 'Create',
			oncreate: async (name: string) => {
				props.choices = [...(props.choices as Choice[]), { id: 'p9', name }];
				return { id: 'p9', name };
			}
		});

		const box = document.querySelector<HTMLInputElement>('.pick-sheet input');
		box!.value = 'Orla Fennimore';
		box!.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();

		press(document.querySelector<HTMLButtonElement>('.make')!);
		await vi.waitFor(() => expect(props.onpick).toHaveBeenCalled());

		expect(props.onpick).toHaveBeenCalledTimes(1);
		expect((props.onpick as ReturnType<typeof vi.fn>).mock.calls[0][0]).toHaveLength(1);
	});

	it('brings what was already ticked along with the one just made', async () => {
		/*
		 * Finishing on Create applies everything ticked, not only the new row: two collections
		 * ticked and a third made applies all three, because the new row is one of the ticked ones.
		 */
		const props = open({
			choices: [{ id: 'c1', name: 'Alice' }] as Choice[],
			createLabel: 'Create',
			oncreate: async (name: string) => {
				props.choices = [...(props.choices as Choice[]), { id: 'p9', name }];
				return { id: 'p9', name };
			}
		});

		press(row('Alice'));
		const box = document.querySelector<HTMLInputElement>('.pick-sheet input');
		box!.value = 'Orla Fennimore';
		box!.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();

		press(document.querySelector<HTMLButtonElement>('.make')!);
		await vi.waitFor(() => expect(props.onpick).toHaveBeenCalled());

		const sent = (props.onpick as ReturnType<typeof vi.fn>).mock.calls[0][0] as Choice[];
		expect(sent.map((one) => one.id).sort()).toEqual(['c1', 'p9']);
	});

	it('says so when making a new one is refused', async () => {
		/*
		 * A name the server will not take (one already used, most often) must be said. Otherwise
		 * the button goes back to rest with the name still in the box, which reads as a press that
		 * did not register, so the next thing somebody does is press it again.
		 *
		 * The server's own words where it gave any: it is the half that knows WHY.
		 */
		const props = open({
			createLabel: 'Create',
			oncreate: vi.fn().mockRejectedValue(new ApiError(409, 'conflict', 'Alice already exists'))
		});

		const box = document.querySelector<HTMLInputElement>('.pick-sheet input');
		box!.value = 'Alice II';
		box!.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();

		press(document.querySelector<HTMLButtonElement>('.make')!);
		await vi.waitFor(() => expect(toasts.items.length).toBe(1));

		expect(toasts.items[0].message).toBe('Alice already exists');
		expect(toasts.items[0].tone).toBe('error');
		// Still open, with the name still in the box: there is something to correct and nowhere
		// else to correct it.
		expect(props.open).toBe(true);
		expect(box!.value).toBe('Alice II');
	});

	it('says something even when the refusal came with no reason', async () => {
		// A network failure or a refusal with no words carries no detail at all. Silence is the one
		// answer that is never right, so the name it could not make is what it says.
		open({
			createLabel: 'Create',
			oncreate: vi.fn().mockRejectedValue(new Error('offline'))
		});

		const box = document.querySelector<HTMLInputElement>('.pick-sheet input');
		box!.value = 'Alice II';
		box!.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();

		press(document.querySelector<HTMLButtonElement>('.make')!);
		await vi.waitFor(() => expect(toasts.items.length).toBe(1));

		expect(toasts.items[0].message).toBe("Alice II couldn't be created");
	});

	it('forgets what was ticked when it is opened again', () => {
		// The sheet is reused rather than rebuilt, so a set left behind would be applied to whatever
		// the NEXT selection is: a set of files nobody was looking at when they ticked anything.
		const props = open();
		press(row('Alice'));
		press(confirm());
		props.onpick = vi.fn();

		props.open = true;
		flushSync();

		expect(confirm().disabled).toBe(true);
	});
});

it('does not draw a row it made twice when the caller-s own list has caught up', async () => {
	/*
	 * The keyed list, rendered with the row in BOTH halves of it, which is what the filter in
	 * `everything` exists to prevent.
	 *
	 * Every store behind this sheet puts a newly made row straight into the list it hands back, so
	 * for the moment after a make the same row is in `made` and in `choices`. Two rows under one key
	 * is not a duplicate on screen: it is an error that takes the sheet down with it, and the pick
	 * that was about to be confirmed with it.
	 *
	 * Reaching it needs the sheet to be OPEN with both halves full, and a make closes the sheet,
	 * so this closes it and opens it again. `made` is cleared on the way IN rather than on the way
	 * out (a box still holding the last search while the sheet fades is the previous question
	 * visibly hanging around), and that clearing is an effect: it runs after the list has been
	 * drawn, which is the window this guards.
	 */
	const props = open({
		choices: [] as Choice[],
		createLabel: 'Create',
		oncreate: async (name: string) => {
			props.choices = [...(props.choices as Choice[]), { id: 'p9', name }];
			return { id: 'p9', name };
		}
	});

	const box = document.querySelector<HTMLInputElement>('.pick-sheet input');
	box!.value = 'Orla Fennimore';
	box!.dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();

	press(document.querySelector<HTMLButtonElement>('.make')!);
	await vi.waitFor(() => expect(props.onpick).toHaveBeenCalled());

	props.open = true;
	flushSync();

	const named = [...document.querySelectorAll('.rows button')].filter((one) =>
		one.textContent?.includes('Orla Fennimore')
	);
	expect(named).toHaveLength(1);
});

/*
 * Asking the server for what is not on this page.
 *
 * `choices` is what the screen already holds, and for people that is one page of a wall of
 * hundreds, so a name past the first page could not be picked at all, however completely it was
 * typed. `onsearch` is the way out, and the three properties below are each easy to get wrong
 * without anything else going red.
 */
describe('finding a choice the screen does not hold', () => {
	afterEach(() => {
		vi.useRealTimers();
	});

	it('waits for the typing to stop before it asks anybody', async () => {
		/* A request per keystroke is six requests for "Amelia", five of them about a prefix nobody
		   meant. The delay is what makes this a search rather than a keylogger pointed at the
		   server.

		   THE CLOCK IS ADVANCED BETWEEN THE KEYSTROKES, and that is the whole test. Typing three
		   times and then advancing past the delay passes with the delay set to ZERO: under fake
		   timers nothing runs until the clock moves, so a keystroke that should have fired
		   immediately simply waits with the rest. Each step here is SHORTER than the delay, so a
		   delay that is real fires nothing and a delay that is not fires on the first step. */
		vi.useFakeTimers();
		const onsearch = vi.fn().mockResolvedValue([]);
		open({ onsearch });

		type('A');
		await vi.advanceTimersByTimeAsync(100);
		type('Am');
		await vi.advanceTimersByTimeAsync(100);
		type('Ame');
		await vi.advanceTimersByTimeAsync(100);

		expect(onsearch).not.toHaveBeenCalled();

		await vi.advanceTimersByTimeAsync(100);

		expect(onsearch).toHaveBeenCalledTimes(1);
		expect(onsearch).toHaveBeenCalledWith('Ame');
	});

	it('offers what came back, and it is a row like any other', async () => {
		/* Found is not the same as pickable. A name drawn from the server that could not be ticked,
		   or that was dropped on the way to `onpick`, would look exactly like this working. */
		const onsearch = vi.fn().mockResolvedValue([{ id: 'z', name: 'Amelia' }]);
		const onpick = vi.fn();
		open({ onsearch, onpick });

		type('Amelia');
		await vi.waitFor(() => expect(names()).toContain('Amelia'));
		press(row('Amelia'));
		press(confirm());

		expect(onpick).toHaveBeenCalledWith([{ id: 'z', name: 'Amelia' }]);
	});

	it('never draws a name the screen already holds twice', async () => {
		/* The list is keyed by id, and two rows under one key is not a duplicate on screen: it is an
		   error that takes the sheet down with it, with the pick that was about to be confirmed. The
		   server answering with somebody who is already on this page is the ordinary case, not an
		   exotic one: a search for "A" finds the held Alice as well as somebody past the page.

		   The answer carries a SECOND, unheld name on purpose: waiting for that one to appear is
		   what proves the answer has landed. Waiting for the CALL instead asserts the count before
		   the promise resolves, which passes with the de-duplication deleted. */
		const onsearch = vi.fn().mockResolvedValue([
			{ id: 'a', name: 'Alice' },
			{ id: 'z', name: 'Amelia' }
		]);
		open({ onsearch });

		type('A');
		await vi.waitFor(() => expect(names()).toContain('Amelia'));

		expect(names().filter((name) => name === 'Alice')).toHaveLength(1);
	});

	it('drops an answer to a question that has already been replaced', async () => {
		/* Two requests in flight and the slower one is the older one: it lands last and draws its
		   own answer over the newer one, so the list ends up describing something nobody has typed
		   for several seconds. The text is compared at the moment the answer arrives, which is the
		   only moment that can tell. */
		let slow: (rows: Choice[]) => void = () => {};
		const onsearch = vi
			.fn()
			.mockImplementationOnce(
				() =>
					new Promise<Choice[]>((resolve) => {
						slow = resolve;
					})
			)
			.mockResolvedValueOnce([{ id: 'n', name: 'Nadia' }]);
		open({ onsearch });

		type('Al');
		await vi.waitFor(() => expect(onsearch).toHaveBeenCalledTimes(1));
		type('Nadia');
		await vi.waitFor(() => expect(onsearch).toHaveBeenCalledTimes(2));
		await vi.waitFor(() => expect(names()).toContain('Nadia'));

		// The first question finally answers, about text nobody is looking at.
		slow([{ id: 'x', name: 'Alfred' }]);
		await Promise.resolve();
		flushSync();

		expect(names()).not.toContain('Alfred');
		expect(names()).toContain('Nadia');
	});

	it('asks nobody at all when the box is emptied', async () => {
		/* An empty question is "everybody", which is the whole wall, and it is asked on the way
		   back from every search, because clearing the box is how somebody starts again. */
		const onsearch = vi.fn().mockResolvedValue([{ id: 'z', name: 'Amelia' }]);
		open({ onsearch });

		type('Amelia');
		await vi.waitFor(() => expect(names()).toContain('Amelia'));
		type('');
		flushSync();

		expect(onsearch).toHaveBeenCalledTimes(1);
		expect(names()).not.toContain('Amelia');
	});
});

describe('what the files are ALREADY on', () => {
	/*
	 * The sheet draws which rows the selection already carries, including "some of them", the same
	 * answer the menu's flyout reads from the same two host functions.
	 */
	beforeEach(() => {
		MEMORY.noted.mockClear();
		remembers();
	});

	it('ticks a row every file is on, and half-ticks one only some are', async () => {
		await openMarked({ a: 'all', b: 'some' });

		expect(names()).toEqual(['Alice', 'Bianca', 'Carol']);
		expect(boxes()).toEqual(['on', 'partly', 'off']);
		expect(pressed()).toEqual(['true', 'mixed', 'false']);
	});

	it('gives the half tick a label, and the other two none', async () => {
		/*
		 * A full box and an empty box say what they mean by being full and empty; a bar does not
		 * say what part of what it is half of, so the half tick explains itself.
		 */
		await openMarked({ a: 'all', b: 'some' });

		const labelled = [...document.querySelectorAll('.rows .tick')].map((tick) =>
			tick.querySelector('.box')?.parentElement?.classList.contains('target') ? 'labelled' : null
		);
		expect(labelled).toEqual([null, 'labelled', null]);
	});

	it('draws no marks at all where the caller cannot take anything off', async () => {
		/* A row that can show a tick and cannot clear it is a control that lies about what pressing
		   it does, so the marks want both halves: the same rule the flyout applies. */
		open({ already: () => Promise.resolve({ a: 'all' }) });
		await Promise.resolve();
		flushSync();

		expect(boxes()).toEqual(['off', 'off', 'off']);
		expect(pressed()).toEqual(['false', 'false', 'false']);
	});

	it('offers nothing to do until something has actually changed', async () => {
		// A row that was already on every file and still shows its tick is not a change, and a
		// button offering to do it again would be offering to do nothing.
		await openMarked({ a: 'all' });

		expect(confirm().disabled).toBe(true);
	});

	it('takes the files off a row whose full tick is cleared', async () => {
		const props = await openMarked({ a: 'all' });

		press(row('Alice'));
		expect(boxes()).toEqual(['off', 'off', 'off']);
		expect(confirm().disabled).toBe(false);

		press(confirm());
		expect(props.onpick).not.toHaveBeenCalled();
		expect(props.onunpick).toHaveBeenCalledWith([{ id: 'a', name: 'Alice' }]);
	});

	it('puts them all on a row only some are on, rather than taking the rest off', async () => {
		// The other direction has no sentence behind it: it would take twelve files out of a
		// collection and leave twenty-eight in, which is not what anybody pressed for.
		const props = await openMarked({ b: 'some' });

		press(row('Bianca'));
		expect(boxes()).toEqual(['off', 'on', 'off']);

		press(confirm());
		expect(props.onpick).toHaveBeenCalledWith([{ id: 'b', name: 'Bianca' }]);
		expect(props.onunpick).not.toHaveBeenCalled();
	});

	it('sends both halves in one finish, and the words on the button say both', async () => {
		const confirmLabel = vi.fn((on: number, off: number) => `on ${on} off ${off}`);
		const props = await openMarked({ a: 'all' }, { confirmLabel });

		press(row('Alice'));
		press(row('Carol'));

		expect(confirm().textContent?.trim()).toBe('on 1 off 1');

		press(confirm());
		expect(props.onpick).toHaveBeenCalledWith([{ id: 'c', name: 'Carol' }]);
		expect(props.onunpick).toHaveBeenCalledWith([{ id: 'a', name: 'Alice' }]);
	});

	it('forgets the marks between sittings, so a stale answer is never drawn', async () => {
		const props = await openMarked({ a: 'all' });

		props.open = false;
		flushSync();
		props.open = true;
		flushSync();

		// Before the answer lands: blank, never the last sitting's marks drawn over new files.
		expect(boxes()).toEqual(['off', 'off', 'off']);
	});
});

describe('the memory of what this account reaches for', () => {
	/*
	 * Selected things come to the top as recently picked: the sheet and the flyout reach for the
	 * same five lists and share one memory.
	 */
	beforeEach(() => {
		MEMORY.noted.mockClear();
		remembers();
	});

	it('records a pick the moment the row is pressed, not when the sheet is finished', () => {
		open({ kind: 'person' });

		press(row('Carol'));

		expect(MEMORY.noted).toHaveBeenCalledWith('person', { id: 'c', name: 'Carol' });
	});

	it('records a row whose tick is cleared as well', async () => {
		/* Somebody clearing a row has reached for it exactly as deliberately as somebody filling
		   one, very often to put it back a moment later, which is when it wants to be near the
		   top. The flyout takes the same view. */
		await openMarked({ a: 'all' }, { kind: 'tag' });

		press(row('Alice'));

		expect(MEMORY.noted).toHaveBeenCalledWith('tag', { id: 'a', name: 'Alice' });
	});

	it('records nothing at all where the caller named no kind', () => {
		// Merging two people and naming a face both open this sheet over a list that is not one of
		// the five the memory counts.
		open();

		press(row('Carol'));

		expect(MEMORY.noted).not.toHaveBeenCalled();
	});

	it('puts what was reached for in front, and loses nothing behind it', () => {
		remembers({ id: 'c', name: 'Carol' }, { id: 'b', name: 'Bianca' });
		open({ kind: 'person' });

		expect(names()).toEqual(['Carol', 'Bianca', 'Alice']);
	});

	it('draws the tail alphabetically for a kind, whatever order the caller held it in', () => {
		/*
		 * The action bar's "Add to" sheets are handed the wall's rows, and the People, Sites and
		 * Tags walls open largest first, so the sheet decides its own order: A to Z, like the
		 * flyout. Case is ignored, as on the walls.
		 */
		remembers({ id: 'z', name: 'Zelda' });
		open({
			kind: 'person',
			choices: [
				{ id: 'c', name: 'Carol' },
				{ id: 'b', name: 'bianca' },
				{ id: 'z', name: 'Zelda' },
				{ id: 'a', name: 'Alice' }
			]
		});

		expect(names()).toEqual(['Zelda', 'Alice', 'bianca', 'Carol']);
		// And the one in front says why: the recency mark.
		const marked = [...document.querySelectorAll('li button')]
			.filter((row) => row.querySelector('[aria-label="Chosen recently"]'))
			.map((row) => row.querySelector('.name')?.textContent?.trim());
		expect(marked).toEqual(['Zelda']);
	});

	it('leaves the order alone for a caller with no kind', () => {
		remembers({ id: 'c', name: 'Carol' });
		open();

		expect(names()).toEqual(['Alice', 'Bianca', 'Carol']);
	});
});

/*
 * A press is not a new opening.
 *
 * Pressing a row must tick it, keep the confirm live, and keep the filtering the typed text made.
 * The reset that runs when the sheet opens must not re-run on a press, even though a press writes
 * the memory above. These are about the reset rather than the memory, because anything else the
 * reset came to read would do the same.
 */
describe('a press is not a new opening', () => {
	beforeEach(() => {
		MEMORY.noted.mockClear();
		remembers();
	});

	it('keeps the tick, the typed narrowing and the confirm', () => {
		open({ kind: 'person' });

		type('Car');
		expect(names()).toEqual(['Carol']);

		press(row('Carol'));

		expect(boxes()).toEqual(['on']);
		expect(box().value).toBe('Car');
		expect(confirm().disabled).toBe(false);
	});

	it('keeps what the files were found to be already on', async () => {
		// The other half of the same reset, and the worse half: the marks are an answer from the
		// server, so a press threw away a round trip as well as the tick it was making.
		await openMarked({ a: 'all' }, { kind: 'tag' });

		press(row('Bianca'));

		expect(boxes()).toEqual(['on', 'on', 'off']);
	});
});

/*
 * The ceiling, said out loud: a sheet showing some of a list and saying nothing is a sheet where
 * the rest do not exist, since nobody scrolls to a bottom that never says it is one. The number is
 * the picker's own `PICK_PAGE`, so the sheet and the flyout cannot cut a list at two lengths.
 */
describe('the ceiling on a long list', () => {
	const MANY = Array.from({ length: 65 }, (_, at) => ({
		id: `p${at}`,
		name: `Person ${String(at).padStart(2, '0')}`
	}));

	it('draws one page of rows and says how many it is not showing', () => {
		open({ choices: MANY });

		expect(document.querySelectorAll('.rows button')).toHaveLength(60);
		expect(document.querySelector('.rest')?.textContent?.trim()).toBe(
			'5 more \u2014 keep typing to filter'
		);
	});

	it('says nothing about a ceiling a list never reaches', () => {
		open();

		expect(document.querySelectorAll('.rows button')).toHaveLength(3);
		expect(document.querySelector('.rest')).toBeNull();
	});
});

/*
 * The picture at the head of a row. The sheets are handed the same rows as the flyout, pictures
 * included, and draw them; nothing is resolved per row.
 */
describe('the picture on a row', () => {
	/* The memory above is module-level and the tests before this one write to it, so the order rows
	   are drawn in is not this group's to assume. What is asserted is which pictures are drawn. */
	beforeEach(() => {
		remembers();
	});

	it('draws the one the caller resolved, without asking for it', () => {
		open({
			kind: 'person',
			choices: [
				{ id: 'a', name: 'Alice', picture: { src: '/api/people/a/cover' } },
				{ id: 'b', name: 'Bianca', picture: { src: '/api/people/b/cover' } }
			]
		});

		const faces = [...document.querySelectorAll('.rows button > .face img')].map((one) =>
			one.getAttribute('src')
		);
		expect(faces).toEqual(['/api/people/a/cover', '/api/people/b/cover']);
	});

	it('falls back to the letter, which is how a tag row lines up with the rest', () => {
		// A tag is a word rather than a thing with a face, so no tag row ever carries a picture. The
		// box is still there and still holds its place, with the name's own initial in it.
		open({ kind: 'tag' });

		const letters = [...document.querySelectorAll('.rows button > .face')].map((one) =>
			one.textContent?.trim()
		);
		expect(letters).toEqual(['A', 'B', 'C']);
		expect(document.querySelectorAll('.rows button > .face img')).toHaveLength(0);
	});

	it('leaves the column off a list that is not one of the kinds the app draws walls of', () => {
		// Folders to move into, and the sheet that merges two people. A column of coloured letters
		// beside rows nobody ever sees a face for is decoration, so the plain row stands.
		open();

		expect(document.querySelectorAll('.rows button > .face')).toHaveLength(0);
		expect(document.querySelectorAll('.rows button')).toHaveLength(3);
	});
});

describe('one answer only', () => {
	/* For the sheets whose question has exactly one answer: which of these people to keep, who to
	 * merge somebody into. Without this the sheet would take ticks like any other list and the
	 * caller would read the first of them, so ticking two would say one thing on screen and do
	 * another, and there
	 * is no sentence that describes folding a person into two people.
	 */
	it('MOVES the tick rather than adding to it, and hands over the one row', () => {
		const picked = vi.fn();
		open({ single: true, onpick: picked });

		press(row('Alice'));
		press(row('Carol'));

		expect(row('Alice').getAttribute('aria-pressed')).toBe('false');
		expect(row('Carol').getAttribute('aria-pressed')).toBe('true');

		press(confirm());
		expect(picked).toHaveBeenCalledWith([{ id: 'c', name: 'Carol' }]);
	});

	it('opens on the row the caller preferred, ready to confirm without a press', () => {
		// A default, not a hint: the sheet is confirmable as it stands and a press is a correction.
		const picked = vi.fn();
		open({ single: true, preset: 'b', onpick: picked });

		expect(row('Bianca').getAttribute('aria-pressed')).toBe('true');

		press(confirm());
		expect(picked).toHaveBeenCalledWith([{ id: 'b', name: 'Bianca' }]);
	});
});

it('draws a face on rows that arrived with one, whether or not the list has a memory behind it', () => {
	/*
	 * The picture is not gated on `kind`, which is only the key of the memory a sheet orders by.
	 * The merge sheet keeps no memory and is the screen asking which of these people is which,
	 * about an act that cannot be undone, so it must draw faces.
	 */
	open({
		kind: undefined,
		choices: [
			{ id: 'a', name: 'Alice', picture: { src: '/api/people/a/cover' } },
			{ id: 'b', name: 'Bianca' }
		]
	});

	// Every row, not only the one with a cover: a column where some rows start at a picture and
	// some at a name is a list nobody can run an eye down. Counted inside THIS sheet: the sheets
	// are portalled to the document, so a count over the document is a count over whatever else is
	// mounted beside it.
	expect(sheet().querySelectorAll('button > .face')).toHaveLength(2);
});

it('leaves the rows plain when nothing was given a picture', () => {
	// A column of empty circles beside a list of folder names is decoration.
	open({ kind: undefined });
	expect(sheet().querySelectorAll('button > .face')).toHaveLength(0);
});

/*
 * The box is `NarrowBox`: the cross, and two Escapes in this order. The first empties the box and
 * the sheet stays up with every row back; the second closes the sheet.
 */
describe('the box at the top of the sheet', () => {
	function escape(): void {
		box().dispatchEvent(
			new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })
		);
		flushSync();
	}

	it('wears the cross while there is something to empty', () => {
		open();
		expect(sheet().querySelector('button.field-clear')).toBeNull();
		type('Bi');
		expect(sheet().querySelector('button.field-clear')).not.toBeNull();
	});

	it('empties the box on the first Escape and closes the sheet on the second', () => {
		const props = open();
		type('Bi');
		expect(document.querySelectorAll('.rows button')).toHaveLength(1);

		escape();
		expect(box().value).toBe('');
		expect(document.querySelectorAll('.rows button')).toHaveLength(3);
		expect(props.open).toBe(true);

		escape();
		expect(props.open).toBe(false);
	});
});

describe('a choice at the head that changes what a tick means', () => {
	it('is drawn above the box, and moving it starts the sheet over with the ticks asked again', async () => {
		let ticked: Record<string, 'all' | 'some' | 'none'> = { a: 'all', b: 'all', c: 'all' };
		const props = await openMarked(ticked, {
			already: () => Promise.resolve(ticked),
			restart: 'except',
			head: createRawSnippet(() => ({ render: () => '<p class="way">Who goes in</p>' }))
		});
		expect(sheet().querySelector('.head .way')?.textContent).toBe('Who goes in');
		expect(boxes()).toEqual(['on', 'on', 'on']);
		press(row('Bianca'));
		type('Car');

		ticked = {};
		(props as Record<string, unknown>).restart = 'only';
		flushSync();
		await vi.waitFor(() => expect(boxes()).toEqual(['off', 'off', 'off']));

		// Started over: the box is empty and the press made under the other way is forgotten.
		expect(box().value).toBe('');
		expect(confirm().disabled).toBe(true);
	});
});
