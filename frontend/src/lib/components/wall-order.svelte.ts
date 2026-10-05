/**
 * The question a wall of files asks the server, and the order it asks it in.
 *
 * What the address filters to, then what the screen is, then the order: the screen's constraint
 * cannot be taken off, and applying both gives a narrower set, never a different one
 * (`bothNarrowings`). Sort is not part of the query language, so it rides beside the query as its
 * own parameter, as `limit` and `offset` do.
 */

import { goto } from '$app/navigation';
import { page as address } from '$app/state';
import { bothNarrowings } from '$lib/components/shell/facet-labels';
import type { RowSource } from '$lib/grid/grid.svelte';
import {
	DEFAULT_SORT,
	gridSort,
	mintSeed,
	RANDOM,
	RELEVANCE,
	RELEVANCE_BY_MEANING,
	RESHUFFLE,
	RESHUFFLE_OPTION,
	SIMILARITY,
	similarityChoice,
	similarToQuery,
	SORT_OPTIONS,
	WALL_ONLY_ORDERS,
	type SimilarTo
} from '$lib/grid/sort-state.svelte';
import { FIELDS } from '$lib/search/search.svelte';

/* What the bar writes to the address, which is all of it a wall reads as filters. */
const FILTER_PARAMS: ReadonlySet<string> = new Set<string>(['q', ...FIELDS]);

/** The first of a parameter that may have been given more than once. */
function firstOf(value: string | string[] | undefined): string | undefined {
	return Array.isArray(value) ? value[0] : value;
}

/** What the wall is: its own query, where its rows come from, and what it fixes. */
interface WallFacts {
	query(): Record<string, string>;
	source(): RowSource;
	/** An order this screen IS, rather than one somebody chose. */
	fixedSort(): string | undefined;
	/** Whether this wall floats what has been pinned. */
	pinnable(): boolean;
}

export class WallOrder {
	private wall: WallFacts;

	constructor(wall: WallFacts) {
		this.wall = wall;
	}

	/* Every value of a parameter, since the bar draws a chip per pair. */
	readonly fromAddress = $derived.by<Record<string, string | string[]>>(() =>
		this.wall.source().filterable !== true
			? {}
			: Object.fromEntries(
					[...new Set(address.url.searchParams.keys())]
						.filter((name) => FILTER_PARAMS.has(name))
						.map((name) => {
							const held = address.url.searchParams.getAll(name);
							return [name, held.length === 1 ? (held[0] as string) : held];
						})
				)
	);

	/* The order the address asks for. It wins on arrival (a search by meaning is a link) and is
	   cleared by a press in the panel, which is what lets the press be heard (`choose`). */
	readonly askedSort = $derived.by(() => address.url.searchParams.get('sort'));

	/* Whether this screen asks the model rather than the word index, the older spelling included. */
	readonly byMeaning = $derived.by(
		() => address.url.searchParams.get('meaning') === '1' || this.askedSort === RELEVANCE_BY_MEANING
	);

	/* Not read off `fullQuery`, which depends on the order this decides. */
	private readonly searching = $derived.by(() =>
		Boolean((firstOf(this.fromAddress.q) ?? this.wall.query().q ?? '').trim())
	);

	private readonly liking = $derived.by(
		() =>
			Boolean(
				firstOf(this.fromAddress.like) ??
				firstOf(this.fromAddress.similar_to_this) ??
				this.wall.query().like
			) || /(^|\s)\(*like:\S/i.test(firstOf(this.fromAddress.q) ?? this.wall.query().q ?? '')
	);

	/* What Similarity is close TO: a Smart Search's words, one file, or nothing. */
	readonly similarTo = $derived.by<SimilarTo>(() =>
		this.byMeaning &&
		similarToQuery(firstOf(this.fromAddress.q) ?? this.wall.query().q ?? '') === 'words'
			? 'words'
			: this.liking
				? 'file'
				: null
	);

	/* Closest match is the word index's score, so an order only where words are searched as words. */
	private readonly wordsMatched = $derived.by(
		() =>
			this.searching &&
			!this.byMeaning &&
			similarToQuery(firstOf(this.fromAddress.q) ?? this.wall.query().q ?? '') === 'words'
	);

	/* The wall's order until somebody picks one: Similarity where there is something to be close to,
	   Closest match where words are searched, newest first everywhere else. */
	private readonly wallDefault = $derived.by(() =>
		this.similarTo ? SIMILARITY : this.wordsMatched ? RELEVANCE : DEFAULT_SORT
	);

	readonly order = $derived.by(
		() => this.askedSort ?? (gridSort.value === DEFAULT_SORT ? this.wallDefault : gridSort.value)
	);

	/* Which shuffle, from the address, so it survives paging, Back and a link; a press mints one. */
	private readonly askedSeed = $derived.by(() => address.url.searchParams.get('seed'));

	/** Whether this wall understands an order. */
	offers(key: string): boolean {
		const source = this.wall.source();
		if (source.drops?.includes(key)) return false;
		if (source.sorts !== null) return source.sorts.includes(key);
		return !WALL_ONLY_ORDERS.has(key) || (source.adds ?? []).includes(key);
	}

	/* Closest match only where there are words; Similarity everywhere, dimmed with its reason; and
	   Shuffle again while shuffled, a press the chooser would not otherwise deliver. */
	readonly sortOptions = $derived.by(() => [
		...SORT_OPTIONS.filter(
			(option) => (option.value !== RELEVANCE || this.wordsMatched) && this.offers(option.value)
		).map((option) => {
			const label = this.wall.source().says?.[option.value] ?? option.label;
			return option.value === SIMILARITY
				? similarityChoice(this.similarTo, label)
				: { ...option, label };
		}),
		...(this.order === RANDOM && this.offers(RANDOM) ? [RESHUFFLE_OPTION] : [])
	]);

	/** Whether this wall can be put in an order now: offered, and not drawn dimmed. */
	private choosable(key: string): boolean {
		return this.sortOptions.some(
			(option) => option.value === key && !('disabled' in option && option.disabled)
		);
	}

	/* The order sent, clamped to one this source understands: the chosen order is shared across
	   screens, and a word the server refuses would leave an empty screen. */
	readonly askedOrder = $derived.by(() =>
		this.choosable(this.order)
			? this.order
			: this.choosable(this.wallDefault)
				? this.wallDefault
				: (this.sortOptions.find((option) => this.choosable(option.value))?.value ?? this.order)
	);

	readonly sort = $derived.by(() => this.wall.fixedSort() ?? this.askedOrder);

	/* Whether the answer depends on what Sift has described so far. */
	readonly describedMatters = $derived.by(() => this.byMeaning || this.sort === SIMILARITY);

	readonly fullQuery = $derived.by(() => ({
		...bothNarrowings(this.fromAddress, this.wall.query()),
		sort: this.sort,
		/* Which shuffle, on every page, so page two follows page one; only while shuffled. */
		...(this.sort === RANDOM && this.askedSeed !== null ? { seed: this.askedSeed } : {}),
		/* How to search, beside how to arrange; left out when off, to keep addresses clean. */
		...(this.byMeaning ? { meaning: '1' } : {}),
		/* Similarity to a FILE: said, because the order's key is also the older spelling of a Smart
		   Search, and the typed words would otherwise go to the model. */
		...(this.sort === SIMILARITY && !this.byMeaning ? { meaning: '0' } : {}),
		/* Whether pinned files float, from the same prop that offers the verb; left out when off. */
		...(this.wall.pinnable() ? { pinned_first: '1' } : {})
	}));

	/* The question as a string, which changes only when it does: opening a file makes a new query
	   object and asks nothing new. Without the order, which is watched on its own. */
	readonly asked = $derived.by(() =>
		JSON.stringify(
			Object.entries(this.fullQuery)
				.filter(([name]) => name !== 'sort')
				.sort(([one], [other]) => one.localeCompare(other))
		)
	);

	/*
	 * An order chosen from the panel. The choice is remembered across screens, and `sort` comes out
	 * of the address, where it would outrank the choice. A shuffle goes the other way: it writes
	 * `sort` and a fresh `seed` in, so paging, Back and a link walk the same draw, and the store is
	 * written after the address lands, or a page would be read under Random with no seed first.
	 * `replaceState`, so pressing three orders puts no steps in the way of Back.
	 */
	async choose(next: string): Promise<void> {
		if (next === RANDOM || next === RESHUFFLE) {
			const url = new URL(address.url);
			url.searchParams.set('sort', RANDOM);
			url.searchParams.set('seed', String(mintSeed()));
			// A page number means nothing in an arrangement that did not exist a moment ago.
			url.searchParams.delete('offset');
			await goto(url, { replaceState: true, keepFocus: true, noScroll: true });
			gridSort.set(RANDOM);
			return;
		}
		gridSort.set(next);
		if (!address.url.searchParams.has('sort') && !address.url.searchParams.has('seed')) return;
		const url = new URL(address.url);
		// The shuffle goes with the order that replaced it.
		url.searchParams.delete('seed');
		/* Where the address carries the legacy spelling of a search by meaning, the search is kept by
		   writing the mode into its own parameter before the order comes out. */
		if (url.searchParams.get('sort') === RELEVANCE_BY_MEANING) url.searchParams.set('meaning', '1');
		url.searchParams.delete('sort');
		url.searchParams.delete('offset');
		void goto(url, { replaceState: true, keepFocus: true, noScroll: true });
	}
}
