/* The wall four screens are made of, and the one number that decides whether it draws at all.
 *
 * There is only one interesting decision in this component: waiting, failed, empty, or the cards.
 * The last two are told apart by a single count, and taking that count from the wrong place is not
 * a subtle wrongness: it is a screen that says "No tags yet" over a tag.
 *
 * The walls page, so each store carries a `total` beside its rows. A store that adds a newly made
 * row to the page it is holding has nothing to say about the total, so a wall reading the total
 * would draw the empty note over the first tag or the first collection on an install: 201 from the
 * server, a row in the store, and a screen that reads as the write having failed.
 *
 * So the prop is named for the rows on the wall rather than for how many exist, and these are the
 * two halves of that: rows with a zero total draw, and no rows draws the note.
 */

import { afterEach, describe, expect, it } from 'vitest';
import { createRawSnippet, flushSync, mount, unmount, type ComponentProps } from 'svelte';
import EntityGrid from './EntityGrid.svelte';
import gridSource from './EntityGrid.svelte?raw';
import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import { navItem } from '$lib/components/shell/nav';
import type { IconName } from '$lib/design/icons';
import codepoints from '$lib/generated/icon-codepoints.json';
import people from '../../../routes/people/+page.svelte?raw';
import sites from '../../../routes/sites/+page.svelte?raw';
import collections from '../../../routes/collections/+page.svelte?raw';
import photoSets from '../../../routes/photo-sets/+page.svelte?raw';
import tags from '../../../routes/tags/+page.svelte?raw';

let host: HTMLElement;
let instance: Record<string, unknown> | null = null;

/** A snippet of plain text, which is all these need: what is under test is WHICH branch renders. */
function words(text: string) {
	return createRawSnippet(() => ({ render: () => `<p>${text}</p>` }));
}

const EMPTY = 'No tags yet.';

type Props = ComponentProps<typeof EntityGrid>;

function draw(props: Partial<Props>) {
	host = document.createElement('div');
	document.body.append(host);
	const all: Props = {
		title: 'Tags',
		icon: 'shoppingmode',
		empty: EMPTY,
		drawn: 0,
		children: words('a card'),
		...props
	};
	instance = mount(EntityGrid, { target: host, props: all });
	flushSync();
}

afterEach(() => {
	if (instance) void unmount(instance, { outro: false });
	instance = null;
	host?.remove();
	document.body.innerHTML = '';
});

describe('what the wall draws', () => {
	it('draws the cards when it has rows, whatever any total says', () => {
		// One row and a zero total is exactly the state a store is in the moment somebody makes the
		// first tag on an install. Both numbers are passed, and they disagree on purpose: a wall
		// handed only one of them cannot tell which it was asked to read.
		draw({ drawn: 1, total: 0 });

		expect(host.textContent).toContain('a card');
		expect(host.textContent).not.toContain(EMPTY);
	});

	it('draws the note when it has none', () => {
		draw({ drawn: 0 });

		expect(host.textContent).toContain(EMPTY);
		expect(host.textContent).not.toContain('a card');
	});

	it("draws each wall's own glyph over its empty note, the rail's for that entity", () => {
		/* One shared tray on every empty wall would make an empty People wall and an empty Tags
		   wall look like one screen. Each wall hands the grid its rail glyph (`RAIL_NAV`) and
		   the empty state draws that. */
		const walls = { people, sites, collections, 'photo-sets': photoSets, tags };
		for (const [id, source] of Object.entries(walls)) {
			const icon = navItem(id)?.icon as IconName;
			expect(icon, id).toBeTruthy();
			expect(source, id).toContain(`icon="${icon}"`);

			draw({ drawn: 0, icon });
			const glyph = host.querySelector('.empty .glyph .icon')?.textContent;
			expect(glyph, id).toBe(String.fromCodePoint(parseInt(codepoints[icon], 16)));
			void unmount(instance!, { outro: false });
			instance = null;
			host.remove();
		}
	});

	it('says it is waiting rather than saying there is nothing', () => {
		// The difference matters on a slow answer: "nothing here" is a statement about somebody's
		// library, and while the answer is still coming it would be a false one.
		draw({ drawn: 0, loading: true });

		expect(host.textContent).not.toContain(EMPTY);
		expect(host.querySelector('[aria-busy="true"]')).not.toBeNull();
	});

	it('waits under placeholders of the one shape every card is', () => {
		draw({ drawn: 0, loading: true });
		expect(host.querySelector('.ghost-face')).not.toBeNull();
		const shapes = [...gridSource.matchAll(/^\s*aspect-ratio:\s*([^;]+);/gm)].map((one) => one[1]);
		expect(shapes).toEqual(['2 / 3']);
	});

	it('says what went wrong rather than saying there is nothing', () => {
		draw({ drawn: 0, failed: 'The tags could not be loaded.' });

		expect(host.textContent).toContain('could not be loaded');
		expect(host.textContent).not.toContain(EMPTY);
	});

	it('puts the pager under the cards, so every wall keeps it in one place', () => {
		draw({ drawn: 1, pager: words('a pager') });

		expect(host.textContent).toContain('a pager');
	});
});

/*
 * At a phone's width the wall is three columns, so two rows fit a screenful and a page (whole rows
 * times four screenfuls, measured by `CardPaging`) holds more than the 8 two tall columns would.
 * The unit environment answers only a plain `screen` rule, so the phone rule is read as one.
 */
describe("the wall at a phone's width", () => {
	afterEach(removeStyles);

	it('floors a card at three to a phone, not two', () => {
		draw({ drawn: 1 });
		const wall = host.querySelector('.wall') as HTMLElement;
		const media = '@media (max-width: 640px)';
		expect(gridSource, 'the wall has no phone rule').toContain(media);
		applyStyles(gridSource.replaceAll(media, '@media screen'), wall);

		expect(getComputedStyle(wall).getPropertyValue('--card-floor').trim()).toBe('104px');
	});
});

describe('what floats over the wall', () => {
	/*
	 * The selection bar is positioned against the frame and stands above its footer only when it is
	 * INSIDE the frame, where the frame says how tall the footer is. Mounted after the wall,
	 * outside the frame, it would read no footer and cover the pager whenever anything was picked,
	 * and at a phone's width it would also sit under a docked corner player.
	 */
	it("draws it inside the frame, where the frame's footer can be read", () => {
		draw({ drawn: 1, floating: words('a bar') });
		const bar = [...host.querySelectorAll('p')].find((one) => one.textContent === 'a bar');
		expect(bar, 'the floating snippet was not drawn').toBeDefined();
		expect(bar!.closest('.frame')).not.toBeNull();
	});

	const walls = import.meta.glob(
		'/src/routes/{people,sites,tags,collections,photo-sets}/+page.svelte',
		{
			query: '?raw',
			import: 'default',
			eager: true
		}
	) as Record<string, string>;

	it('is handed the selection bar by every wall of things, rather than drawn after it', () => {
		expect(Object.keys(walls)).toHaveLength(5);
		for (const [path, source] of Object.entries(walls)) {
			const inside = source.slice(
				source.indexOf('{#snippet floating()}'),
				source.indexOf('</EntityGrid>')
			);
			expect(source.includes('{#snippet floating()}'), `${path} hands no floating snippet`).toBe(
				true
			);
			expect(inside, `${path} draws its selection bar outside the frame`).toContain(
				'<EntitySelectionBar'
			);
		}
	});
});
