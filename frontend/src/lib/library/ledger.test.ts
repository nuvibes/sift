/* THE RECORD'S TWO FILTERS, read back. */
import { describe, expect, it } from 'vitest';

import * as ledger from './ledger';
import { ANY, KINDS, VERBS, kindChoices, verbChoices } from './ledger';

describe('the filter row is the vocabulary itself', () => {
	it('offers every act, and offers asking for all of them first', () => {
		const choices = verbChoices();
		expect(choices[0]).toEqual({ value: ANY, label: 'Everything' });
		expect(choices.slice(1).map((one) => one.value)).toEqual(Object.keys(VERBS));
	});

	it('offers every kind of thing, named the way its wall names it', () => {
		const choices = kindChoices();
		expect(choices[0]).toEqual({ value: ANY, label: 'Everything' });
		expect(choices.slice(1).map((one) => one.label)).toEqual(
			Object.values(KINDS).map((one) => one.many)
		);
	});

	it('gives every act a name somebody could choose', () => {
		for (const act of Object.values(VERBS)) expect(act.label.length).toBeGreaterThan(0);
	});

	it('says the acts in the History word table', () => {
		const labels = Object.values(VERBS).map((one) => one.label);
		expect(labels).toContain('Unhidden');
		expect(labels).toContain('Stash-box lookups turned off');
		expect(labels.join(' ')).not.toMatch(/Unlinked|No longer hidden|Kept local|Allowed enrichment/);
	});
});

describe('the browser builds no line of its own', () => {
	it('exports no sentence, no actor words and no word for a missing name', () => {
		const exported = Object.keys(ledger);
		for (const retired of ['sentence', 'actorWords', 'nameOf', 'byDay', 'dayOf', 'timeOf']) {
			expect(exported).not.toContain(retired);
		}
	});
});
