/*
 * The row of crops that stands for a card, and the room it reserves.
 *
 * The property held: every card on a faces wall is the same size whatever its subject has. The row
 * draws a fixed number of cells rather than a fixed height: every cell is square and the columns
 * divide the card, so a count of cells is a height at every window size, where a height in rems
 * would be right at one column count.
 */
import { expect, it, vi } from 'vitest';
import { mount, unmount } from 'svelte';

vi.mock('$lib/people/faces.svelte', () => ({
	cropUrl: (face: { track_id: string }) => `/api/faces/${face.track_id}/crop`
}));

import FaceCovers from './FaceCovers.svelte';
import source from './FaceCovers.svelte?raw';
import { applyStyles, removeStyles } from '$lib/design/testing-styles';

/** As many faces as asked for. Only `track_id` is read here, so only it is written. */
const faces = (many: number) =>
	Array.from({ length: many }, (_one, at) => ({ track_id: `face-${at}` })) as never;

function draw(props: Record<string, unknown>) {
	const host = document.createElement('div');
	document.body.append(host);
	const drawn = mount(FaceCovers, { target: host, props: props as never });
	return {
		cells: host.querySelectorAll('.faces > *').length,
		crops: host.querySelectorAll('img').length,
		more: host.querySelector('.more')?.textContent?.trim() ?? null,
		close: () => {
			void unmount(drawn);
			host.remove();
		}
	};
}

it('draws exactly what it was handed when no room is reserved', () => {
	// A file's own strip of faces is as long as that file's faces. It is not one of a wall of
	// equals, so reserving room for it would be room held for nothing.
	const drawn = draw({ faces: faces(3) });
	expect(drawn.cells).toBe(3);
	expect(drawn.more).toBe(null);
	drawn.close();
});

it('holds the same room for a card of one as for a card of twelve', () => {
	const one = draw({ faces: faces(1), most: 12 });
	const full = draw({ faces: faces(12), most: 12 });

	expect(one.cells).toBe(12);
	expect(full.cells).toBe(12);
	// And the short one is short on CROPS, not on cells: the empty ones hold the height and show
	// nothing, because a wall of cards must not display its own scaffolding.
	expect(one.crops).toBe(1);
	expect(full.crops).toBe(12);
	one.close();
	full.close();
});

it('says how many it is not showing, in a cell of its own', () => {
	/* A card must not quietly report four of somebody's faces as though that were all of them. The
	   counter takes a cell, so it counts everything from the cell it stands in onwards: "+9" over
	   twelve faces in four cells, never "+8", which would be a cell spent hiding one face it had
	   room for. */
	const drawn = draw({ faces: faces(12), most: 4 });

	expect(drawn.cells).toBe(4);
	expect(drawn.crops).toBe(3);
	expect(drawn.more).toBe('+9');
	drawn.close();
});

it('counts the faces it was never handed, as the server counted them', () => {
	/* A confirm card is sent twelve of somebody's 229 faces; the last cell says the rest. */
	const drawn = draw({ faces: faces(12), most: 12, total: 229 });

	expect(drawn.cells).toBe(12);
	expect(drawn.crops).toBe(11);
	expect(drawn.more).toBe('+218');
	drawn.close();
});

it('lays a card strip six across, and a file strip by width', () => {
	const card = document.createElement('div');
	const file = document.createElement('div');
	document.body.append(card, file);
	const one = mount(FaceCovers, { target: card, props: { faces: faces(3), most: 12 } as never });
	const two = mount(FaceCovers, { target: file, props: { faces: faces(3) } as never });

	expect(card.querySelector('.faces')?.classList.contains('rows')).toBe(true);
	expect(file.querySelector('.faces')?.classList.contains('rows')).toBe(false);
	void unmount(one);
	void unmount(two);
	card.remove();
	file.remove();
});

it("lays a group's share of a card strip in its own columns", () => {
	const host = document.createElement('div');
	document.body.append(host);
	const two = mount(FaceCovers, {
		target: host,
		props: { faces: faces(3), most: 4, across: 2 } as never
	});
	const strip = host.querySelector('.faces') as HTMLElement;
	expect(strip.classList.contains('across-3')).toBe(false);
	expect(strip.children).toHaveLength(4);
	applyStyles(source, strip);
	expect(getComputedStyle(strip).gridTemplateColumns).toBe('repeat(2, minmax(0, 1fr))');
	removeStyles();
	void unmount(two);
	host.remove();
});
