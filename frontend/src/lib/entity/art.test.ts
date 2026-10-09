/* The addresses Sift builds for pictures, and the one property they all rest on. */
import { describe, expect, it } from 'vitest';
import { clipWasBuilt, coverUrl, hiddenMark, thumbUrl } from './art';

describe('an entity cover address', () => {
	it('names which file the cover is', () => {
		/* The token names the picture: choosing a different file must change the address. */
		const before = coverUrl('/people/p1', 'stamp', { assetId: 'a1' });
		const after = coverUrl('/people/p1', 'stamp', { assetId: 'a2' });

		expect(before).not.toEqual(after);
	});

	it('is the same address while the cover is the same', () => {
		// The other half, and the reason this is a token rather than a random number: an address
		// that moved on every render would fetch every picture again on every page.
		expect(coverUrl('/people/p1', 'stamp', { assetId: 'a1' })).toEqual(
			coverUrl('/people/p1', 'stamp', { assetId: 'a1' })
		);
	});

	it('still moves when the account can see different things', () => {
		// The stamp is the other half and it is not optional: without it a cover would go on being
		// shown out of the browser's own store after the person in it was hidden.
		expect(coverUrl('/people/p1', 'stamp-4', { assetId: 'a1' })).not.toEqual(
			coverUrl('/people/p1', 'stamp-5', { assetId: 'a1' })
		);
	});

	it('falls back to the plain token when nothing has been chosen', () => {
		// Nothing to fold in, and the address is answered with a 404 anyway.
		expect(coverUrl('/people/p1', 'stamp')).toEqual('/api/people/p1/cover?v=stamp');
	});

	it('names the chosen moment, so a frame of a video has an address of its own', () => {
		/* The server keeps a cover only under an address naming which cover it is: the file AND
		   the moment. */
		expect(coverUrl('/people/p1', 'stamp', { assetId: 'a1', atMs: 5000 })).toEqual(
			'/api/people/p1/cover?v=stamp.a1.5000'
		);
		expect(coverUrl('/people/p1', 'stamp', { assetId: 'a1', atMs: 0 })).toEqual(
			'/api/people/p1/cover?v=stamp.a1.0'
		);
	});

	it('keeps the wall and the id as separate segments', () => {
		/* Not cosmetic. */
		expect(coverUrl('/collections/c1', null)).toEqual('/api/collections/c1/cover');
	});

	/* A Site with no chosen picture is answered with the SHIPPED logo, which its address must
	   name. */
	it("names the shipped logo after the row's own token", () => {
		expect(coverUrl('/sites/s1', 'stamp', { icon: '0.1.171-abc' })).toEqual(
			'/api/sites/s1/cover?v=stamp.0.1.171-abc'
		);
	});

	it('lets a chosen cover win over the logo, as the server does', () => {
		expect(coverUrl('/sites/s1', 'stamp', { assetId: 'a1', icon: '0.1.171-abc' })).toEqual(
			'/api/sites/s1/cover?v=stamp.a1'
		);
		expect(coverUrl('/sites/s1', 'stamp', { uploadId: 'u1', icon: '0.1.171-abc' })).toEqual(
			'/api/sites/s1/cover?v=stamp.u1'
		);
	});

	it('moves the address when the pack ships a new logo', () => {
		expect(coverUrl('/sites/s1', 'stamp', { icon: '0.1.171-abc' })).not.toEqual(
			coverUrl('/sites/s1', 'stamp', { icon: '0.1.172-def' })
		);
	});
});

describe('whether a tile asks for its hover clip', () => {
	it('asks once the server says the clip was built', () => {
		expect(clipWasBuilt({ media_type: 'video', preview: true, concealed: false })).toBe(true);
		// A GIF has one too. Leaving them out would leave every GIF frozen under the cursor while
		// the clip it would have played was already on disk.
		expect(clipWasBuilt({ media_type: 'gif', preview: true, concealed: false })).toBe(true);
	});

	it('does not ask for a file that has a still and no clip', () => {
		/* The whole reason the field exists: a moving file can have a still and no clip. */
		expect(clipWasBuilt({ media_type: 'video', preview: false, concealed: false })).toBe(false);
	});

	it('never asks for a still, whatever else is true of it', () => {
		expect(clipWasBuilt({ media_type: 'image', preview: true, concealed: false })).toBe(false);
	});

	it('never asks for a concealed placeholder', () => {
		// It describes nothing about itself and the server would refuse the picture anyway.
		expect(clipWasBuilt({ media_type: 'video', preview: true, concealed: true })).toBe(false);
	});
});

describe('the still of a file', () => {
	it('is an address that carries the token', () => {
		expect(thumbUrl({ id: 'f1', art: 'v1' })).toBe('/api/assets/f1/thumb?v=v1');
	});

	it('is the hidden mark for a concealed file, and never an address the server would refuse', () => {
		/* Asked for, a hidden copy's still would be refused by the server on every visit. */
		const still = thumbUrl({ id: 'f1', art: null, concealed: true });
		expect(still).toBe(hiddenMark());
		expect(still.startsWith('data:image/svg+xml,')).toBe(true);
		expect(still).not.toContain('/api/');
	});

	it('is the same mark a face in the vault is drawn with', () => {
		expect(hiddenMark()).toBe(hiddenMark());
	});
});
