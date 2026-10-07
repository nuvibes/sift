/* One value, drawn from its declared type.
 *
 * This is the only place in the app that turns a stored number into something to read, and every
 * record surface goes through it: the facts under a name, the panel beside it, and the fields a
 * form cannot edit. A wrong unit here is wrong on all three at the same time and looks perfectly
 * plausible on each, which is why the conversions are asserted against exact strings rather than
 * against "contains a number".
 *
 * The other half is the dash. A field with nothing in it draws a dash rather than a blank, so a
 * record with gaps reads as a record with gaps instead of as a screen that failed to load, and
 * "empty" has to mean the same thing for a null, an empty string and an empty list.
 */

import { afterEach, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import RecordValue from './RecordValue.svelte';
import type { FieldKind, RecordLink } from '$lib/entity/records.svelte';
import { appearance } from '$lib/theme/appearance.svelte';

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
});

function draw(kind: FieldKind, value: unknown): HTMLElement {
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(RecordValue, { target: host, props: { kind, value } }) as Record<string, unknown>;
	flushSync();
	return host;
}

function text(kind: FieldKind, value: unknown): string {
	return (draw(kind, value).textContent ?? '').trim();
}

const DASH = '—';

it.each([
	['nothing at all', null],
	['a field never sent', undefined],
	['a box somebody emptied', ''],
	['a list with nothing in it', []]
])('draws a dash for %s', (_what, value) => {
	expect(text('text', value)).toBe(DASH);
	expect(host.querySelector('.unset')).not.toBeNull();
});

it.each([
	[true, 'Yes'],
	[1, 'Yes'],
	['1', 'Yes'],
	[false, 'No'],
	[0, 'No'],
	['0', 'No']
])('draws a flag %s as a word', (value, said) => {
	/* Yes or no, never the 0 and 1 the column keeps, and never the dash: a flag has two states and
	   no third, so it is answered for everybody the moment the row exists. `'0'` is the one that
	   has to be named: a value that came back through a form as text is a non-empty string, which
	   is truthy, so a plain truth test would read "off" as Yes. */
	expect(text('flag', value)).toBe(said);
	expect(host.querySelector('.unset')).toBeNull();
});

it('does not call zero empty', () => {
	// A count of nothing and a field nobody filled in are different facts, and `0` is falsy.
	expect(text('count', 0)).toBe('0');
	expect(host.querySelector('.unset')).toBeNull();
});

it.each([
	[0, '0 B'],
	[999, '999 B'],
	[1000, '1.0 kB'],
	[1500, '1.5 kB'],
	[9999, '10.0 kB'],
	[10_000, '10 kB'],
	[1_000_000, '1.0 MB'],
	[1_500_000_000, '1.5 GB'],
	[2_000_000_000_000, '2.0 TB'],
	// Past the last unit the number grows rather than inventing one.
	[5_000_000_000_000_000, '5000 TB']
])('draws %i bytes as %s', (count, expected) => {
	expect(text('bytes', count)).toBe(expected);
});

/* Zero is not a length. It is what a file nothing could be read from is written down as, so the
   record draws the blank it draws for anything nobody has filled in: `0:00` claims a film of no
   duration. The tile badge has always hidden itself on a zero for the same reason. */
it('draws a length of zero as a blank rather than as 0:00', () => {
	expect(text('duration', 0)).toBe('\u2014');
});

it.each([
	[1000, '0:01'],
	[59_000, '0:59'],
	[60_000, '1:00'],
	[95_000, '1:35'],
	[3_599_000, '59:59'],
	[3_600_000, '1:00:00'],
	[3_661_000, '1:01:01']
])('draws %i milliseconds as %s', (ms, expected) => {
	expect(text('duration', ms)).toBe(expected);
});

it('reads a timestamp as whole seconds and a date as text', () => {
	// Noon UTC on New Year's Day 2021, away from a midnight boundary so the local day is not in
	// doubt.
	const noon = Date.UTC(2021, 0, 1, 12, 0, 0) / 1000;
	const asTimestamp = text('timestamp', noon);
	const asDate = text('date', '2021-01-01T12:00:00Z');

	expect(asTimestamp).toContain('2021');
	// Seconds, not milliseconds. Read as milliseconds this is 1970 and looks like a real date.
	expect(asTimestamp).not.toContain('1970');
	expect(asDate).toContain('2021');
});

it('draws a plain day as that day, whatever the zone is', () => {
	/*
	 * `new Date('1991-07-09')` is midnight UTC, and drawing that instant in a zone behind UTC gives
	 * the EIGHTH. A birthdate typed as the ninth would read back as the eighth, for most of the
	 * world, with the suite green if the suite runs where the offset is zero.
	 *
	 * So the check is not "does it say 1991": it is the DAY, compared against a date built from the
	 * same three numbers locally. That is true in every zone, including the one that hid it.
	 */
	const drawn = text('date', '1991-07-09');
	const local = new Date(1991, 6, 9).toLocaleDateString(undefined, {
		year: 'numeric',
		month: 'short',
		day: 'numeric'
	});

	expect(drawn).toBe(local);
	expect(drawn).toContain('9');
});

it('rounds a rate to two places and says what it is', () => {
	expect(text('rate', 29.970029)).toBe('29.97 fps');
	expect(text('rate', 60)).toBe('60 fps');
});

it('draws dimensions as width by height', () => {
	expect(text('dimensions', [1920, 1080])).toBe('1920 x 1080');
});

it('draws a count as itself', () => {
	expect(text('count', 42)).toBe('42');
});

it('falls back to the plain value when a number field holds text', () => {
	// An older server, or a field whose type was changed. Better a readable string than NaN.
	expect(text('bytes', 'a lot')).toBe('a lot');
	expect(text('duration', 'ages')).toBe('ages');
	expect(text('rate', 'fast')).toBe('fast');
});

it('draws a list of names as a real list of chips', () => {
	draw('names', ['Jane', 'Janet']);

	const row = host.querySelector('ul');
	expect(row).not.toBeNull();
	expect(host.querySelectorAll('li')).toHaveLength(2);
	expect((row?.textContent ?? '').replace(/\s+/g, ' ').trim()).toBe('Jane Janet');
});

it('links a tag to its own page', () => {
	draw('tags', [{ id: 't1', name: 'runway' }]);

	const link = host.querySelector('a') as HTMLAnchorElement;
	expect(link.getAttribute('href')).toBe('/tags/t1');
	expect(link.textContent?.trim()).toBe('runway');
});

it('opens a link in a new page without handing over where it came from', () => {
	draw('links', [{ id: 'l1', url: 'https://example.test/a?utm=1', site_name: 'Example' }]);

	const link = host.querySelector('a') as HTMLAnchorElement;
	expect(link.getAttribute('href')).toBe('https://example.test/a?utm=1');
	expect(link.getAttribute('target')).toBe('_blank');
	// noreferrer as well as noopener: the second is what stops this install's address being sent.
	expect(link.getAttribute('rel')).toContain('noopener');
	expect(link.getAttribute('rel')).toContain('noreferrer');
	expect(link.textContent?.trim()).toBe('Example');
});

it('names a link by its address when the site is not known', () => {
	draw('links', [{ id: 'l1', url: 'https://example.test/a', site_name: null }]);

	expect(host.querySelector('a')?.textContent?.trim()).toBe('example.test/a');
});

it("draws the site's own mark before each link, asked by the host alone", () => {
	draw('links', [
		{ id: 'l1', url: 'https://en.example.test/wiki/Page?q=1', site_name: null },
		'https://quillhouse.example/wren'
	]);

	const marks = [...host.querySelectorAll('a')].map((one) => one.querySelector('img.mark'));
	expect(marks.map((one) => one?.getAttribute('src'))).toEqual([
		'/api/sites/icons/for?host=en.example.test',
		'/api/sites/icons/for?host=quillhouse.example'
	]);
	// Decorative: the words beside it already say which site, and they are unchanged.
	expect(marks[0]?.getAttribute('alt')).toBe('');
	expect(host.querySelector('a')?.textContent?.trim()).toBe('en.example.test/wiki/Page?q=1');
});

it('takes the mark away when the pack has none for that host, and keeps the words', () => {
	draw('links', [{ id: 'l1', url: 'https://nobody.example/x', site_name: 'Nobody' }]);

	const mark = host.querySelector('img.mark') as HTMLImageElement;
	expect(mark).not.toBeNull();
	mark.dispatchEvent(new Event('error'));
	flushSync();

	expect(host.querySelector('img.mark')).toBeNull();
	expect(host.querySelector('a')?.textContent?.trim()).toBe('Nobody');
});

it('draws no mark for an address with no host to ask about', () => {
	draw('links', ['not an address', 'https://localhost/x']);

	expect(host.querySelectorAll('a')).toHaveLength(2);
	expect(host.querySelector('img.mark')).toBeNull();
});

it('keeps the line breaks somebody typed', () => {
	draw('paragraph', 'one\ntwo');

	const written = host.querySelector('.paragraph') as HTMLElement;
	expect(written).not.toBeNull();
	expect(written.textContent).toBe('one\ntwo');
});

it('says a box constant in a word field as the word, the way the History line does', () => {
	// An enumerated value is drawn in the same words History uses for it ("Blonde", not `BLONDE`).
	expect(text('word', 'BLONDE')).toBe('Blonde');
	expect(text('word', 'mp4')).toBe('mp4');
});

it.each(['filename', 'path', 'word'] as FieldKind[])(
	'draws %s in the face that does not re-order it',
	(kind) => {
		draw(kind, 'clip.mp4');
		expect(host.querySelector('.exact')).not.toBeNull();
		expect(host.querySelector('.plain')).toBeNull();
	}
);

/* One address, as opposed to a list of them.
 *
 * Its own kind rather than a `links` list holding one entry: a field that always holds exactly one
 * thing and is typed as a list makes every reader handle a case that cannot happen, and leaves
 * every writer to decide what a second entry would mean.
 */
it('draws a single address as a link that opens away from the page', () => {
	const shown = draw('link', 'https://example.com/watch/1');
	const anchor = shown.querySelector('a');

	expect(anchor?.getAttribute('href')).toBe('https://example.com/watch/1');
	expect(anchor?.getAttribute('target')).toBe('_blank');
	// Both tokens, for two different reasons: one stops the page that opens reaching back through
	// `window.opener`, the other stops this install's address being sent to the site as a Referer.
	expect(anchor?.getAttribute('rel')).toContain('noopener');
	expect(anchor?.getAttribute('rel')).toContain('noreferrer');
});

it('reads an address without its scheme, because that is not how anybody recognises a site', () => {
	expect(text('link', 'https://example.com/watch/1')).toBe('example.com/watch/1');
	expect(text('link', 'http://example.com/x')).toBe('example.com/x');
});

it('draws no link at all when there is no address, rather than an empty one', () => {
	// A file Sift did not fetch has none, which is most of a library that was scanned. An anchor
	// with nothing in it is a control that looks pressable and does nothing.
	expect(text('link', null)).toBe(DASH);
	expect(draw('link', null).querySelector('a')).toBeNull();
	expect(text('link', '')).toBe(DASH);
});

/* A value that NAMES SOMETHING, drawn as the way in to it.
 *
 * A site's "Part of" is a `text` field whose value is the name of another site: a page with a
 * cover, a wall and a record of its own. Drawn as text it would be a word with nothing to press,
 * and the only route to the network would be to search for its name. The link is worked out by
 * `linkFor` from the server's declaration and handed in; what is asserted here is that it WINS
 * over the type, because what a row leads to matters more than which shape it is stored as.
 */
function drawLink(kind: FieldKind, value: unknown, link: RecordLink): HTMLElement {
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(RecordValue, { target: host, props: { kind, value, link } }) as Record<
		string,
		unknown
	>;
	flushSync();
	return host;
}

it('draws a value that names something as a chip that opens it', () => {
	const shown = drawLink('text', 'Northlight Group', {
		kind: 'site',
		id: 'pf1',
		href: '/sites/pf1'
	});
	const anchor = shown.querySelector('a');

	expect(anchor?.getAttribute('href')).toBe('/sites/pf1');
	// The chip's own label, not the whole chip: the picture in front of the words falls back to the
	// initial when nothing has loaded, which is an `N` sitting in `textContent` beside the name.
	expect(shown.querySelector('.label')?.textContent?.trim()).toBe('Northlight Group');
	// ...and the picture IS drawn, which is the half of this that makes a site recognisable before
	// it is read. Its address is the entity's own cover. See `entityPicture`.
	expect(shown.querySelector('img')?.getAttribute('src')).toBe('/api/sites/pf1/cover');
	// An address INSIDE this application, so none of the away-from-the-page tokens belong on it:
	// `target="_blank"` on an internal link opens a second copy of Sift.
	expect(anchor?.getAttribute('target')).toBeNull();
});

it('draws the same value as plain text when it leads nowhere', () => {
	// Which is every record in a library where nothing has been said to be part of anything, and
	// every field that is only a word. The link is the exception and has to look like one.
	expect(draw('text', 'Northlight Group').querySelector('a')).toBeNull();
	expect(text('text', 'Northlight Group')).toBe('Northlight Group');
});

it('draws a dash rather than an empty chip when the value is gone', () => {
	// The id can outlive the name on a half-written draft. A chip with an address and no word in it
	// is a target nobody can read, so emptiness still wins over the link.
	const shown = drawLink('text', '', { kind: 'site', id: 'pf1', href: '/sites/pf1' });
	expect((shown.textContent ?? '').trim()).toBe(DASH);
	expect(shown.querySelector('a')).toBeNull();
});

/*
 * A height follows the account's own system, and the stored value never moves.
 *
 * The one measured kind a record carries is `Kind.LENGTH`, whose one field is `height_cm`. The
 * exact string is asserted, because a height in the wrong system looks plausible wherever it is
 * drawn. The arithmetic has its own tests in `measure.test.ts`; what is tested here is that the
 * preference reaches the record at all, the half a component can get wrong on its own.
 */
it('draws a height in whichever system this account reads in', () => {
	appearance.units = 'metric';
	expect(text('length', 175)).toBe('175 cm');

	appearance.units = 'imperial';
	expect(text('length', 175)).toBe('5 ft 9 in');

	appearance.units = 'metric';
});
