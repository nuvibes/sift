import { describe, expect, it } from 'vitest';

import { HELD_AT_ZERO, offences, startsWithAVerb } from '../../../scripts/lib/vocabulary.js';

/* A verb with a scope opens a label only on its own screens: the swap's Start, Join, End and
 * They match, and Take where another source's offer is accepted. */
describe('a scoped verb belongs to its own screens', () => {
	it('opens a label on the screens it is scoped to', () => {
		expect(startsWithAVerb('End the swap', 'lib/components/swap/SwapProgress.svelte')).toBe(true);
		expect(startsWithAVerb('They match', 'lib/components/swap/SwapCode.svelte')).toBe(true);
		expect(startsWithAVerb('Join a swap', 'routes/swap/+page.svelte')).toBe(true);
		expect(startsWithAVerb('Take these', 'lib/components/swap/OfferScreen.svelte')).toBe(true);
		expect(startsWithAVerb('Take theirs', 'lib/components/organize/ReconcilePanel.svelte')).toBe(
			true
		);
	});

	it('is counted anywhere else, and where no file is named', () => {
		expect(startsWithAVerb('Start again', 'lib/components/AssetDetailControls.svelte')).toBe(false);
		expect(
			startsWithAVerb('Take this off', 'lib/components/organize/DisagreementsPanel.svelte')
		).toBe(false);
		expect(startsWithAVerb('End the task', 'lib/jobs/JobsScreen.svelte')).toBe(false);
		expect(startsWithAVerb('Take theirs')).toBe(false);
	});

	it('leaves an unscoped verb open everywhere', () => {
		expect(startsWithAVerb('Remove this file', 'lib/jobs/JobsScreen.svelte')).toBe(true);
		expect(startsWithAVerb('Remove this file')).toBe(true);
	});
});

/* A name keeps its capital wherever it stands in a sentence, held at zero over the client's
 * copy. */
describe('a name written without its capital', () => {
	it('is held at zero', () => {
		expect(HELD_AT_ZERO).toContain('lower_case_names');
	});

	it('is found in a sentence, at its start and before a mark', () => {
		const found = (text: string) =>
			offences('lower_case_names', text).map((one: { found: string }) => one.found);
		expect(found("That site couldn't be loaded.")).toEqual(['site']);
		expect(found('site mark, and sites, and the site.')).toEqual(['site', 'sites', 'site']);
		expect(found('Add it to a photo set')).toEqual(['photo set']);
	});

	it('leaves the name, a key and a template word alone', () => {
		for (const text of [
			'That Site',
			'Sites within',
			'site',
			'sites',
			'{site}',
			'/sites/x',
			'website'
		]) {
			expect(offences('lower_case_names', text), text).toEqual([]);
		}
	});
});
