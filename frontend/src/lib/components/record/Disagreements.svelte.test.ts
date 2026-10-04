/*
 * Where a stash-box disagrees with this record, drawn on the record.
 *
 * Asked on the person's page rather than on a queue of unrelated fields, because "1990 or 1991" is
 * a coin toss away from the person and a question somebody can answer on their page.
 *
 * Two things here could not be checked by reading it. The count comes from the panel rather than a
 * second request, because a second reader is a second count free to disagree with the list under
 * it. And a record with nothing to say takes no space: "nothing disagrees" at the top of every
 * person's history would be a sentence about a feature rather than a fact about the person.
 *
 * It is at the top of the History tab rather than in the page header, whose capped column is too
 * narrow for these cards.
 */
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';
import { words } from '$lib/design/testing.svelte';

const mocks = vi.hoisted(() => ({ disagreementsOf: vi.fn(), settle: vi.fn() }));

vi.mock('$lib/entity/reconcile.svelte', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/entity/reconcile.svelte')>()),
	disagreementsOf: mocks.disagreementsOf,
	settle: mocks.settle
}));

import Disagreements from './Disagreements.svelte';
import { noServerAt } from '../../../test-setup';

/* Left unanswered on purpose: the field registry and the settings behind it, which this file does not draw. */
noServerAt('/api/records/fields', '/api/settings');

function row(over: Record<string, unknown> = {}) {
	return {
		subject: 'person',
		local_id: 'p-1',
		name: 'Neve Arbogast',
		box_id: 'box-1',
		box_name: 'StashDB',
		key: 'birth_date',
		mine: '1990-01-01',
		theirs: '1991-02-02',
		...over
	};
}

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

beforeEach(() => {
	vi.clearAllMocks();
	mocks.disagreementsOf.mockResolvedValue([]);
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
	document.body.innerHTML = '';
});

async function draw(): Promise<void> {
	drawn = mount(Disagreements, {
		target: host,
		props: { subject: 'person', localId: 'p-1' }
	}) as Record<string, unknown>;
	flushSync();
	await tick();
	await tick();
	flushSync();
}

it('says how many, from the count the panel itself found', async () => {
	mocks.disagreementsOf.mockResolvedValue([row(), row({ key: 'country' })]);

	await draw();

	expect(words(host)).toContain('2 fields StashDB disagrees with');
});

it('counts one in the singular', async () => {
	mocks.disagreementsOf.mockResolvedValue([row()]);

	await draw();

	expect(words(host)).toContain('One field StashDB disagrees with');
});

it('passes the count on, so the mark beside the tab can come down with the last row', async () => {
	// The strip's own request answers this number too. This one is the fresher of the two while
	// somebody is looking at the tab, so settling the last row takes the mark away rather than
	// leaving it standing over an empty panel until the whole strip is asked again.
	mocks.disagreementsOf.mockResolvedValue([row()]);
	const told = vi.fn();

	drawn = mount(Disagreements, {
		target: host,
		props: { subject: 'person', localId: 'p-1', onchange: told }
	}) as Record<string, unknown>;
	flushSync();
	await tick();
	await tick();
	flushSync();

	// With the box by name, so the mark beside the tab says which box it means.
	expect(told).toHaveBeenCalledWith(1, ['StashDB']);
});

it('says nothing at all on a record with nothing to say', async () => {
	/* The ordinary case: most records are never linked to a box. */
	await draw();

	expect(words(host)).toBe('');
});
