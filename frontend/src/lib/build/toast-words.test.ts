/**
 * What `scripts/check_toast_stops.js` reads out of a toast and refuses, driven with source made
 * for it: the messages a call can say, the punctuation rules, and a name said as words.
 */

import { describe, expect, it } from 'vitest';

import {
	clientToasts,
	messagesOf,
	namesAsWords,
	punctuation,
	serverToasts
} from '../../../scripts/lib/toast-words.js';

describe('the toast reader', () => {
	it('reads a literal, each branch of a ternary, a fallback and a list of pieces', () => {
		expect(messagesOf("'Link copied'")).toEqual(['Link copied']);
		expect(messagesOf("n === 1 ? 'One.' : `${n} more.`")).toEqual(['One.', '${n} more.']);
		expect(messagesOf("error?.detail ?? 'That failed.'")).toEqual(['That failed.']);
		expect(messagesOf("['Added to ', thing('tag', id, name), '. Nothing moved.']")).toEqual([
			'Added to ${}. Nothing moved.'
		]);
		expect(messagesOf('message')).toEqual([]);
	});

	it('finds every door a toast goes through, with the argument that is its words', () => {
		const text = [
			"toasts.show('Saved.', { tone: 'success' });",
			"toasts.settle(id, 'Done.', 'success');",
			'decided(`Removed ${n} files`, receipt);',
			"answered.decided('not a toast');"
		].join('\n');

		expect(clientToasts(text).map((one) => one.messages)).toEqual([
			['Saved.'],
			['Done.'],
			['Removed ${n} files']
		]);
	});

	it('refuses a stop on one sentence, a semicolon, a comma splice and a stray space', () => {
		expect(punctuation('Link copied.')).toEqual(['a full stop on one sentence']);
		expect(punctuation('Filled in; the rest waits')).toEqual(['a semicolon']);
		expect(punctuation('That was saved, it is in Activity')).toEqual([
			'a comma splicing two sentences'
		]);
		expect(punctuation("${n} deleted, ${m} couldn't be")).toEqual([
			'a comma splicing two sentences'
		]);
		expect(punctuation('Saved as "x" , in the panel')).toEqual(['a stray space']);
	});

	it('passes what reads right', () => {
		for (const fine of [
			'Link copied',
			'Downloading...',
			'Downloaded. Drag it in from your downloads.',
			"Kept local, so it isn't offered",
			'Added ${n} files to ${}. Nothing moved.'
		])
			expect(punctuation(fine), fine).toEqual([]);
	});

	it('finds a name said as words, however it is reached', () => {
		expect(namesAsWords('Already in ${collection?.name ?? "that collection"}')).toEqual([
			'collection.name'
		]);
		expect(namesAsWords('Removed from ${nameOf(group)}')).toEqual(['nameOf(group)']);
		expect(namesAsWords('Tagged ${counted(n)} files')).toEqual([]);
	});

	it("reads a router's toast sentences, and whether pieces go beside them", () => {
		const router = [
			'    said = f"Looking up {_files(n)}; left out {m}."',
			'    return Done(said=said)',
			'',
			'    line = said_line()',
			'    return Answered(said=f"{one.name} stays a Site", pieces=pieces_of(line))',
			''
		].join('\n');
		const read = serverToasts(router);

		expect(read.map((one) => [one.message, one.pieces])).toEqual([
			['Looking up ${_files(n)}; left out ${m}.', false],
			['${one.name} stays a Site', true]
		]);
		expect(punctuation(read[0].message, { stops: false })).toEqual(['a semicolon']);
	});
});
