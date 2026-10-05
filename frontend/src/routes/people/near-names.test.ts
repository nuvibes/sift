import { describe, expect, it } from 'vitest';
import { likeness, NEAR_ENOUGH, nearNames } from './near-names';

const people = [
	{ name: 'Ada Lumen' },
	{ name: 'Marisol Quent' },
	{ name: 'Hollowgrain' },
	{ name: 'Brisa Tolland' }
].map((one, at) => ({ ...one, id: `p${at}` }));

describe('who a name nobody answers to might be', () => {
	it('offers a near spelling', () => {
		expect(nearNames(people, 'ada lumin').map((one) => one.name)).toEqual(['Ada Lumen']);
	});

	it('offers nobody for a name that only shares its first two letters', () => {
		expect(likeness('Marisol Quent', 'Maxwell Orrin')).toBeLessThan(NEAR_ENOUGH);
		expect(nearNames(people, 'Maxwell Orrin')).toEqual([]);
	});

	it('reads digits as the letters they stand in for', () => {
		expect(nearNames(people, 'h0ll0w').map((one) => one.name)).toEqual(['Hollowgrain']);
	});

	it('offers nobody for nothing typed', () => {
		expect(nearNames(people, '  ')).toEqual([]);
	});
});
