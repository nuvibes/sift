/* The panel beside the name: the fields the server put behind the switch, and nothing else.
 *
 * The rule worth a test is the split. A record has two placements (what sits under the heading
 * always, and what is here) and which field is in which is the SERVER's word, read from the
 * registry. Drawing the whole registry here would repeat the names and the links two inches under
 * themselves, and no screen would be wrong on its own: each would be drawing everything it was
 * given. So the assertion is a negative one, and it is the reason this component takes a registry
 * rather than a list of rows.
 *
 * The second is that an empty field is still a row. A record that hides what nobody has filled in
 * changes shape as it is edited, and there is then nowhere to see what COULD be filled in.
 */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { FieldDescription } from '$lib/entity/records.svelte';
import { flushSync, mount, unmount } from 'svelte';

const mocks = vi.hoisted(() => ({
	/* The registry, stood in for. The real one is one shared instance that memoises a request for
	   the session, so a component test using it would be testing whatever the case before it left
	   behind, and what is under test here is what the component does with an answer, not how the
	   answer is fetched. That is `records.svelte.test.ts`. */
	all: [] as FieldDescription[]
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

import RecordView from './RecordView.svelte';

function field(over: Partial<FieldDescription> = {}): FieldDescription {
	return {
		key: 'details',
		subject: 'person',
		label: 'Details',
		kind: 'paragraph',
		shown: 'more',
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

function draw(values: Record<string, unknown>, showing?: 'more' | 'all'): HTMLElement {
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(RecordView, {
		target: host,
		props: { subject: 'person', values, label: 'About Jane', ...(showing ? { showing } : {}) }
	}) as Record<string, unknown>;
	flushSync();
	return host;
}

it('draws only what the server put behind the switch', () => {
	mocks.all = [
		field({ key: 'aliases', label: 'Aliases', kind: 'names', shown: 'record' }),
		field({ key: 'details', label: 'Details', shown: 'more' })
	];

	draw({ aliases: ['Janet'], details: 'a note' });

	const labels = [...host.querySelectorAll('dt')].map((one) => one.textContent);
	expect(labels).toEqual(['Details']);
	// The names are under the heading already. Drawn here too they are on screen twice.
	expect(host.textContent).not.toContain('Janet');
});

it('draws every field when there is no summary above it', () => {
	mocks.all = [
		field({ key: 'aliases', label: 'Aliases', kind: 'names', shown: 'record' }),
		field({ key: 'details', label: 'Details', shown: 'more' })
	];

	draw({ aliases: ['Janet'], details: 'a note' }, 'all');

	expect([...host.querySelectorAll('dt')].map((one) => one.textContent)).toEqual([
		'Aliases',
		'Details'
	]);
});

it('keeps a row for a field nobody has filled in', () => {
	mocks.all = [field()];

	draw({});

	expect(host.querySelector('dt')?.textContent).toBe('Details');
	expect(host.querySelector('dd')?.textContent?.trim()).toBe('—');
});

it('names the record for anybody who cannot see whose it is', () => {
	mocks.all = [field()];

	draw({ details: 'a note' });

	expect(host.querySelector('dl')?.getAttribute('aria-label')).toBe('About Jane');
});

it('draws nothing at all rather than an empty panel', () => {
	mocks.all = [field({ shown: 'record' })];

	draw({ details: 'a note' });

	expect(host.querySelector('dl')).toBeNull();
	expect(host.textContent?.trim()).toBe('');
});

it('keeps the registry order rather than sorting the rows', () => {
	// Alphabetical rows read as a dump of a table. The order is somebody's, and it is the server's.
	mocks.all = [field({ key: 'zeta', label: 'Zeta' }), field({ key: 'alpha', label: 'Alpha' })];

	draw({ zeta: 'z', alpha: 'a' });

	expect([...host.querySelectorAll('dt')].map((one) => one.textContent)).toEqual(['Zeta', 'Alpha']);
});
