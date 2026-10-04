/*
 * The file's record: which rows it draws, and when.
 *
 * One rule is under test here and it is the one that cannot be seen when it breaks. A file's record
 * declares thirty-odd machine facts and most files answer a handful, so the panel beside the
 * lookalikes would be mostly dashes, and `onlyFilled` takes the empty ones out WHILE THE CELLS ARE
 * VALUES and puts every one of them back the moment the cells are boxes. Both halves matter: with
 * the second one broken, the form can only edit what is already filled in, which is a form that
 * cannot be used to fill anything in. Nothing about that is visible in a screenshot of either mode
 * on its own.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { FieldDescription } from '$lib/entity/records.svelte';
import { createRawSnippet, flushSync, mount, tick, unmount } from 'svelte';

const mocks = vi.hoisted(() => ({
	/* The registry, stood in for. The real one memoises a request for the session, so a component
	   test using it would be testing whatever the case before it left behind. */
	all: [] as FieldDescription[]
}));

/* A real registry with its one request replaced, rather than an object shaped like one: the same
   shape `RecordView.svelte.test.ts` uses, and for the same reason: which fields a record draws is
   then answered by the class under test's own rules rather than by a double that drifts. */
vi.mock('$lib/entity/records.svelte', async (importOriginal) => {
	const real = await importOriginal<typeof import('$lib/entity/records.svelte')>();
	class Standing extends real.Fields {
		override of(): FieldDescription[] {
			return mocks.all;
		}
	}
	return { ...real, fields: new Standing() };
});

import RecordGrid from './RecordGrid.svelte';

function field(over: Partial<FieldDescription> = {}): FieldDescription {
	return {
		key: 'container',
		subject: 'asset',
		label: 'Container',
		kind: 'text',
		shown: 'record',
		group: 'record',
		editable: true,
		links_to: null,
		help: null,
		imported: false,
		suggests: null,
		entry: null,
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
	mocks.all = [];
});

function draw(values: Record<string, unknown>, props: Record<string, unknown> = {}) {
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(RecordGrid, {
		target: host,
		props: { subject: 'asset', values, label: 'File Info', ...props }
	}) as Record<string, unknown>;
	flushSync();
}

/** Every row on screen, by its label. */
function labels(): string[] {
	return [...host.querySelectorAll('dt')].map((one) => one.textContent?.trim() ?? '');
}

describe('only what the file actually answers', () => {
	it('leaves out a field with nothing in it', () => {
		mocks.all = [
			field({ key: 'container', label: 'Container' }),
			field({ key: 'title', label: 'Title' })
		];

		draw({ container: 'mp4', title: null }, { onlyFilled: true });

		expect(labels()).toEqual(['Container']);
	});

	it('draws every field, empty ones included, when it is not asked to', () => {
		/* The other half of the branch, so a test that passes because nothing renders at all cannot
		   be mistaken for this one working. This is what every entity record does. */
		mocks.all = [
			field({ key: 'container', label: 'Container' }),
			field({ key: 'title', label: 'Title' })
		];

		draw({ container: 'mp4', title: null });

		expect(labels()).toEqual(['Container', 'Title']);
	});

	it('puts every field back the moment the cells are boxes', () => {
		/* The half that is invisible when it breaks: a field somebody cannot see is a field they
		   cannot fill in, so a form that hid the empty ones could never be used to fill one. */
		mocks.all = [
			field({ key: 'container', label: 'Container' }),
			field({ key: 'title', label: 'Title' })
		];

		draw({ container: 'mp4', title: null }, { onlyFilled: true, editing: true });

		expect(labels()).toEqual(['Container', 'Title']);
	});

	it('counts an empty list and a blank string as nothing, and a nought as something', () => {
		/* A nought is an answer. Dropping it would hide "0 views" and "0 chapters" on exactly the
		   files where that is the fact somebody came to read. */
		mocks.all = [
			field({ key: 'aliases', label: 'Aliases', kind: 'names' }),
			field({ key: 'title', label: 'Title' }),
			field({ key: 'views', label: 'Views', kind: 'number' })
		];

		draw({ aliases: [], title: '   ', views: 0 }, { onlyFilled: true });

		expect(labels()).toEqual(['Views']);
	});

	it('draws nothing at all rather than an empty record', () => {
		mocks.all = [field({ key: 'title', label: 'Title' })];

		draw({ title: null }, { onlyFilled: true });

		expect(host.querySelector('form')).toBeNull();
	});
});

describe('the facts a kind of file actually has', () => {
	/* ffprobe reads a PNG or a JPEG as a one-frame video stream, so a still arrives carrying a
	   video codec and a frame rate of 25: neither of which is true of the photograph. The stored
	   facts stay; the record is what must not repeat them. */
	function measured(): FieldDescription[] {
		return [
			field({ key: 'container', label: 'Container', group: 'media', editable: false }),
			field({ key: 'vcodec', label: 'Video codec', group: 'media', editable: false }),
			field({ key: 'fps', label: 'Frame rate', group: 'media', editable: false })
		];
	}

	it('leaves a still its container and takes away the moving picture facts', () => {
		mocks.all = measured();

		draw(
			{ container: 'jpeg', vcodec: 'mjpeg', fps: 25 },
			{ group: 'media', onlyFilled: true, mediaType: 'image' }
		);

		expect(labels()).toEqual(['Container']);
	});

	it('keeps them for a video', () => {
		mocks.all = measured();

		draw(
			{ container: 'mp4', vcodec: 'h264', fps: 30 },
			{ group: 'media', onlyFilled: true, mediaType: 'video' }
		);

		expect(labels()).toEqual(['Container', 'Video codec', 'Frame rate']);
	});

	it('keeps them for a GIF, which has real frames at a real rate', () => {
		mocks.all = measured();

		draw(
			{ container: 'gif', vcodec: 'gif', fps: 12 },
			{ group: 'media', onlyFilled: true, mediaType: 'gif' }
		);

		expect(labels()).toEqual(['Container', 'Video codec', 'Frame rate']);
	});

	it('keeps every field for a record that is not a file at all', () => {
		/* A person, a Site, a photo set: no media type, so nothing is hidden. A field named `fps`
		   on some other subject is not this rule's business. */
		mocks.all = measured();

		draw({ container: 'jpeg', vcodec: 'mjpeg', fps: 25 }, { group: 'media', onlyFilled: true });

		expect(labels()).toEqual(['Container', 'Video codec', 'Frame rate']);
	});
});

describe('the two halves of a file record', () => {
	/* The split is the REGISTRY's mark and never a list in the component, which is why `group` is on
	   the declaration: a field added next year lands in the half it declares
	   rather than in whichever one a list here happened to hold. */
	function both(): FieldDescription[] {
		return [
			field({ key: 'title', label: 'Title', group: 'record' }),
			field({ key: 'container', label: 'Container', group: 'media', editable: false })
		];
	}

	it('draws only what a person wrote when it is asked for that half', () => {
		mocks.all = both();

		draw({ title: 'A clip', container: 'mp4' }, { group: 'record' });

		expect(labels()).toEqual(['Title']);
	});

	it('draws only what the file measures when it is asked for the other', () => {
		mocks.all = both();

		draw({ title: 'A clip', container: 'mp4' }, { group: 'media' });

		expect(labels()).toEqual(['Container']);
	});

	it('draws both halves when it is asked for neither', () => {
		/* Every record but a file's: a person has one half, and asking that page to name it would be
		   asking it to know about a split that does not apply to it. */
		mocks.all = both();

		draw({ title: 'A clip', container: 'mp4' });

		expect(labels()).toEqual(['Title', 'Container']);
	});

	it('keeps a field whose half this build has never heard of', () => {
		/* An older client meeting a newer server. A group nothing here knows is drawn with the rest
		   of the record, because a field in neither pane is a field that has disappeared. */
		mocks.all = [field({ key: 'mood', label: 'Mood', group: 'whatever-comes-next' })];

		draw({ mood: 'sunny' }, { group: 'record' });

		expect(labels()).toEqual(['Mood']);
	});
});

describe('an empty field that invites one', () => {
	it('offers Add where there is nothing, and names the field out loud', () => {
		/* The point of drawing the empty rows at all: what can be filled in is visible without
		   pressing Edit first. The word alone on screen, the field's name in the announcement:
		   otherwise a screen reader hears the fifth "Add" in a grid of them. */
		mocks.all = [field({ key: 'title', label: 'Title' })];

		draw({ title: null }, { onadd: () => undefined });

		const add = host.querySelector('button');
		expect(add?.textContent?.trim()).toBe('Add');
		expect(add?.getAttribute('aria-label')).toBe('Add Title');
	});

	it('hands back the key that was pressed', () => {
		const asked: string[] = [];
		mocks.all = [field({ key: 'title', label: 'Title' })];

		draw({ title: null }, { onadd: (key: string) => asked.push(key) });
		host.querySelector('button')?.click();
		flushSync();

		expect(asked).toEqual(['title']);
	});

	it('leaves a filled field and an unwritable one alone', () => {
		/* Two things that must not offer it: a value somebody already wrote, and a fact no form can
		   change: an Add against a file size is an invitation nothing could honour. */
		mocks.all = [
			field({ key: 'title', label: 'Title' }),
			field({ key: 'size_bytes', label: 'Size', editable: false })
		];

		draw({ title: 'A clip', size_bytes: null }, { onadd: () => undefined });

		expect(host.querySelector('button[aria-label^="Add"]')).toBeNull();
	});

	it('opens that one field alone, with the cross and the tick inside its box', async () => {
		/* Not the whole form: somebody pressing Add against one field asked to fill in one. The tick
		   saves that one field and sends nothing else, so no stale value is written back. */
		mocks.all = [field({ key: 'title', label: 'Title' }), field({ key: 'music', label: 'Music' })];
		const saved: Record<string, unknown>[] = [];
		draw(
			{ title: 'A clip', music: null },
			{ onadd: () => undefined, onsave: async (next: Record<string, unknown>) => saved.push(next) }
		);

		(host.querySelector('button[aria-label="Add Music"]') as HTMLButtonElement).click();
		flushSync();
		await tick();

		const box = host.querySelector('.edit-marks input') as HTMLInputElement;
		expect(box).not.toBeNull();
		expect(host.querySelectorAll('input')).toHaveLength(1);
		expect(document.activeElement).toBe(box);
		box.value = 'A tune';
		box.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		(host.querySelector('button[aria-label="Save Music"]') as HTMLButtonElement).click();
		await vi.waitFor(() => expect(saved).toHaveLength(1));
		expect(saved[0]).toEqual({ music: 'A tune' });
		await vi.waitFor(() => expect(host.querySelector('.edit-marks')).toBeNull());
	});

	it('leaves the field as it was on the cross', async () => {
		mocks.all = [field({ key: 'music', label: 'Music' })];
		const saved: unknown[] = [];
		draw(
			{ music: null },
			{ onadd: () => undefined, onsave: async (next: unknown) => saved.push(next) }
		);
		(host.querySelector('button[aria-label="Add Music"]') as HTMLButtonElement).click();
		flushSync();
		(
			host.querySelector('button[aria-label="Keep what was in Music"]') as HTMLButtonElement
		).click();
		flushSync();
		expect(host.querySelector('.edit-marks')).toBeNull();
		expect(saved).toHaveLength(0);
	});

	it('copies a value that reads as words by pressing it, through the one copy helper', () => {
		mocks.all = [field({ key: 'title', label: 'Title' })];
		draw({ title: 'A clip' });
		const press = host.querySelector('dd .copyable') as HTMLElement | null;
		expect(press?.textContent?.trim()).toBe('A clip');
	});

	it('puts the cursor in the box the press asked for', () => {
		/* Half a promise otherwise: somebody presses Add on the third field and the form opens with
		   nothing focused, which is the same number of presses as Edit and worse. */
		mocks.all = [field({ key: 'title', label: 'Title' }), field({ key: 'music', label: 'Music' })];

		draw({ title: null, music: null }, { editing: true, focusField: 'music' });

		const box = document.activeElement as HTMLElement | null;
		expect(box?.id.endsWith('-music-box')).toBe(true);
	});
});

/*
 * A LINE UNDER ONE FIELD'S VALUE, drawn by the caller: where a file's song name came from, under
 * its Music. Inside that field's own cell, so it reads as about that value and not the record; and
 * only while the record is READ, because under a box it would describe a value no longer on screen.
 */
describe('a line under one field', () => {
	const under = createRawSnippet<[FieldDescription]>((one) => ({
		render: () =>
			one().key === 'music' ? '<p class="under-line">Named from AcoustID</p>' : '<i></i>'
	}));

	it("draws the caller's line inside that field's own cell", () => {
		mocks.all = [field({ key: 'title', label: 'Title' }), field({ key: 'music', label: 'Music' })];

		draw({ title: 'A clip', music: 'A song' }, { under });

		const line = host.querySelector('.under-line');
		expect(line?.textContent).toBe('Named from AcoustID');
		expect(line?.closest('.fact')?.querySelector('dt')?.textContent?.trim()).toBe('Music');
	});

	it('draws no line while the cells are boxes', () => {
		mocks.all = [field({ key: 'music', label: 'Music' })];

		draw({ music: 'A song' }, { under, editing: true });

		expect(host.querySelector('.under-line')).toBeNull();
	});
});
