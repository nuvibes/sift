/* The picture a chip, a pick row and the hover card draw an entity by. */
import { describe, expect, it, vi } from 'vitest';

/* The names Sift holds a creator picture for, which the store asks the server once for. */
vi.mock('$lib/entity/creator-art.svelte', () => ({
	creatorArt: { has: (name: string) => name === 'Esme Wrenfield' }
}));

import { drawnBy, entityPicture, glyphOf, linkMarks, pickRow } from './entity-picture';

describe('the address an entity is drawn by', () => {
	it('names the chosen file and moment when the caller holds the row', () => {
		const drawn = entityPicture('person', 'p1', 'Neve Arbogast', {
			cover_asset_id: 'a1',
			cover_at_ms: 1500,
			art: 'stamp'
		});
		expect(drawn.src).toBe('/api/people/p1/cover?v=stamp.a1.1500');
	});

	it('names an upload with the token in front of it', () => {
		const drawn = entityPicture('tag', 't1', 'Beach', { cover_upload_id: 'u1', art: 'stamp' });
		expect(drawn.src).toBe('/api/tags/t1/cover?v=stamp.u1');
	});

	it("names a site's shipped logo when its row says nobody has chosen a picture", () => {
		const drawn = entityPicture('site', 's1', 'Quillhouse', { icon: '0.1.171-abc', art: 'stamp' });
		expect(drawn.src).toBe('/api/sites/s1/cover?v=stamp.0.1.171-abc');
	});

	it('stays the bare address when the caller has only a name and an id', () => {
		expect(entityPicture('collection', 'c1', 'Shelf').src).toBe('/api/collections/c1/cover');
	});

	it("draws a song nobody covered as the music glyph, at the song's own address", () => {
		/* Every chip and hover card that names a song reads this, so none of them can fall back
		   to the song's letter by forgetting. */
		const drawn = entityPicture('song', 's1', 'Lantern Hum');
		expect(drawn.src).toBe('/api/songs/s1/cover');
		expect(drawn.glyph).toBe('music_note_2');
		expect(glyphOf('song')).toBe('music_note_2');
		// Every other kind keeps its letter.
		expect(entityPicture('photo_set', 'p1', 'Shoot').glyph).toBeUndefined();
		expect(glyphOf('person')).toBeUndefined();
	});
});

/* THE PICTURE A THING IS DRAWN BY in a picker: the rule its card and its page apply, branch by
 * branch, so a row in "Who is this?" */
describe('the picture a picker draws a thing by', () => {
	it('draws an upload first, framed as chosen', () => {
		expect(
			drawnBy('person', {
				id: 'p1',
				name: 'Anouk Vestergaard',
				cover_upload_id: 'u1',
				cover_asset_id: null,
				art: 'stamp'
			})
		).toEqual({ src: '/api/people/p1/cover?v=stamp.u1' });
	});

	it('draws the face when the cover is a face in a file, as the card does', () => {
		expect(
			drawnBy('person', {
				id: 'p1',
				name: 'Anouk Vestergaard',
				cover_asset_id: 'a1',
				cover_track_id: 't1',
				art: 'stamp'
			})
		).toEqual({ src: '/api/faces/t1/cover?v=stamp' });
	});

	it("draws the file at its chosen moment, with the file's own still behind it", () => {
		expect(
			drawnBy('collection', {
				id: 'c1',
				name: 'Late nights',
				cover_asset_id: 'a1',
				cover_at_ms: 1500,
				art: 'stamp'
			})
		).toEqual({
			src: '/api/collections/c1/cover?v=stamp.a1.1500',
			instead: '/api/assets/a1/thumb'
		});
	});

	it('draws a person nobody chose a cover for by the picture the site shows them with', () => {
		expect(drawnBy('person', { id: 'p2', name: 'Esme Wrenfield' })).toEqual({
			src: '/api/creator-art/Esme%20Wrenfield'
		});
		// Only a person: a tag of the same name is not the creator.
		expect(drawnBy('tag', { id: 't2', name: 'Esme Wrenfield' })).toBeUndefined();
	});

	it('draws a Site nobody chose a cover for by its shipped logo, whole', () => {
		expect(
			drawnBy('site', { id: 's1', name: 'Quillhouse', icon: '0.1.171-abc', art: 'stamp' })
		).toEqual({ src: '/api/sites/s1/cover?v=stamp.0.1.171-abc', mark: true });
	});

	it('draws nothing where nothing was chosen, so the row keeps its letter', () => {
		expect(drawnBy('person', { id: 'p3', name: 'Bryn Calloway' })).toBeUndefined();
		expect(pickRow('tag', { id: 't3', name: 'Beach' })).toEqual({ id: 't3', name: 'Beach' });
	});

	it('hands a picker the row with that picture', () => {
		expect(
			pickRow('tag', { id: 't4', name: 'Beach', cover_upload_id: 'u4', art: 'stamp' })
		).toEqual({ id: 't4', name: 'Beach', picture: { src: '/api/tags/t4/cover?v=stamp.u4' } });
	});
});

/* EVERY PICTURE THAT MAY STAND FOR ONE LINK, best first, read by both link surfaces. */
describe('the pictures a link may be drawn with', () => {
	it("asks the pack by the link's host, and a filed site adds no second address", () => {
		expect(linkMarks('https://www.example.test/jane?x=1', 'FiledSite')).toEqual([
			'/api/sites/icons/for?host=www.example.test'
		]);
	});

	it('offers the pack alone for a link with no site filed behind it', () => {
		expect(linkMarks('https://example.test/jane', null)).toEqual([
			'/api/sites/icons/for?host=example.test'
		]);
		expect(linkMarks('https://example.test/jane', 'Unfetched')).toEqual([
			'/api/sites/icons/for?host=example.test'
		]);
	});

	it('offers nothing for an address with no host, and the caller draws its own stand-in', () => {
		expect(linkMarks('not an address', null)).toEqual([]);
	});
});
