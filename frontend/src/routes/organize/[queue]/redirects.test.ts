/* The addresses the faces queues once lived at. */
import { describe, expect, it } from 'vitest';

import { load as queueLoad } from './+page';
import { load as itemLoad } from './[id]/+page';

interface Thrown {
	status: number;
	location: string;
}

function whereItGoes(run: () => void): Thrown | null {
	try {
		run();
		return null;
	} catch (thrown) {
		return thrown as Thrown;
	}
}

describe('a queue that moved', () => {
	it('sends each old work address to the tab that holds what it held', () => {
		const moves = {
			unidentified: '/organize/faces-to-name',
			ignored: '/organize/discarded-faces',
			'ignored-faces': '/organize/discarded-faces',
			'look-alikes': '/organize/faces',
			'to-check': '/organize/faces'
		};
		for (const [old, now] of Object.entries(moves)) {
			const went = whereItGoes(() =>
				queueLoad({ params: { queue: old }, url: new URL(`http://x/organize/${old}`) })
			);
			expect(went?.status).toBe(308);
			expect(went?.location).toBe(now);
		}
	});

	it('sends the record to the record', () => {
		const went = whereItGoes(() =>
			queueLoad({
				params: { queue: 'identified' },
				url: new URL('http://x/organize/identified')
			})
		);
		expect(went?.location).toBe('/organize/known-people');
	});

	it('keeps the narrowing the link was carrying', () => {
		// A link into one filter is a link to a place, not to a screen.
		const went = whereItGoes(() =>
			queueLoad({
				params: { queue: 'identified' },
				url: new URL('http://x/organize/identified?show=matched')
			})
		);
		expect(went?.location).toBe('/organize/known-people?show=matched');
	});

	it('leaves a queue that never moved alone', () => {
		// The known positive. A redirect that fired for everything would be an endless one.
		expect(
			whereItGoes(() =>
				queueLoad({ params: { queue: 'folders' }, url: new URL('http://x/organize/folders') })
			)
		).toBeNull();
	});
});

describe('one item of a queue that moved', () => {
	it('goes to the same item under the new name', () => {
		const went = whereItGoes(() =>
			itemLoad({
				params: { queue: 'unidentified', id: 'pile-1' },
				url: new URL('http://x/organize/unidentified/pile-1')
			})
		);
		expect(went?.status).toBe(308);
		expect(went?.location).toBe('/organize/faces-to-name/pile-1');
	});

	it('sends a face group of the one list to the tab that opens a pile, not to the lead page', () => {
		// The queue `to-check` goes to the group's first page; an item of it is one pile, and
		// `/organize/faces/<id>` has no item screen.
		const went = whereItGoes(() =>
			itemLoad({
				params: { queue: 'to-check', id: 'pile-2' },
				url: new URL('http://x/organize/to-check/pile-2')
			})
		);
		expect(went?.status).toBe(308);
		expect(went?.location).toBe('/organize/faces-to-name/pile-2');
	});

	it('carries a person to their own screen with the tab they were sent to', () => {
		const went = whereItGoes(() =>
			itemLoad({
				params: { queue: 'identified', id: 'person-1' },
				url: new URL('http://x/organize/identified/person-1?show=suggested')
			})
		);
		expect(went?.location).toBe('/organize/known-people/person-1?show=suggested');
	});

	it('leaves an item of a queue that never moved alone', () => {
		expect(
			whereItGoes(() =>
				itemLoad({
					params: { queue: 'duplicates', id: 'chain-1' },
					url: new URL('http://x/organize/duplicates/chain-1')
				})
			)
		).toBeNull();
	});
});
