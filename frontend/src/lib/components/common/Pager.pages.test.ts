/*
 * The numbered pager's numbers: the first and last page, the one being read and one either side,
 * and a gap only where it hides more than one page.
 */
import { describe, expect, it } from 'vitest';
import { pageMarks } from './Pager.svelte';

const said = (current: number, pages: number) =>
	pageMarks(current, pages).map((mark) => ('page' in mark ? mark.page : '...'));

describe('pageMarks', () => {
	it('draws every page of a short list', () => {
		expect(said(1, 1)).toEqual([1]);
		expect(said(2, 4)).toEqual([1, 2, 3, 4]);
	});

	it('keeps the first, the last, and the pages around the current one, with gaps between', () => {
		expect(said(1, 62)).toEqual([1, 2, '...', 62]);
		expect(said(30, 62)).toEqual([1, '...', 29, 30, 31, '...', 62]);
		expect(said(62, 62)).toEqual([1, '...', 61, 62]);
	});

	it('draws the one page a gap would hide instead of the gap', () => {
		expect(said(4, 10)).toEqual([1, 2, 3, 4, 5, '...', 10]);
	});

	it('draws nothing for a list with no pages', () => {
		expect(said(1, 0)).toEqual([]);
	});
});
