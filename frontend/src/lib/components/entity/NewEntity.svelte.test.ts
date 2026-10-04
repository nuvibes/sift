/*
 * Add opens a blank form rather than a name prompt.
 *
 * Two things are held: the screen really is the edit form with nothing behind it, so a kind with a
 * record draws every field that record has and a field added to the registry arrives here without
 * anybody remembering it; and nothing is made from a blank name, the one rule this screen adds to
 * the form.
 */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import { ApiError } from '$lib/api/client';
import NewEntity from './NewEntity.svelte';
import { noServerAt } from '../../../test-setup';

/* Left unanswered on purpose: the field registry and the settings behind it, which this file does not draw. */
noServerAt('/api/records/fields', '/api/settings');

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

beforeEach(() => {
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
});

function draw(props: Record<string, unknown>): void {
	drawn = mount(NewEntity, {
		target: host,
		props: {
			noun: 'tag',
			crumbs: [{ label: 'Tags', href: '/tags' }, { label: 'New tag' }],
			oncreate: async () => {},
			oncancel: () => {},
			...props
		}
	}) as Record<string, unknown>;
	flushSync();
}

it("draws the record form, and every field in it is the registry's", () => {
	draw({ subject: 'tag' });
	// `RecordForm`'s own form, named for this screen: the edit form with nothing behind it.
	expect(host.querySelector('form')?.getAttribute('aria-label')).toBe('New tag');
	/* And NOT one box of this component's own. The field registry is fetched from the server, so it
	   is empty here, which is exactly what makes this worth asserting: a name box written into
	   this file would still be drawn, and it would be a second answer to what a tag's form is.

	   The file input the picture control puts on the page is not one of those, so it is excluded
	   by name rather than by the count being loosened: a loosened count would stop noticing the
	   thing this case is about. */
	expect(host.querySelectorAll('input:not([type="file"])').length).toBe(0);
});

it('draws a name and nothing else for a kind with no record', () => {
	draw({ noun: 'collection' });
	expect(host.querySelector('form')?.getAttribute('aria-label')).toBe('New collection');
	expect(host.querySelectorAll('input:not([type="file"])').length).toBe(1);
});

it('refuses a blank name and makes nothing', async () => {
	const oncreate = vi.fn(async () => {});
	draw({ noun: 'collection', oncreate });
	(host.querySelector('form') as HTMLFormElement).requestSubmit();
	await Promise.resolve();
	flushSync();
	expect(oncreate).not.toHaveBeenCalled();
	expect(host.textContent).toContain('Give the collection a name.');
});

it('hands over the trimmed name when there is one', async () => {
	const oncreate = vi.fn(async () => {});
	draw({ noun: 'collection', oncreate });
	const box = host.querySelector('input:not([type="file"])') as HTMLInputElement;
	box.value = '  Best of  ';
	box.dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();
	(host.querySelector('form') as HTMLFormElement).requestSubmit();
	await Promise.resolve();
	flushSync();
	// The picture is the second argument and nobody chose one, which is the ordinary case.
	expect(oncreate).toHaveBeenCalledWith({ name: 'Best of' }, null);
});

/* A NAME THAT IS TAKEN SAYS SO. The create is the one call whose refusal reaches this screen, and a
 * kind that keeps its names unique answers 409 for a name already used, which the form's flat
 * "That couldn't be saved." would leave somebody guessing at, looking at a name spelled right. */
it('says the name is taken when the create answers 409, and keeps the form', async () => {
	const oncreate = vi.fn(async () => {
		throw new ApiError(409, 'That clashes with something already there.');
	});
	draw({ noun: 'collection', oncreate });
	const box = host.querySelector('input:not([type="file"])') as HTMLInputElement;
	box.value = 'Best of';
	box.dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();
	(host.querySelector('form') as HTMLFormElement).requestSubmit();
	await Promise.resolve();
	await Promise.resolve();
	flushSync();

	expect(oncreate).toHaveBeenCalledOnce();
	expect(host.textContent).toContain('There\'s already a collection called "Best of".');
	expect(host.querySelector('form')).not.toBeNull();
});

it('goes back to the wall when Cancel is pressed', () => {
	const oncancel = vi.fn();
	draw({ noun: 'collection', oncancel });
	const cancel = [...host.querySelectorAll('button')].find(
		(one) => one.textContent?.trim() === 'Cancel'
	);
	cancel?.click();
	expect(oncancel).toHaveBeenCalledOnce();
});

/*
 * The two controls that end the screen, at the top as well as the bottom. Asserted as a pair per
 * row and in order, because the fault guarded against is one end of the screen offering them
 * mirrored from the other.
 */
it('offers Save and Cancel at the top and at the foot, Save last in both', () => {
	draw({ noun: 'collection' });
	const rows = [...host.querySelectorAll('header .controls, form .close')];
	expect(rows.length).toBe(2);
	for (const row of rows) {
		/* The LABEL's own text, not the button's: a button with an icon carries the glyph's
		   private-use character in `textContent`, which no trim removes. */
		const words = [...row.querySelectorAll('button')].map((one) =>
			one.querySelector('.label')?.textContent?.trim()
		);
		expect(words).toEqual(['Cancel', 'Save']);
	}
});

it('submits the form from the button in the header, which is outside it', async () => {
	const oncreate = vi.fn(async () => {});
	draw({ noun: 'collection', oncreate });
	const box = host.querySelector('input:not([type="file"])') as HTMLInputElement;
	box.value = 'Best of';
	box.dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();

	const header = host.querySelector('header .controls') as HTMLElement;
	const save = [...header.querySelectorAll('button')].find(
		(one) => one.querySelector('.label')?.textContent?.trim() === 'Save'
	) as HTMLButtonElement;
	/* The mechanism, stated: the header's Save is not inside the form, so what makes it work is
	   `form="..."` pointing at the form's own id. Asserted before it is pressed, because jsdom
	   does not implement that attribute: the press below therefore submits through the form
	   directly, and without this line the case would pass on a button wired to nothing. */
	expect(save.getAttribute('form')).toBe(host.querySelector('form')?.id);
	(host.querySelector('form') as HTMLFormElement).requestSubmit();
	await Promise.resolve();
	flushSync();

	expect(oncreate).toHaveBeenCalledWith({ name: 'Best of' }, null);
});

/* A COVER CAN BE CHOSEN BEFORE THE THING EXISTS. The pencil that changes one lives on the entity's
 * own page, which a new Site does not have yet. */
it('offers a picture, and hands the chosen file to the create', async () => {
	const oncreate = vi.fn(async () => {});
	draw({ noun: 'collection', oncreate });

	const chooser = host.querySelector('input[type="file"]') as HTMLInputElement;
	expect(chooser).not.toBeNull();
	expect(chooser.accept).toBe('image/*');
	expect(host.textContent).toContain('Choose a picture');

	/* The file, as the control would report it. `URL.createObjectURL` is not in jsdom, so it is
	   stood in for: what is being held is that the FILE reaches the create, not what the
	   preview looks like. */
	const picture = new File(['x'], 'cover.png', { type: 'image/png' });
	const urls = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:cover');
	vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
	Object.defineProperty(chooser, 'files', { value: [picture], configurable: true });
	chooser.dispatchEvent(new Event('change', { bubbles: true }));
	flushSync();
	expect(urls).toHaveBeenCalledOnce();
	expect(host.textContent).toContain('Change the picture');

	const box = host.querySelector('input:not([type="file"])') as HTMLInputElement;
	box.value = 'Best of';
	box.dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();
	(host.querySelector('form') as HTMLFormElement).requestSubmit();
	await Promise.resolve();
	flushSync();

	expect(oncreate).toHaveBeenCalledWith({ name: 'Best of' }, picture);
	vi.restoreAllMocks();
});
