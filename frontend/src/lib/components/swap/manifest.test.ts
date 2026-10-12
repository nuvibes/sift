/* The Manifest's words: the order Sift files things in, the after number, the held row's two
 * reasons, the time at a measured rate, and the waiting screens' lines. */

import { describe, expect, it } from 'vitest';
import {
	afterNumber,
	deviceWords,
	elapsedWords,
	fileFacts,
	goodForWords,
	groupContext,
	haveWords,
	heldWords,
	inFilingOrder,
	kindsWords,
	leftOutWords,
	learnsCounts,
	oneThing,
	pillOf,
	rateWords,
	reachingWords,
	rejoinWords,
	retryWords,
	secondsTo,
	stateOf,
	swappedBeforeWords,
	throughWords
} from './manifest';
import type { ManifestGroup } from './wire';

function group(over: Partial<ManifestGroup> = {}): ManifestGroup {
	return {
		key: 'person:0',
		kind: 'person',
		index: 0,
		name: 'Ava Example',
		match_name: null,
		matched_by: null,
		local_id: null,
		usernames: 0,
		faces: 0,
		files: 612,
		bytes: 17_900_000_000,
		have: null,
		to_take: 612,
		held_same: 0,
		held_look: 0,
		state: 'offered',
		done: 0,
		failed: 0,
		crossing: 0,
		left_out: null,
		sample: [],
		more: 0,
		...over
	};
}

describe('the manifest', () => {
	it('lists groups in the order Sift files things', () => {
		const rows = inFilingOrder([
			group({ key: 'loose', kind: 'loose', name: null }),
			group({ key: 'site:0', kind: 'site', name: 'Northlight Media' }),
			group({ key: 'songs', kind: 'songs', name: null }),
			group({ key: 'person:0' })
		]);
		expect(rows.map((one) => one.key)).toEqual(['person:0', 'site:0', 'songs', 'loose']);
	});

	it('says the after number as what is here now and once taken', () => {
		const ava = group({ have: 203, to_take: 409 });
		expect(afterNumber(ava, true)).toBe('203 \u2192 612');
		expect(afterNumber(ava, false)).toBe('203 \u2192 203');
		expect(haveWords(ava)).toBe('You have 203 \u00b7 409 to take');
		// The host is never told what the guest holds.
		expect(afterNumber(group(), true)).toBeNull();
	});

	it('says the match, the Usernames and the faces under a name', () => {
		expect(
			groupContext(group({ match_name: 'Ava Example', matched_by: 'alias', have: 3, faces: 14 }))
		).toBe('Matches your Ava Example, by another name \u00b7 14 faces');
		expect(groupContext(group({ have: 0 }))).toBe('New to your library');
		expect(groupContext(group({ kind: 'site', usernames: 6 }))).toBe('Site \u00b7 6 Usernames');
	});

	it('keeps a row that will not go, with its mark', () => {
		const kept = group({ name: 'Orla Tennant', left_out: 'local', files: 1200 });
		expect(leftOutWords(kept)).toBe('Kept local \u00b7 1,200 files not offered');
		expect(stateOf(kept, true)).toBe('left_out');
		expect(pillOf('left_out', kept)).toEqual({ tone: 'failed', word: 'Kept local' });
	});

	it('takes the tick before Take is pressed, and the server after', () => {
		expect(stateOf(group(), true)).toBe('taking');
		expect(stateOf(group(), false)).toBe('skipped');
		expect(stateOf(group({ to_take: 0 }), true)).toBe('held');
		expect(stateOf(group({ state: 'landed' }), null)).toBe('landed');
		expect(pillOf('crossing', group({ done: 3, to_take: 308 })).word).toBe('3 of 308');
	});

	it('splits the held row by its two reasons', () => {
		expect(heldWords(32, 171)).toBe(
			"You already have 203 of these, so they aren't sent: 32 the very same files and 171 that look the same."
		);
		expect(heldWords(0, 0)).toBeNull();
	});

	it('collapses a swap of one thing to a sentence', () => {
		expect(oneThing([group()])?.key).toBe('person:0');
		expect(oneThing([group(), group({ key: 'person:1' })])).toBeNull();
		expect(oneThing([group({ left_out: 'swap' })])).toBeNull();
	});

	it('counts the kinds going', () => {
		expect(
			kindsWords([
				group(),
				group({ key: 'person:1' }),
				group({ key: 'person:2', left_out: 'local' }),
				group({ key: 'site:0', kind: 'site' })
			])
		).toBe('2 People, 1 Site');
	});

	it('times a size at the tunnel rate, and says nothing without one', () => {
		expect(throughWords(38_200_000_000, 200_000_000, 'NY-715')).toBe(
			'about 15 to 30 minutes through NY-715'
		);
		expect(throughWords(38_200_000_000, null, 'NY-715')).toBeNull();
	});

	it('says what the other side learns, by count', () => {
		expect(learnsCounts({ people: 3, aliases: 2, sites: 1, usernames: 6, faces: 23 })).toBe(
			'3 names \u00b7 2 aliases \u00b7 1 Site \u00b7 6 Usernames \u00b7 23 faces'
		);
		expect(learnsCounts(undefined)).toBeNull();
	});

	it('words the waiting lines', () => {
		expect(elapsedWords(1000, 1000 + 9 * 60 + 5)).toBe('9 min in');
		expect(elapsedWords(1000, 1030)).toBe('Under a minute in');
		expect(goodForWords(1000 + 23 * 3600 + 50 * 60, 1000)).toBe(
			'The token is good for another 23 h 50 min.'
		);
		expect(goodForWords(900, 1000)).toBe('The token has run out.');
		expect(goodForWords(1030, 1000)).toBe('The token is about to run out.');
		expect(swappedBeforeWords(2)).toBe("You've swapped with this device 2 times before.");
		expect(swappedBeforeWords(0)).toBe("You haven't swapped with this device before.");
		expect(swappedBeforeWords(null)).toBeNull();
		expect(deviceWords('DESK-2F9A', 'abcd1234efgh')).toBe('DESK-2F9A (ABCD-1234-EFGH)');
		expect(reachingWords('DESK-2F9A', 'NY-711')).toBe('Reaching DESK-2F9A through NY-711');
		expect(reachingWords(null, null)).toBe('Reaching them');
		expect(secondsTo(1015, 1000.2)).toBe(15);
		expect(retryWords(15)).toBe('Trying again after 15 s');
		expect(retryWords(0)).toBe('Trying again');
		expect(rejoinWords(1000 + 14 * 60 + 20, 1000, 'host')).toBe(
			'They can rejoin for another 14 min.'
		);
		expect(rejoinWords(1030, 1000, 'guest')).toBe('Sift keeps trying for under a minute.');
		expect(rejoinWords(900, 1000, 'guest')).toBeNull();
	});

	it('words the rate and a file', () => {
		expect(rateWords(192_800_000, 16)).toBe('24 MB/s on 16 streams');
		expect(rateWords(0, 16)).toBeNull();
		expect(
			fileFacts({
				key: 'f1',
				title: 'beach set 03.mp4',
				kind: 'video',
				size: 1_200_000_000,
				duration_ms: 247_000,
				width: 1920,
				height: 1080,
				held: null
			})
		).toBe('video \u00b7 1.2 GB \u00b7 4:07');
	});
});
