/* The gates build patterns out of the code they read, so a name or an expression goes into a
 * pattern escaped whole: `$` and `+` are characters of the code there, never pattern syntax. */

import { describe, expect, it } from 'vitest';

import { menuFaultsIn } from '../../../scripts/lib/menu-groups.js';
import { rawCounts } from '../../../scripts/lib/vocabulary.js';

describe('a name or expression read out of code', () => {
	it('finds every push to a verb list whose name holds two dollar signs', () => {
		const code = [
			'const more$of$them: Verb[] = [];',
			...Array.from(
				{ length: 3 },
				(_, at) => `more$of$them.push({ id: 'v${at}', label: 'V${at}', icon: 'add', run });`
			)
		].join('\n');

		expect(menuFaultsIn(code, {}).map((one: { what: string }) => one.what)).toEqual([
			'3 verbs in `more$of$them`, 3 with no group'
		]);
	});

	it('finds a count whose word asks about the same sum', () => {
		const found = rawCounts("`${kept + gone} ${kept + gone === 1 ? 'file' : 'files'}`");

		expect(found.map((one) => one.found)).toEqual([
			"${kept + gone} ${kept + gone === 1 ? 'file' : 'files'}"
		]);
	});
});
