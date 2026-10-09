/* SPDX-License-Identifier: AGPL-3.0-or-later */
/* The count sentence under an entity's name, in the one place it is written. */

import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect, it } from 'vitest';

import {
	COUNTS_SEPARATOR,
	SIZE_SEPARATOR,
	counted,
	filesSaid,
	filesSized,
	joinCounts,
	peopleSaid,
	picturesSaid,
	picturesSized,
	sizeOf,
	withSize
} from './entity-counts';
import { size } from '../library/facts';

it('groups the thousands and says files', () => {
	expect(filesSaid(8400)).toBe(`${(8400).toLocaleString()} files`);
});

it('says one file without an s', () => {
	expect(filesSaid(1)).toBe('1 file');
});

it('says nothing plural about nought', () => {
	expect(filesSaid(0)).toBe('0 files');
});

it('counts a Photo Set in pictures', () => {
	expect(picturesSaid(24)).toBe('24 pictures');
	expect(picturesSaid(1)).toBe('1 picture');
});

it('joins counts on one line with an em dash, the mark the server writes for the same line', () => {
	expect(COUNTS_SEPARATOR).toBe(' \u2014 ');
	expect(joinCounts(filesSaid(8354), peopleSaid(397))).toBe(
		`${(8354).toLocaleString()} files \u2014 397 people`
	);
	expect(joinCounts('4 files', '')).toBe('4 files');
	expect(peopleSaid(1)).toBe('1 person');
	expect(peopleSaid(1397)).toBe(`${(1397).toLocaleString()} people`);
});

/* No line on screen joins two values with a spaced hyphen. */
it('leaves no spaced hyphen between two interpolated values in the client', () => {
	const root = join(__dirname, '..', '..');
	const offenders: string[] = [];
	const walk = (dir: string) => {
		for (const entry of readdirSync(dir, { withFileTypes: true })) {
			const path = join(dir, entry.name);
			if (entry.isDirectory()) walk(path);
			else if (
				/\.(svelte|ts)$/.test(entry.name) &&
				!/\.test\.ts$|schema\.d\.ts$/.test(entry.name)
			) {
				if (entry.name === 'NameTemplateField.svelte') continue;
				const text = readFileSync(path, 'utf8');
				if (text.includes('} - ${') || text.includes('} - {')) offenders.push(path);
			}
		}
	};
	walk(root);
	expect(offenders).toEqual([]);
});

it('writes a bare count grouped, as the sentences write theirs', () => {
	expect(counted(12345)).toBe('12,345');
	expect(filesSaid(12345)).toBe(`${counted(12345)} files`);
});

it('says the size of the files beside their count, in the one size formatter', () => {
	expect(filesSized(1867, 23_400_000_000)).toBe(
		`1,867 files${SIZE_SEPARATOR}${size(23_400_000_000)}`
	);
	expect(filesSized(1, 1_500_000)).toBe(`1 file${SIZE_SEPARATOR}${size(1_500_000)}`);
	expect(picturesSized(24, 310_000_000)).toBe(`24 pictures${SIZE_SEPARATOR}${size(310_000_000)}`);
	expect(withSize('On 3 files', 3, 4_000)).toBe(`On 3 files${SIZE_SEPARATOR}${size(4_000)}`);
});

it('leaves the size off where it is not said, and beside nothing at all', () => {
	expect(filesSized(1867, null)).toBe('1,867 files');
	expect(filesSized(1867, undefined)).toBe('1,867 files');
	expect(filesSized(0, 0)).toBe('0 files');
	expect(sizeOf({ size_bytes: 12 })).toBe(12);
	expect(sizeOf({ size_bytes: null })).toBeNull();
	expect(sizeOf({})).toBeNull();
});
