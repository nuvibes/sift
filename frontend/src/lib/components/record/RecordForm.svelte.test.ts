/*
 * The whole record, edited together and saved once.
 *
 * The rules under test make "one Save" mean something: everything typed (adding another name,
 * correcting a link, attaching a tag, emptying a box) goes into a draft, and nothing reaches the
 * page until Save, so Cancel undoes all of it.
 *
 * Two are invisible on screen, and are why this file exists rather than an end-to-end walk. The
 * draft is seeded once: the page behind this form re-fetches while it is open, and a draft that
 * followed would throw away half-typed text silently. And Enter inside a list box adds to the list;
 * letting it through would save a record while somebody was still filling one field in.
 */

import { readFileSync } from 'node:fs';

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { words as wordsOn } from '$lib/design/testing.svelte';
import codepoints from '$lib/generated/icon-codepoints.json';
import type { FieldDescription } from '$lib/entity/records.svelte';
import { flushSync, mount, tick, unmount } from 'svelte';

const mocks = vi.hoisted(() => ({
	all: [] as FieldDescription[],
	get: vi.fn(),
	post: vi.fn(),
	put: vi.fn(),
	del: vi.fn()
}));

/* A real registry with its one request replaced, rather than an object shaped like one. Which
   fields a record draws, and which sit behind the switch, are then answered by the class under
   test's own rules: an object retyping them here is a second copy that drifts, and a filter added
   to the registry would leave such a double answering that it did not exist. */
vi.mock('$lib/entity/records.svelte', async (importOriginal) => {
	const real = await importOriginal<typeof import('$lib/entity/records.svelte')>();
	class Standing extends real.Fields {
		override of(): FieldDescription[] {
			return mocks.all;
		}
	}
	return { ...real, fields: new Standing() };
});

/* The tag editor inside the form reaches for the library's tags on its first frame. Answered with
   nothing rather than left to fail: what is under test is where an attached tag GOES, not where the
   list of available ones comes from. */
vi.mock('$lib/api/client', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/client')>()),
	api: { get: mocks.get, post: mocks.post, put: mocks.put, del: mocks.del }
}));

import { appearance } from '$lib/theme/appearance.svelte';
import RecordForm from './RecordForm.svelte';
import { ApiError } from '$lib/api/client';
import { SaveRefused, saveProblem } from '$lib/entity/records.svelte';

function field(over: Partial<FieldDescription> = {}): FieldDescription {
	return {
		key: 'aliases',
		subject: 'person',
		label: 'Aliases',
		kind: 'names',
		shown: 'record',
		group: 'record',
		editable: true,
		links_to: null,
		help: null,
		imported: false,
		suggests: null,
		entry: 'another name',
		ordered: false,
		...over
	};
}

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
});

beforeEach(() => {
	vi.clearAllMocks();
	mocks.get.mockResolvedValue({ items: [], total: 0 });
	mocks.all = [];
});

interface Drawn {
	saved: ReturnType<typeof vi.fn>;
	cancelled: ReturnType<typeof vi.fn>;
	props: Record<string, unknown>;
}

function draw(values: Record<string, unknown>, onsave?: (draft: unknown) => Promise<void>): Drawn {
	const saved = vi.fn(onsave ?? (async () => {}));
	const cancelled = vi.fn();
	host = document.createElement('div');
	document.body.append(host);
	const props = $state({
		subject: 'person' as const,
		values,
		onsave: saved,
		oncancel: cancelled,
		label: 'Edit Jane'
	});
	drawn = mount(RecordForm, { target: host, props }) as Record<string, unknown>;
	flushSync();
	return { saved, cancelled, props };
}

/** The box a list field is added through: the one with the Add button beside it. */
function adder(): HTMLInputElement {
	return host.querySelector('.adding input') as HTMLInputElement;
}

/** The boxes holding the entries already in the list. */
function entries(): HTMLInputElement[] {
	return [...host.querySelectorAll('.entries input')] as HTMLInputElement[];
}

function type(box: HTMLInputElement, text: string) {
	box.value = text;
	box.dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();
}

function press(label: string) {
	const button = [...host.querySelectorAll('button')].find((one) => wordsOn(one) === label);
	if (!button) throw new Error(`no button labelled ${label}`);
	button.click();
	flushSync();
}

it('wears the save glyph on Save, and none on Cancel', () => {
	mocks.all = [field()];
	draw({});
	const named = (label: string) =>
		[...host.querySelectorAll('button')].find((one) => wordsOn(one) === label);

	/* An act wears its glyph and an answer its words: the rule at the head of `Button`. */
	expect(named('Save')?.querySelector(':scope > .icon')?.textContent).toBe(
		String.fromCodePoint(parseInt(codepoints.save, 16))
	);
	expect(named('Cancel')?.querySelector(':scope > .icon')).toBeNull();
});

it('does not write anything until Save', async () => {
	mocks.all = [field()];
	const { saved } = draw({ aliases: ['Janet'] });

	type(adder(), 'Jan');
	press('Add');

	expect(host.querySelectorAll('.entries li')).toHaveLength(2);
	expect(saved, 'adding a name wrote it to the page').not.toHaveBeenCalled();
});

it('keeps what is being typed when the page behind it re-fetches', async () => {
	mocks.all = [field()];
	const { props } = draw({ aliases: ['Janet'] });

	type(adder(), 'half typ');
	// The page re-fetches: a library change, a tag written elsewhere. It hands down new values.
	props.values = { aliases: ['Janet', 'Somebody Else'] };
	await tick();
	flushSync();

	expect(adder().value, 'a re-fetch threw away what was being typed').toBe('half typ');
	expect(entries().map((one) => one.value)).toEqual(['Janet']);
});

it('adds on Enter without saving the record', async () => {
	mocks.all = [field()];
	const { saved } = draw({ aliases: [] });

	const box = adder();
	type(box, 'Jan');
	const key = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true });
	box.dispatchEvent(key);
	flushSync();

	expect(entries().map((one) => one.value)).toEqual(['Jan']);
	expect(key.defaultPrevented, 'Enter submitted the whole form').toBe(true);
	expect(saved).not.toHaveBeenCalled();
});

it("says in the adder's empty box what one entry of THIS list is, from the field itself", () => {
	// A box for tattoos must not ask for "another name". The word is the registry's, per field, and
	// the form invents none.
	mocks.all = [field({ key: 'tattoos', label: 'Tattoos', entry: 'a tattoo' })];
	draw({ tattoos: [] });
	expect(adder().placeholder).toBe('a tattoo');
});

it('will not add the same name twice however it was capitalised', () => {
	mocks.all = [field()];
	draw({ aliases: ['Janet'] });

	type(adder(), '  janet  ');
	press('Add');

	expect(entries().map((one) => one.value)).toEqual(['Janet']);
	// The box is cleared either way: leaving the text there reads as a press that did nothing.
	expect(adder().value).toBe('');
});

it('will not add an empty name', () => {
	mocks.all = [field()];
	draw({ aliases: [] });

	type(adder(), '   ');
	press('Add');

	expect(entries()).toHaveLength(0);
});

it('corrects an entry in place rather than making somebody retype it', () => {
	mocks.all = [field()];
	draw({ aliases: ['Jane', 'Other'] });

	type(entries()[0], 'Janet');

	expect(entries().map((one) => one.value)).toEqual(['Janet', 'Other']);
});

it('removes the entry that was pressed and not the first one', () => {
	// By POSITION. The rows are keyed by position rather than by text: keyed by text, editing a
	// character destroys the row and rebuilds it, which takes the caret with it.
	mocks.all = [field()];
	draw({ aliases: ['Jane', 'Janet', 'Other'] });

	const remove = [...host.querySelectorAll('button')].filter((one) =>
		one.getAttribute('aria-label')?.startsWith('Remove')
	);
	remove[1].click();
	flushSync();

	expect(entries().map((one) => one.value)).toEqual(['Jane', 'Other']);
});

it('drops the boxes somebody emptied and trims the rest on the way out', async () => {
	mocks.all = [field()];
	const { saved } = draw({ aliases: ['Jane', 'Other'] });

	type(entries()[0], '  Janet  ');
	type(entries()[1], '');
	(host.querySelector('form') as HTMLFormElement).requestSubmit();
	await tick();
	await tick();

	// An emptied box is a row somebody was finished with, not a blank name to store.
	expect(saved).toHaveBeenCalledWith(expect.objectContaining({ aliases: ['Janet'] }));
});

it('leaves the form up with what was typed still in it when the save is refused', async () => {
	mocks.all = [field()];
	draw({ aliases: ['Jane'] }, async () => {
		throw new Error('no');
	});

	type(adder(), 'Janet');
	press('Add');
	(host.querySelector('form') as HTMLFormElement).requestSubmit();
	await tick();
	await tick();
	flushSync();

	expect(host.textContent).toContain("That couldn't be saved.");
	expect(entries().map((one) => one.value)).toEqual(['Jane', 'Janet']);
});

it("says a route's refusal in its own words, whichever page saved, and brings it into view", async () => {
	mocks.all = [field()];
	const shown = vi.fn();
	const was = Element.prototype.scrollIntoView;
	Element.prototype.scrollIntoView = shown;
	try {
		draw({ aliases: ['Jane'] }, async () => {
			// Thrown as the client throws it: no page wraps it or opts in.
			throw new ApiError(
				422,
				'Some of those details were not valid.',
				'Choose a Site from another branch.'
			);
		});
		(host.querySelector('form') as HTMLFormElement).requestSubmit();
		for (let turn = 0; turn < 4; turn++) await tick();
		flushSync();

		expect(host.textContent).toContain('Choose a Site from another branch.');
		expect(host.textContent).not.toContain("That couldn't be saved.");
		expect(shown).toHaveBeenCalled();
	} finally {
		Element.prototype.scrollIntoView = was;
	}
});

/* A refusal at the form's foot stands far from the Part of field it is about. A route that names
   the field has its words said under that field; one that names none keeps the foot. */
it('says a refusal under the field the route names, and at the foot when it names none', async () => {
	mocks.all = [
		field({ key: 'details', label: 'Details', kind: 'paragraph' }),
		field({ key: 'parent', label: 'Part of', kind: 'text', suggests: 'site' })
	];
	const said = 'A Site cannot be part of itself.';
	const refuse = async (named?: string) => {
		draw({ details: '', parent: '' }, async () => {
			throw new ApiError(422, 'Some of those details were not valid.', said, named);
		});
		(host.querySelector('form') as HTMLFormElement).requestSubmit();
		for (let turn = 0; turn < 4; turn++) await tick();
		flushSync();
	};
	const was = Element.prototype.scrollIntoView;
	Element.prototype.scrollIntoView = vi.fn();
	try {
		await refuse('parent');
		const fields = [...host.querySelectorAll('.fields > .field')];
		expect(fields[1].textContent).toContain(said);
		expect(fields[0].textContent).not.toContain(said);
		expect(host.querySelector('.fields + div')?.textContent ?? '').not.toContain(said);

		unmount(drawn!);
		drawn = null;
		host.remove();
		await refuse(undefined);
		expect(host.querySelector('.fields')?.textContent).not.toContain(said);
		expect(host.textContent).toContain(said);
	} finally {
		Element.prototype.scrollIntoView = was;
	}
});

it('keeps the flat sentence for a failure that is not a refusal in words', () => {
	const flat = "That couldn't be saved.";
	// A fault in Sift, whatever it says about itself.
	expect(saveProblem(new ApiError(500, 'Something went wrong.', 'Traceback'))).toBe(flat);
	// A refusal with no words in it: a validation report arrives as a list, which is no detail.
	expect(saveProblem(new ApiError(422, 'Some of those details were not valid.'))).toBe(flat);
	expect(saveProblem(new Error('no'))).toBe(flat);
});

it("puts a page's own sentence and a route's refusal in words in front of the reader", () => {
	expect(saveProblem(new SaveRefused('Give the tag a name.'))).toBe('Give the tag a name.');
	expect(saveProblem(new ApiError(422, 'x', 'Choose a tag from another branch.'))).toBe(
		'Choose a tag from another branch.'
	);
});

it('throws the whole draft away on Cancel', () => {
	mocks.all = [field()];
	const { cancelled, saved } = draw({ aliases: ['Jane'] });

	type(adder(), 'Janet');
	press('Add');
	press('Cancel');

	expect(cancelled).toHaveBeenCalled();
	expect(saved).not.toHaveBeenCalled();
});

it('shows a field the server will not take an edit for, and does not offer a box for it', () => {
	mocks.all = [
		field({ key: 'size', label: 'Size', kind: 'bytes', editable: false }),
		field({ key: 'name', label: 'Name', kind: 'text', editable: true })
	];
	draw({ size: 1500, name: 'Jane' });

	// Drawn as it reads. Left out entirely, the form would be a subset of the record somebody has
	// to close it to see.
	expect(host.querySelector('.fixed')?.textContent).toContain('1.5 kB');
	expect(host.querySelectorAll('input')).toHaveLength(1);
});

it('takes a tag into the draft rather than writing it to the library', async () => {
	mocks.all = [field({ key: 'tags', label: 'Tags', kind: 'tags' })];
	const { saved } = draw({ tags: [{ id: 't1', name: 'runway' }] });
	await tick();
	flushSync();

	// The editor is the shared one, so the chips it was handed are the draft's.
	expect(host.textContent).toContain('runway');
	expect(mocks.post, 'attaching a tag wrote it before Save').not.toHaveBeenCalled();
	expect(saved).not.toHaveBeenCalled();
});

it('edits several lines in a box that takes several lines', () => {
	mocks.all = [field({ key: 'details', label: 'Details', kind: 'paragraph' })];
	draw({ details: 'a note' });

	const box = host.querySelector('textarea') as HTMLTextAreaElement;
	expect(box).not.toBeNull();
	expect(box.value).toBe('a note');
});

/*
 * A HEIGHT IS TYPED IN THE SYSTEM THIS ACCOUNT READS IN, and saved in centimetres either way.
 *
 * Here rather than in `measure.test.ts` because the arithmetic is already pinned there and what can
 * go wrong on this side is different: the preference not reaching the field at all, the two boxes
 * writing half a height between them, and a cleared pair saving a measurement of none. The draft is
 * what leaves the form, so each of these is asserted on what Save was handed.
 */
function heightField() {
	return field({ key: 'height_cm', label: 'Height', kind: 'length' });
}

/** One of the two imperial boxes, by the name it gives itself. */
function part(named: string): HTMLInputElement {
	return host.querySelector(`input[aria-label="Height, ${named}"]`) as HTMLInputElement;
}

/** Type into a box that commits when it is left, which is what a number field does. */
function typeAndLeave(box: HTMLInputElement, text: string) {
	type(box, text);
	box.dispatchEvent(new Event('blur', { bubbles: true }));
	flushSync();
}

afterEach(() => {
	appearance.units = 'metric';
});

it('types a height in centimetres for an account reading metric', () => {
	appearance.units = 'metric';
	mocks.all = [heightField()];
	draw({ height_cm: 175 });

	const boxes = [...host.querySelectorAll('input')] as HTMLInputElement[];
	expect(boxes).toHaveLength(1);
	expect(boxes[0].value).toBe('175');
});

it('types a height in feet and inches for an account reading imperial, and saves centimetres', async () => {
	appearance.units = 'imperial';
	mocks.all = [heightField()];
	const { saved } = draw({ height_cm: 175 });

	// 175 cm is 68.9 inches, which is 5 ft 9 in to the nearest inch.
	expect(part('feet').value).toBe('5');
	expect(part('inches').value).toBe('9');

	typeAndLeave(part('feet'), '6');

	// The inch that was not touched is kept, and the pair goes back as whole centimetres: 6 ft 9 in
	// is 81 inches is 205.74 cm, and it reads back as the same pair.
	expect(part('feet').value).toBe('6');
	expect(part('inches').value).toBe('9');

	(host.querySelector('form') as HTMLFormElement).requestSubmit();
	await tick();
	await tick();
	expect(saved).toHaveBeenCalledWith(expect.objectContaining({ height_cm: 206 }));
});

it('draws a height nobody has filled in as an empty measurement', async () => {
	appearance.units = 'imperial';
	mocks.all = [heightField()];
	const { saved } = draw({ height_cm: null });

	// Nothing has been filled in. Zero is what the server already reads as no height, so the pair
	// stands at nought rather than inventing one, and saving it untouched writes nothing new.
	expect(part('feet').value).toBe('0');
	expect(part('inches').value).toBe('0');

	(host.querySelector('form') as HTMLFormElement).requestSubmit();
	await tick();
	await tick();
	expect(saved).toHaveBeenCalledWith(expect.objectContaining({ height_cm: null }));
});

it('saves a height zeroed away as nothing, rather than as a person of no height', async () => {
	appearance.units = 'imperial';
	mocks.all = [heightField()];
	const { saved } = draw({ height_cm: 175 });

	typeAndLeave(part('feet'), '0');
	typeAndLeave(part('inches'), '0');

	(host.querySelector('form') as HTMLFormElement).requestSubmit();
	await tick();
	await tick();
	// The empty string is what the centimetre box sends when it is cleared, and the server files
	// that as nothing at all.
	expect(saved).toHaveBeenCalledWith(expect.objectContaining({ height_cm: '' }));
});

it('names the form for anybody who cannot see whose record it is', () => {
	mocks.all = [field()];
	draw({ aliases: [] });

	expect(host.querySelector('form')?.getAttribute('aria-label')).toBe('Edit Jane');
});

/*
 * The pair that ends an edit, on the right and in one order: Cancel then Save, matching the
 * header's row above the record, so one edit never offers the same two controls mirrored at its two
 * ends.
 */
it('closes with Cancel and then Save, at the right-hand edge', () => {
	mocks.all = [field()];
	draw({ aliases: [] });

	const close = host.querySelector('.close') as HTMLElement;
	const words = [...close.querySelectorAll('button')].map((one) =>
		one.querySelector('.label')?.textContent?.trim()
	);
	expect(words).toEqual(['Cancel', 'Save']);
	// The primary is the submit, so the order is not merely cosmetic: the button at the far edge
	// is the one that ends the edit by saving it.
	expect(close.querySelector('button[type="submit"]')).toBe(close.querySelectorAll('button')[1]);

	/* And the row really is ended. jsdom resolves no layout, so the rule is read where it is
	   written: the same reason `Avatar`'s box is read out of its stylesheet. */
	const source = readFileSync('src/lib/components/record/RecordForm.svelte', 'utf8');
	const rule = source.slice(source.indexOf('\t.close {'));
	expect(rule.slice(0, rule.indexOf('}'))).toContain('justify-content: flex-end');
});
