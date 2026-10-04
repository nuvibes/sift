import { describe, expect, it } from 'vitest';
import { directContents } from './direct';

/* The fault this guards: a folder holding five folders and no loose files would draw a wall of
   thousands of tiles from its grandchildren, because `in:` means the whole subtree. */

describe('directContents', () => {
	it('asks for the folder and not what is under it', () => {
		expect(directContents({ in: 'parent' })).toEqual({ in: 'parent', depth: 'direct' });
	});

	it('needs nothing but the folder itself', () => {
		/* The folder's own id is the whole of the ask: it is in the address already. Needing
		   every child's id would mean needing the tree: the wall asking for the subtree once and
		   for the folder again when the tree landed, and giving up above a cap of children and
		   showing the subtree for ever. */
		expect(Object.keys(directContents({ in: 'parent' })).sort()).toEqual(['depth', 'in']);
	});

	it("never writes into `q`, where somebody's own words live", () => {
		/* Not as `-in:<id>` terms in `q`: the wall would then believe it was searching: the
		   default order and the "Closest match" option both turn on whether `q` holds anything,
		   so walking into a folder would re-order the wall by relevance to an exclusion nobody
		   typed. */
		expect(directContents({ in: 'parent' }).q).toBeUndefined();
	});

	it('leaves a SEARCH alone: looking for something in a folder looks under it', () => {
		const asked = { in: 'parent', q: 'sunset' };
		expect(directContents(asked)).toEqual(asked);
	});

	it('treats a blank query as no query', () => {
		expect(directContents({ in: 'parent', q: '  ' })).toEqual({
			in: 'parent',
			q: '  ',
			depth: 'direct'
		});
	});

	it('does not write into what it was given', () => {
		const asked = { in: 'parent' };
		directContents(asked);
		expect(asked).toEqual({ in: 'parent' });
	});
});
