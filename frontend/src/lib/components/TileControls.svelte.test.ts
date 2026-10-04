import { afterEach, describe, expect, it, vi } from 'vitest';
import { createRawSnippet, flushSync, mount, unmount } from 'svelte';
import Tile from './Tile.svelte';
import TileControls from './TileControls.svelte';
import RatingChip from './common/RatingChip.svelte';
import Heart from './common/Heart.svelte';
import { ratingScale } from '$lib/library/rating.svelte';
import {
	ALWAYS,
	FAVORITE_MARK,
	ON_HOVER,
	RATING_MARK,
	tileMarks
} from '$lib/grid/tile-marks.svelte';

/* The controls, in the place they actually live: over a tile.
 *
 * Tested here rather than in isolation because the thing worth asserting is an interaction between
 * two components. The tile opens the player when it is clicked, and the controls sit on top of it,
 * so a heart that lets its click through starts playing the clip it was meant to favorite, and
 * neither component can be shown to prevent that on its own.
 */

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

afterEach(() => {
	if (mounted) unmount(mounted);
	mounted = null;
	host?.remove();
});

const ITEM = { id: 'asset-1', media_type: 'video', duration_ms: 5000 };

/** A tile whose overlay holds one real control. */
function renderTile(control: 'heart' | 'stars', handlers: Record<string, unknown>) {
	host = document.createElement('div');
	document.body.append(host);

	const controls = createRawSnippet<[string]>(() => ({
		render: () => '<span class="planted"></span>'
	}));

	mounted = mount(Tile, {
		target: host,
		props: { item: ITEM, width: 200, height: 200, thumbSrc: '/thumb', controls, ...handlers }
	}) as Record<string, unknown>;
	flushSync();

	// Mounted into the overlay the tile drew, which is where the real one goes.
	const slot = host.querySelector('.planted') as HTMLElement;
	const component = control === 'heart' ? Heart : RatingChip;
	const props = control === 'heart' ? { favorite: false } : { rating: null };
	mount(component as never, { target: slot, props: { ...props, ...handlers } as never });
	flushSync();
	return host;
}

describe('a control drawn over a tile', () => {
	it('does not open the player when the heart is clicked', () => {
		const onopen = vi.fn();
		const onchange = vi.fn();
		renderTile('heart', { onopen, onchange });

		(host.querySelector('.heart') as HTMLButtonElement).click();

		expect(onchange).toHaveBeenCalledWith(true);
		expect(onopen, 'favouriting a clip also started playing it').not.toHaveBeenCalled();
	});

	it('does not open the player when the rating mark is pressed', () => {
		/*
		 * The mark opens `RatingChoices`, where the number is picked (and asserted, in
		 * `RatingChoices.svelte.test.ts`). What this test holds is that a control drawn on a tile
		 * is not also a press of the tile.
		 *
		 * `.mark` and not `.chip .body`: the rating is the star beside the heart on every surface,
		 * with no pill.
		 */
		const onopen = vi.fn();
		const onchange = vi.fn();
		renderTile('stars', { onopen, onchange });

		(host.querySelector('.mark') as HTMLButtonElement).click();

		expect(onopen, 'reaching for the rating also started playing it').not.toHaveBeenCalled();
	});

	it('puts the overlay beside the tile button rather than inside it', () => {
		// A button inside a button is not valid markup, browsers disagree about what it means, and
		// the tile's own handler would swallow every press meant for a control.
		renderTile('heart', {});

		const button = host.querySelector('button.tile') as HTMLElement;
		expect(button.querySelector('.controls')).toBeNull();
		expect(host.querySelector('.tile-frame > .controls')).not.toBeNull();
	});
});

describe('what the overlay is drawn on', () => {
	it('is absent on a concealed tile, which has nothing to rate', () => {
		host = document.createElement('div');
		document.body.append(host);
		const controls = createRawSnippet<[string]>(() => ({
			render: () => '<span class="planted"></span>'
		}));
		mounted = mount(Tile, {
			target: host,
			props: {
				item: { ...ITEM, concealed: true },
				width: 200,
				height: 200,
				thumbSrc: '/thumb',
				controls
			}
		}) as Record<string, unknown>;
		flushSync();

		expect(host.querySelector('.controls')).toBeNull();
	});

	it('is absent while the file is still being imported', () => {
		host = document.createElement('div');
		document.body.append(host);
		const controls = createRawSnippet<[string]>(() => ({
			render: () => '<span class="planted"></span>'
		}));
		mounted = mount(Tile, {
			target: host,
			props: { item: ITEM, width: 200, height: 200, importing: true, controls }
		}) as Record<string, unknown>;
		flushSync();

		expect(host.querySelector('.controls')).toBeNull();
	});
});

describe('the rating a tile reports', () => {
	/*
	 * A rating is stored out of ten and drawn on the account's scale, and this badge converts like
	 * `RatingChip` and `RatingChoices` do; drawing the stored value against a fixed "out of 5"
	 * would show a four-star file as `8 of 5`.
	 */
	function badge(rating: number | null): HTMLElement | null {
		host = document.createElement('div');
		document.body.append(host);
		mounted = mount(TileControls, {
			target: host,
			props: { id: 'asset-1', favorite: false, rating }
		}) as Record<string, unknown>;
		flushSync();
		return host.querySelector('.rating');
	}

	afterEach(() => {
		ratingScale.stars = 5;
	});

	it('says four stars for a stored eight, on a five-star account', () => {
		ratingScale.stars = 5;

		expect(badge(8)?.textContent).toBe(`4${String.fromCharCode(9733)}`);
	});

	it('says out of five rather than out of the storage', () => {
		ratingScale.stars = 5;

		expect(badge(8)?.getAttribute('aria-label')).toBe('Rated 4 out of 5');
	});

	it('rounds up, so a stored seven is four stars and not three', () => {
		// Seven of ten is nearer four than three, and the same rule every other surface uses.
		ratingScale.stars = 5;

		expect(badge(7)?.textContent).toBe(`4${String.fromCharCode(9733)}`);
	});

	it('reports the stored number itself on a ten-star account', () => {
		ratingScale.stars = 10;

		expect(badge(7)?.textContent).toBe(`7${String.fromCharCode(9733)}`);
		expect(badge(7)?.getAttribute('aria-label')).toBe('Rated 7 out of 10');
	});

	it('draws no badge at all for a file nobody has rated', () => {
		// Absent rather than a dash: the wall is for skimming, and a mark that means "no opinion"
		// on every unrated tile is a mark on almost every tile.
		expect(badge(null)).toBeNull();
	});
});

/*
 * The heart and the rating, resting-first: the bottom row's half of the rule the corner marks
 * follow (see `Tile.svelte.test.ts`). A heart waiting for the hover keeps its space, so drawn first
 * it would hold a gap open in front of a rating drawn always.
 */
describe('the bottom row at rest', () => {
	afterEach(() => tileMarks.forget());

	function renderRow() {
		host = document.createElement('div');
		document.body.append(host);
		mounted = mount(TileControls, {
			target: host,
			props: { id: 'asset-1', favorite: false, rating: 8 }
		}) as Record<string, unknown>;
		flushSync();
		return [...(host.querySelector('.controls-row')?.children ?? [])];
	}

	it('puts an always-drawn rating ahead of a heart waiting for the hover', () => {
		tileMarks.follow({ [FAVORITE_MARK]: ON_HOVER, [RATING_MARK]: ALWAYS });
		const row = renderRow();

		expect(row).toHaveLength(2);
		expect(row[0].classList.contains('rating'), 'the resting mark sat behind a hole').toBe(true);
		expect(row[1].querySelector('.heart')).not.toBeNull();
	});

	it('keeps the heart first when both wait for the hover, as they do out of the box', () => {
		const row = renderRow();

		expect(row[0].querySelector('.heart')).not.toBeNull();
		expect(row[1].classList.contains('rating')).toBe(true);
	});
});
