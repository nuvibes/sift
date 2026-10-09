import { describe, expect, it } from 'vitest';
import { disambiguate, recentFirst } from './folder-names';

/* The fault this exists for: a library where every creator's folder holds an "Images", so the
   Add panel's chooser would list nine rows all reading the same word. */

describe('disambiguate', () => {
	it('leaves a name nobody else has bare', () => {
		const said = disambiguate([
			{ value: 'a', label: 'Sift Downloads', path: 'Sift Downloads' },
			{ value: 'b', label: 'Images', path: 'Juniper/2025/Images' }
		]);
		expect(said[0]).toEqual({ value: 'a', label: 'Sift Downloads' });
	});

	it('turns nine Images into nine different rows, each saying only what it must', () => {
		const said = disambiguate([
			{ value: '1', label: 'Images', path: 'Juniper/2025/Images' },
			{ value: '2', label: 'Images', path: 'Juniper/2024/Images' },
			{ value: '3', label: 'Images', path: 'Kestrel/Images' }
		]);
		expect(said.map((one) => one.detail)).toEqual(['2025', '2024', 'Kestrel']);
		// The label itself never changes: it is what somebody is looking for.
		expect(said.every((one) => one.label === 'Images')).toBe(true);
	});

	it('lengthens the phrase only as far as it has to', () => {
		const said = disambiguate([
			{ value: '1', label: 'Images', path: 'Juniper/2025/Images' },
			{ value: '2', label: 'Images', path: 'Kestrel/2025/Images' }
		]);
		// "2025" is claimed by both, so each falls back to the segment above it.
		expect(said.map((one) => one.detail)).toEqual(['Juniper/2025', 'Kestrel/2025']);
	});

	it('says nothing about an option with no path, even when its name repeats', () => {
		const said = disambiguate([
			{ value: '', label: 'Images' },
			{ value: '2', label: 'Images', path: 'Kestrel/Images' }
		]);
		expect(said[0].detail).toBeUndefined();
		expect(said[1].detail).toBe('Kestrel');
	});

	it('keeps a root bare: its path is its own name and there is nothing above it', () => {
		const said = disambiguate([
			{ value: '1', label: 'Archive', path: 'Archive' },
			{ value: '2', label: 'Archive', path: 'Kestrel/Archive' }
		]);
		expect(said[0].detail).toBeUndefined();
		expect(said[1].detail).toBe('Kestrel');
	});

	it('says the whole path when two folders of one name sit in the same place', () => {
		const said = disambiguate([
			{ value: '1', label: 'Images', path: 'Kestrel/Images' },
			{ value: '2', label: 'Images', path: 'Kestrel/Images' }
		]);
		expect(said.map((one) => one.detail)).toEqual(['Kestrel', 'Kestrel']);
	});

	it('carries `disabled` through untouched', () => {
		const said = disambiguate([{ value: '1', label: 'Images', path: 'a/Images', disabled: true }]);
		expect(said[0].disabled).toBe(true);
	});
});

/* The other half of a folder chooser: what it lists FIRST. */
describe('recentFirst', () => {
	const FOLDERS = [
		{ value: 'c', label: 'Kestrel' },
		{ value: 'a', label: 'Juniper' },
		{ value: 'b', label: 'Alder' }
	];

	it('puts what was used last on top, in that order, and sorts the rest by name', () => {
		const listed = recentFirst(FOLDERS, ['a', 'c'], 5);

		expect(listed.map((one) => one.label)).toEqual(['Juniper', 'Kestrel', 'Alder']);
	});

	it('is the plain alphabetical list where nothing has been used yet', () => {
		expect(recentFirst(FOLDERS, [], 5).map((one) => one.label)).toEqual([
			'Alder',
			'Juniper',
			'Kestrel'
		]);
	});

	it('shows no more than it was told to, and the rest fall back into the alphabet', () => {
		// A longer run of them starts to BE the list, in an order nobody can predict, above an
		// alphabetical one they could, so the row somebody wants is in the middle of a different
		// middle.
		const listed = recentFirst(FOLDERS, ['a', 'c', 'b'], 1);

		expect(listed.map((one) => one.label)).toEqual(['Juniper', 'Alder', 'Kestrel']);
	});

	it('skips a remembered id this list does not hold', () => {
		// A folder can be removed from the library, or handed back read-only, long after somebody
		// downloaded into it.
		const listed = recentFirst(FOLDERS, ['gone', 'b'], 5);

		expect(listed.map((one) => one.label)).toEqual(['Alder', 'Juniper', 'Kestrel']);
	});
});
