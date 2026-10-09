/* The field registry, and the one thing about it that fails silently. */

import { beforeEach, expect, it, vi } from 'vitest';

import { api } from '$lib/api/client';
import { Fields } from '$lib/entity/records.svelte';

vi.mock('$lib/api/client', () => ({
	api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() }
}));

/* The preference lives behind its own call. Mocked to "off" by default, which is what a fresh
   install has, so a case that says nothing about it is testing the ordinary screen. */
const settings = vi.hoisted(() => ({ values: vi.fn() }));
vi.mock('$lib/settings-ui/settings', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/settings-ui/settings')>()),
	fetchSettingValues: settings.values
}));

const mocked = vi.mocked(api);

const ANSWER = {
	subjects: {
		person: [
			{
				key: 'aliases',
				subject: 'person',
				label: 'Aliases',
				kind: 'names',
				shown: 'record',
				editable: true,
				help: null
			},
			{
				key: 'details',
				subject: 'person',
				label: 'Details',
				kind: 'paragraph',
				shown: 'more',
				editable: true,
				help: null
			},
			{
				key: 'eye_color',
				subject: 'person',
				label: 'Eye color',
				kind: 'word',
				shown: 'every',
				editable: true,
				help: null
			}
		]
	}
};

/* A fresh one per test. The app shares a single registry and memoises the one request for the
   session, so a case that reused it would be testing whatever the case before it left behind. */
let fields: Fields;

beforeEach(() => {
	vi.clearAllMocks();
	settings.values.mockResolvedValue(new Map());
	fields = new Fields();
});

it('asks for itself the first time anything reads it, with no screen having to remember', async () => {
	mocked.get.mockResolvedValue(ANSWER);

	// Nothing has called `load`. This is what a screen does: it reads.
	const first = fields.of('person');

	// Empty for this frame, because the answer is not back yet, and that is the honest reading.
	expect(first).toEqual([]);
	// But the ask went out. Without this line a screen draws an empty form forever and says nothing.
	expect(mocked.get).toHaveBeenCalledWith('/records/fields');
});

it('asks once however many surfaces read it', async () => {
	mocked.get.mockResolvedValue(ANSWER);

	fields.of('person');
	fields.onRecord('person');
	fields.behindMore('person');
	await fields.load();
	fields.of('person');

	expect(mocked.get).toHaveBeenCalledTimes(1);
});

it('splits a subject into what is drawn without asking and what is behind the switch', async () => {
	mocked.get.mockResolvedValue(ANSWER);
	await fields.load();

	expect(fields.onRecord('person').map((one) => one.key)).toEqual(['aliases']);
	expect(fields.behindMore('person').map((one) => one.key)).toEqual(['details']);
	// The two halves together are the whole record, with nothing in both and nothing in neither.
	expect(fields.of('person').map((one) => one.key)).toEqual(['aliases', 'details', 'eye_color']);
});

it('leaves out the fields only a stash-box fills in until they have been asked for', async () => {
	/* The whole reason there are three placements and not two. */
	mocked.get.mockResolvedValue(ANSWER);
	await fields.load();

	expect(fields.showEvery).toBe(false);
	expect(fields.behindMore('person').map((one) => one.key)).toEqual(['details']);
	expect(fields.drawn('person').map((one) => one.key)).toEqual(['aliases', 'details']);
});

it("draws them once the switch is on, in the record's own order rather than in a group", async () => {
	/* Read with the switch on, the record is the record read with it off with rows filled in
	   BETWEEN, not the same record with a block appended. */
	mocked.get.mockResolvedValue(ANSWER);
	settings.values.mockResolvedValue(new Map([['records.show_every_field', true]]));
	await fields.load();

	expect(fields.showEvery).toBe(true);
	expect(fields.behindMore('person').map((one) => one.key)).toEqual(['details', 'eye_color']);
	expect(fields.drawn('person').map((one) => one.key)).toEqual(['aliases', 'details', 'eye_color']);
});

it('keeps the registry when the preference cannot be read', async () => {
	/* Two calls, two separate failures. */
	mocked.get.mockResolvedValue(ANSWER);
	settings.values.mockRejectedValue(new Error('no'));
	await fields.load();

	expect(fields.showEvery).toBe(false);
	expect(fields.of('person')).toHaveLength(3);
});

it('a server that refuses leaves an empty registry and does not ask again every frame', async () => {
	mocked.get.mockRejectedValue(new Error('no'));

	await fields.load();
	fields.of('person');
	fields.of('site');

	expect(fields.of('person')).toEqual([]);
	expect(mocked.get).toHaveBeenCalledTimes(1);
});
