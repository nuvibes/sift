/* The tags on one person, or one site, and the answer that arrives after you have left the page.
 *
 * The generation counter is the whole reason this is a store rather than a fetch inside a
 * component. A detail page navigated away from has a request in the air, and its answer lands
 * after the next page has asked for its own. So the previous person's tags appear under this
 * person's name, and taking one off there removes a tag from somebody else.
 *
 * `tagMany` is here for the opposite reason: it is deliberately NOT on the store, because every
 * write to the store replaces the whole set with the server's answer, and a bulk write through it
 * would leave the chips of whichever row answered last standing under somebody else's name.
 */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));

vi.mock('$lib/api/client', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/client')>()),
	api: { get: mocks.get, post: mocks.post }
}));

import { entityTags, tagMany } from './entity-tags.svelte';
import type { components } from '$lib/api/schema';

/* Typed, because the two differ on `color` and the second one is the interesting shape: inferred
   from the first, `null` is not assignable and the whole file stops type-checking. */
type Chip = components['schemas']['TagOnEntity'];

/* A colour NAME rather than a hex value. Every colour in this client comes from a token, and the
   gate that says so reads test files too, correctly, since a fixture carrying a raw hex is where
   the next one gets copied from. */
const RED: Chip = { id: 't-1', name: 'Red' };
const BLUE: Chip = { id: 't-2', name: 'Blue' };

/** A promise this test decides when to settle, so two requests can be answered out of order. */
function held<T>(): { promise: Promise<T>; settle: (value: T) => void } {
	let settle!: (value: T) => void;
	const promise = new Promise<T>((resolve) => (settle = resolve));
	return { promise, settle };
}

beforeEach(() => {
	vi.clearAllMocks();
	entityTags.forget();
});

afterEach(() => {
	entityTags.forget();
});

it('asks the endpoint for the kind, because the two are two addresses', async () => {
	// Not a flag the server reads. `/people/{id}/tags` and `/sites/{id}/tags` share only the
	// shape of the answer.
	mocks.get.mockResolvedValue([RED]);

	await entityTags.load('sites', 's-1');

	expect(mocks.get).toHaveBeenCalledWith('/sites/s-1/tags');
	expect(entityTags.items).toEqual([RED]);
});

it('draws nothing rather than refusing the page when the tags cannot be read', async () => {
	// A screen that will not draw because a secondary fetch failed is worse than one missing a row
	// of chips. The rest of the record is still readable.
	mocks.get.mockResolvedValue([RED]);
	await entityTags.load('people', 'p-1');
	mocks.get.mockRejectedValue(new Error('no'));

	await entityTags.load('people', 'p-2');

	expect(entityTags.items).toEqual([]);
});

it('ignores an answer for a page that has already been left', async () => {
	// The fault this store exists for. Without the generation, the first person's tags land under
	// the second person's name and taking one off there edits somebody else.
	const first = held<Chip[]>();
	const second = held<Chip[]>();
	mocks.get.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);

	const one = entityTags.load('people', 'p-1');
	const two = entityTags.load('people', 'p-2');
	second.settle([BLUE]);
	await two;
	first.settle([RED]);
	await one;

	expect(entityTags.items).toEqual([BLUE]);
});

it('ignores a FAILED answer for a page that has already been left', async () => {
	// The refusal path needs the same guard as the success path, and it is the one that gets
	// forgotten: an old request failing would otherwise blank the chips of the page now on screen.
	const first = held<Chip[]>();
	let refuse!: (why: unknown) => void;
	mocks.get
		.mockReturnValueOnce(new Promise((_, reject) => (refuse = reject)))
		.mockReturnValueOnce(first.promise);

	const one = entityTags.load('people', 'p-1');
	const two = entityTags.load('people', 'p-2');
	first.settle([BLUE]);
	await two;
	refuse(new Error('no'));
	await one;

	expect(entityTags.items).toEqual([BLUE]);
});

it('keeps the whole set the server answers with rather than patching its own copy', async () => {
	// A client that added the chip itself would drift from what was really stored, and the drift
	// only shows up on the next page load.
	mocks.post.mockResolvedValue([RED, BLUE]);

	await entityTags.set('people', 'p-1', 't-2', true);

	expect(mocks.post).toHaveBeenCalledWith('/people/p-1/tags', {
		body: { tag_id: 't-2', add: true }
	});
	expect(entityTags.items).toEqual([RED, BLUE]);
});

it('ignores the answer to a write for a page that has already been left', async () => {
	const write = held<Chip[]>();
	const read = held<Chip[]>();
	mocks.post.mockReturnValueOnce(write.promise);
	mocks.get.mockReturnValueOnce(read.promise);

	const writing = entityTags.set('people', 'p-1', 't-1', true);
	const reading = entityTags.load('people', 'p-2');
	read.settle([BLUE]);
	await reading;
	write.settle([RED]);
	await writing;

	expect(entityTags.items).toEqual([BLUE]);
});

it('forgets what it holds between two pages', async () => {
	mocks.get.mockResolvedValue([RED]);
	await entityTags.load('people', 'p-1');

	entityTags.forget();

	expect(entityTags.items).toEqual([]);
});

it('makes an answer already in the air worthless when it forgets', async () => {
	// Forgetting is what a page does on the way out, and an answer that arrived a moment later
	// would otherwise put the last page's chips back on an empty screen.
	const first = held<Chip[]>();
	mocks.get.mockReturnValueOnce(first.promise);

	const one = entityTags.load('people', 'p-1');
	entityTags.forget();
	first.settle([RED]);
	await one;

	expect(entityTags.items).toEqual([]);
});

it('tags several things one request at a time, because the endpoint is per thing', async () => {
	mocks.post.mockResolvedValue([]);

	await tagMany('people', ['p-1', 'p-2'], ['t-1', 't-2']);

	expect(mocks.post).toHaveBeenCalledTimes(4);
	expect(mocks.post).toHaveBeenCalledWith('/people/p-1/tags', {
		body: { tag_id: 't-1', add: true }
	});
	expect(mocks.post).toHaveBeenCalledWith('/people/p-2/tags', {
		body: { tag_id: 't-2', add: true }
	});
});

it('takes a tag off several things when told to', async () => {
	mocks.post.mockResolvedValue([]);

	await tagMany('sites', ['s-1'], ['t-1'], false);

	expect(mocks.post).toHaveBeenCalledWith('/sites/s-1/tags', {
		body: { tag_id: 't-1', add: false }
	});
});

it('fails the whole call rather than reporting a partial tagging', async () => {
	// A partial write that does not say which part worked is worse than a refusal: nothing on
	// screen is a copy of this, so there is nowhere for a half-answer to be shown.
	mocks.post.mockResolvedValueOnce([]).mockRejectedValueOnce(new Error('no'));

	await expect(tagMany('people', ['p-1', 'p-2'], ['t-1'])).rejects.toThrow();
});

it('leaves the chips of the page on screen alone, because a bulk write skips the store', async () => {
	mocks.get.mockResolvedValue([RED]);
	await entityTags.load('people', 'p-1');
	mocks.post.mockResolvedValue([BLUE]);

	await tagMany('people', ['p-2'], ['t-2']);

	expect(entityTags.items).toEqual([RED]);
});
