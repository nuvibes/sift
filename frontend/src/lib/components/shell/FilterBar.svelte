<script lang="ts">
	/*
	 * What is filtering this screen, always on screen and always removable: a filter somebody cannot
	 * see is how a person comes to believe their library has lost files. It belongs to the grid,
	 * since every wall of files can be filtered. Nothing here understands the query language: a clause
	 * is drawn from what the server made of the query, in the server's spelling, and written back with
	 * the one function that spells a rule.
	 */
	import { goto } from '$app/navigation';
	import { readStored, writeStored } from '$lib/shell/remembered.svelte';
	import { page } from '$app/state';
	import type { CheckState } from '$lib/components/common/Checkbox.svelte';
	import { Button, Chip, Select, TextInput } from '$lib/components/common';
	import FacetPanel from '$lib/components/shell/FacetPanel.svelte';
	import SavedFilters from './SavedFilters.svelte';
	import KeepFilter from '$lib/components/shell/KeepFilter.svelte';
	import BarDrawers from '$lib/components/shell/BarDrawers.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { ratingScale } from '$lib/library/rating.svelte';
	import { asAQuestion, savedSearches } from '$lib/search/saved-searches.svelte';
	import {
		FIELDS,
		chipLabel,
		parseQuery,
		rowFor,
		ruleText,
		type ParsedClause,
		type ParsedQuery,
		type Rule
	} from '$lib/search/search.svelte';
	import { untrack } from 'svelte';
	import {
		FILTERS_PANEL,
		PHONE_SHEET,
		ableTo,
		ordersOffered,
		screenBar,
		type Narrowing
	} from './screen-bar.svelte';
	import { phoneWidth } from '$lib/components/common/phone-width.svelte';
	import {
		FILING_PARAMETERS,
		facetKeys,
		picksAreRepeated,
		facetLabel,
		facetValueLabel,
		type FilingParameter
	} from './facet-labels';
	import {
		ChipNames,
		ChipOrder,
		asCounted,
		asQuery,
		flip,
		keptOnThisScreen,
		opposite,
		PendingPicks,
		placed,
		sides
	} from './filter-bar.svelte';
	import { countsAhead } from './facet-counts.svelte';
	import { partsOf, sameFilters, taken, written } from '$lib/search/query-parts';
	import FilterChip from './FilterChip.svelte';
	import ContextMenu from '$lib/components/common/ContextMenu.svelte';
	import VerbMenuItems from '$lib/components/common/VerbMenuItems.svelte';
	import { menuVerbs } from '$lib/components/common/verbs';
	import { WORDS } from './wall-words';
	import ScreenMenus from './ScreenMenus.svelte';
	import { stage } from './stage.svelte';
	import { keptClear } from './kept-clear.svelte';

	/** The parameter a username's files are filtered by. See `narrowedToUsername`. */
	const USERNAME = 'username';

	/* Drawn by the layout, once, above whatever screen is on, so it takes no props: what it needs is
	   in the store the screen publishes into. Its controls are drawn here, not handed up as snippets,
	   because a snippet is styled where it is rendered and would arrive undressed. */
	const tools = $derived(screenBar.tools);
	const query = $derived(tools.query ?? {});

	/* Which noun is on the wall: it decides the panel's columns, which parameters are filters rather
	   than paging, and how a multi-value pick is spelled (`picksAreRepeated`). Files unless said. */
	const subject = $derived(tools.subject ?? 'asset');

	/* Which parameter names are filters here: on a wall of files the language's whole field list, on
	   a wall of things that noun's facets, since `sort`, `prefix` and the anchor page that wall. */
	const filterNames = $derived<readonly string[]>(
		subject === 'asset' ? (FIELDS as readonly string[]) : facetKeys(subject)
	);

	/*
	 * What the panel reads and writes: the address, or a kept filter's draft. An edit has a draft so
	 * that editing a kept filter does not put it on the screen; the panel points at whichever is
	 * being edited, while the chips keep describing the screen. The write is the half that differs:
	 * an address is navigated to, a draft is assigned.
	 */
	const address: Narrowing = {
		read: () => new URLSearchParams(page.url.searchParams),
		write: (next) => {
			// A different question has its own results; page three of the old one is not where to land.
			next.delete('offset');
			// A pressed chip stays the same element, so the keyboard stays on it.
			void goto(`${page.url.pathname}?${next}`, { keepFocus: true });
		}
	};

	const keptDraft: Narrowing = {
		read: () => new URLSearchParams(savedSearches.editing?.draft ?? ''),
		write: (next) => {
			const open = savedSearches.editing;
			if (open) savedSearches.editing = { ...open, draft: next.toString() };
		}
	};

	/*
	 * What is on screen, which is what the chips describe: the screen's own filter (a Theater cell,
	 * which has no address) or the address; a kept filter being edited is not on screen until Save.
	 * A chip's press is a finished choice, as a kept filter is: it lands now (`choose`).
	 */
	const onScreen = $derived<Narrowing>(tools.narrowing ?? address);
	const describing = $derived<Narrowing>(
		onScreen.choose ? { ...onScreen, write: onScreen.choose } : onScreen
	);

	/* What the panel reads and writes: an open edit outranks everything, but only on the wall it was
	   opened on, or another wall's columns would point at a filter in another noun's vocabulary. */
	const editingHere = $derived(
		savedSearches.editing?.kind === subject ? savedSearches.editing : null
	);

	const narrowing = $derived<Narrowing>(editingHere !== null ? keptDraft : onScreen);

	/*
	 * The panel keeps its box while a draft is open, and past its end while the pointer is still in
	 * it: see `screenBar.shapeHeld` for the fault this answers. Told here because this is where the
	 * draft is scoped to the wall; the height itself is held by `BarDrawers`.
	 */
	$effect(() => {
		screenBar.drafting(editingHere !== null);
	});
	$effect(() => () => screenBar.drafting(false));

	/* What pointing at something that filters lights: the chips spread the screen's answer; the
	   columns do too, except while they edit a draft, which is not on screen. */
	const chipsPoint = $derived(tools.narrowing?.pointing);
	const columnsPoint = $derived(editingHere === null ? tools.narrowing?.pointing : undefined);

	/* Whether the address is what the SCREEN is filtered by, which decides how much of the screen is
	   part of the answer. A screen's own constraint (the person whose page this is) filters the
	   address and filters nothing else; a cell's filter is self-contained. */
	const onTheAddress = $derived(tools.narrowing === undefined);

	/* What the panel counts against: while an edit is open, the draft alone. */
	const panelQuery = $derived(
		asCounted(
			subject,
			editingHere === null && onTheAddress
				? { ...asQuery(narrowing.read()), ...query }
				: asQuery(narrowing.read())
		)
	);

	/* What the target filters by on its own control; the panel counts within it. See `Narrowing`. */
	const panelWithin = $derived(asQuery(narrowing.within?.() ?? new URLSearchParams()));

	/* `getAll`, not `get`: a facet says "these, and not that one" by repeating its parameter, and
	   comparing against the first value alone would draw the second as a locked chip. */
	const locked = $derived(
		onTheAddress
			? Object.entries(query).filter(
					([name, value]) => !describing.read().getAll(name).includes(value)
				)
			: []
	);

	/* The named parameters in the address. `?tags=beach` is what clicking a tag produces, and it is
	 * a filter somebody can take back off. `q` is the query language and is drawn below. */
	const named = $derived(
		[...describing.read().entries()].filter(([name]) => filterNames.includes(name) && name !== 'q')
	);

	/* One chip per value in the order first seen; Has and No are one chip, so a press keeps focus. */
	const order = new ChipOrder();
	const chips = $derived(
		order.lay(
			named,
			([name, value]) => ({ field: name, values: taken(value).values }),
			(field, value) => (opposite(subject, field, value) === undefined ? value : 'any|none')
		)
	);

	/* What the server made of the typed query. Fetched rather than split up here, for the reason
	 * this whole file gives: the client has no parser and must never grow one. */
	let parsed = $state<ParsedQuery>({ text: '', clauses: [], terms: {}, problems: [] });

	$effect(() => {
		const asked = describing.read().get('q') ?? '';
		void (async () => {
			parsed = await parseQuery(asked);
		})();
	});

	/** Why a clause's value could not be read, when the server said so. Found by field and value,
	 *  which is how a clause carrying several values names the one that is wrong. */
	function problemFor(clause: ParsedQuery['clauses'][number]): string | undefined {
		return parsed.problems?.find(
			(one) => one.field === clause.field && clause.values.includes(one.value)
		)?.reason;
	}

	/** The whole query, written from its clauses. The clause text is the SERVER's spelling. */
	function queryFrom(clauses: string[], text: string): string {
		return [...clauses, text].filter(Boolean).join(' ').trim();
	}

	/* A different typed query, through the filtering rather than straight to the address, so a
	   draft or a Theater cell is not left behind by a navigation. */
	function ask(next: string) {
		const wanted = describing.read();
		/* Typed in the stars on screen, stored in the units the rows hold. */
		const asked = ratingScale.storedQuery(next);
		if (asked) wanted.set('q', asked);
		else wanted.delete('q');
		editing = null;
		describing.write(wanted);
	}

	/*
	 * THE WALL'S OWN WORDS where its box keeps them under a name other than `q` (Loops): read from
	 * what is on screen, drawn as the chip the typed part of `q` wears, and taken off by its cross.
	 */
	const ownWords = $derived(
		tools.words && tools.words !== WORDS ? (describing.read().get(tools.words) ?? '').trim() : ''
	);

	function dropOwnWords() {
		if (!tools.words) return;
		const next = describing.read();
		next.delete(tools.words);
		describing.write(next);
	}

	/** The typed words off the query, every clause kept. */
	function dropText() {
		ask(
			queryFrom(
				parsed.clauses.map((clause) => clause.query),
				''
			)
		);
	}

	function dropClause(at: number) {
		ask(
			queryFrom(
				parsed.clauses.filter((_, index) => index !== at).map((clause) => clause.query),
				parsed.text
			)
		);
	}

	function replaceClause(at: number, written: string) {
		if (!written) {
			dropClause(at);
			return;
		}
		ask(
			queryFrom(
				parsed.clauses.map((clause, index) => (index === at ? written : clause.query)),
				parsed.text
			)
		);
	}

	/*
	 * A chip's verbs, one value at a time, through the writer a tick in the panel goes through
	 * (`pick`), pointed at the filtering the chip was drawn from: the screen's on the bar, the draft
	 * under Editing. One value, so one tag of two in a column can be refused alone.
	 */
	function flipValue(where: Narrowing, name: string, value: string) {
		flip(subject, name, value, where);
	}

	function dropValue(where: Narrowing, name: string, value: string) {
		pick(name, value, 'off', where);
	}

	/* Swap one parameter between any-of and all-of (pipe against comma), leaving a refusal kept as a
	   second parameter of the same name alone. */
	function switchMatch(where: Narrowing, name: string, value: string) {
		const held = taken(value);
		const next = where.read();
		const kept = next
			.getAll(name)
			.map((each) => (each === value ? written({ ...held, all: !held.all }) : each));
		where.write(placed(next, name, kept));
	}

	function clearAll() {
		const wanted = describing.read();
		for (const name of [...wanted.keys()]) wanted.delete(name);
		describing.write(wanted);
	}

	/*
	 * The username this wall is filtered to by `?username=`: not a word of the query language (the
	 * address carries its id), so it has its own chip, named from the server's page when it matches
	 * the id and "a username" otherwise. The filter is in force either way, and its cross is the way
	 * out.
	 */
	const narrowedToUsername = $derived.by(() => {
		if (subject !== 'asset') return null;
		const id = describing.read().get(USERNAME);
		if (!id) return null;
		const known = tools.username?.id === id ? tools.username : null;
		return {
			label: known
				? known.site
					? `@${known.username} on ${known.site}`
					: `@${known.username}`
				: 'a username',
			/* The chip opens the person behind the username, where there is one. */
			href: known?.person_id ? `/people/${encodeURIComponent(known.person_id)}` : undefined
		};
	});

	/** Take the username off, and nothing else: the other chips on the row are other filters. */
	function dropUsername() {
		const next = describing.read();
		next.delete(USERNAME);
		describing.write(next);
	}

	/* The names the chips show for the ids in the address. See `ChipNames`. */
	const names = new ChipNames({
		subject: () => subject,
		describing: () => describing,
		named: () => named,
		clauses: () => parsed.clauses
	});

	/** Take one History line off, and nothing else: another line of the same kind stays. */
	function dropFiling(parameter: FilingParameter, value: string) {
		const next = describing.read();
		const kept = next.getAll(parameter).filter((one) => one !== value);
		describing.write(placed(next, parameter, kept));
	}

	/* Whether anything on this row can be taken off, the username and History lines included, which
	   are also kept with a saved filter. The screen's own constraint does not count: it is not drawn. */
	const anything = $derived(
		named.length > 0 ||
			parsed.clauses.length > 0 ||
			Boolean(parsed.text) ||
			Boolean(ownWords) ||
			narrowedToUsername !== null ||
			names.filings.length > 0
	);

	/* The names a kept filter is compared by: this wall's filters, and on a wall of files the username
	   and History lines too, which draw chips of their own rather than through `filterNames`. */
	const keptNames = $derived<readonly string[]>(
		subject === 'asset' ? [...filterNames, USERNAME, ...FILING_PARAMETERS] : filterNames
	);

	/* Editing a chip where it stands, offered only for a clause `rowFor` can write back unchanged. The
	   ways a filter can match, in the query language's own values (`ruleText` spells a rule). */
	const MATCHES = [
		{ value: 'any', label: 'any of' },
		{ value: 'all', label: 'all of' },
		{ value: 'none', label: 'none of' },
		{ value: 'present', label: 'has any' },
		{ value: 'empty', label: 'has none' }
	];

	/* Whether the panel was left open: a fact about this window, so the browser keeps it. */
	const PANEL_KEY = 'sift.filters.panel';

	function remembered(): boolean {
		return readStored(PANEL_KEY) === 'open';
	}

	/* The facets are one of the bar's panels; only they are reopened on arrival. */
	const panelOpen = $derived(screenBar.open === FILTERS_PANEL);

	$effect(() => {
		if (untrack(() => screenBar.open) === null && ableTo(tools.filterable) && remembered()) {
			screenBar.show(FILTERS_PANEL);
		}
	});

	/* Kept from the state, not the press (three ways in), and only where the screen has facets. */
	$effect(() => {
		if (!ableTo(tools.filterable)) return;
		const open = panelOpen;
		writeStored(PANEL_KEY, open ? 'open' : 'shut');
	});

	/* The screen's own panels, and the one open, checked against the list so a stale id draws none. */
	const panels = $derived(tools.panels ?? []);
	const openPanel = $derived(panels.find((one) => one.id === screenBar.open) ?? null);

	const showing = $derived.by(() => {
		if (screenBar.open === FILTERS_PANEL) return ableTo(tools.filterable) ? FILTERS_PANEL : null;
		/* The order is a menu on the row of triggers (`ScreenMenus`); the kept filters are at the
		   foot of the panel this bar opens (`SavedFilters`). */
		return openPanel?.id ?? null;
	});
	/* The panels drawn at the tail of this row: the leading ones are named triggers on the top bar,
	   and one whose trigger is at the top is drawn from neither end, though it opens here. */
	const trailing = $derived(panels.filter((one) => !one.lead && !one.atTheTop));

	/* How far below this row the open panel hangs, so the screen's title row stays in reach. */
	let row = $state<HTMLElement | null>(null);
	let clear = $state(0);
	$effect(() => {
		const title = keptClear.row;
		if (showing === null || row === null) return;
		const bar = row;
		const measure = () => {
			const below = title
				? title.getBoundingClientRect().bottom - bar.getBoundingClientRect().bottom
				: 0;
			clear = Math.max(0, Math.round(below));
		};
		measure();
		const watching = new ResizeObserver(measure);
		if (title) watching.observe(title);
		window.addEventListener('resize', measure);
		return () => {
			watching.disconnect();
			window.removeEventListener('resize', measure);
		};
	});

	/* The phone's sheet: the orders and the facets together, from the one control a phone's bar
	   draws for both, wired here because the facets are. A press on an order lands on `onSort`. */
	const sheetOpen = $derived(phoneWidth.yes && screenBar.open === PHONE_SHEET);
	const phoneOrders = $derived(ordersOffered(tools.sorts));
	const phoneOrder = $derived(tools.sort ?? phoneOrders[0]?.value);

	/* Whether the screen's menus are drawn on this row: while the window is filled, and whenever the
	   top bar has no room. `nothing` reads it too, or the row would hide with the menus in it. */
	const menusHere = $derived(stage.filling || !screenBar.roomOnTopBar);

	/* Whether this row holds anything, which decides whether it draws its ground: what is in it, not
	   what the screen offers, or Browse would draw an empty slab. An empty row keeps its height, so
	   every title stays on one line. */
	const nothing = $derived(!anything && trailing.length === 0 && !tools.extra && !menusHere);
	const handedDown = $derived(menusHere || trailing.some((one) => one === screenBar.sizeHome));

	/*
	 * A column's picks, written into the column's named parameter rather than the typed query, so
	 * clicking and typing stay different acts, and a second name widens the column. Read from and
	 * written to `narrowing` unless told otherwise; see `filter-bar.svelte.ts`.
	 */
	function chosenIn(facet: string): string[] {
		const { included, excluded } = sides(facet, narrowing);
		return [...included.values, ...excluded.values];
	}

	/* A pick is drawn on the press, while its address is on its way; the counts are asked ahead. */
	const picks = new PendingPicks(() => ({ subject, narrowing, path: page.url.pathname }));
	countsAhead(() => (editingHere === null ? { query: panelQuery, within: panelWithin } : null));
	/** Which of the three a single value is in. */
	function stanceOf(facet: string, value: string, where: Narrowing = narrowing): CheckState {
		return picks.stance(facet, value, where);
	}

	function pick(facet: string, value: string, next?: CheckState, where: Narrowing = narrowing) {
		picks.pick(facet, value, next, where);
	}

	/* Replace a whole named filter with one value, or clear it: `added:` takes a span, not ticks. */
	function setWhole(facet: string, value: string) {
		const next = narrowing.read();
		next.delete(facet);
		if (value) next.set(facet, value);
		narrowing.write(next);
	}

	/* Keeping this search under a name, in the sheet `KeepFilter` draws. */
	let naming = $state(false);

	/* The query a saved search is kept as, the screen's own constraint included: saved from a person's
	   page with a tag on the bar, it means that person, tagged that. */
	const query_now = $derived(
		new URLSearchParams(
			onTheAddress
				? { ...Object.fromEntries(describing.read().entries()), ...query }
				: Object.fromEntries(describing.read().entries())
		).toString()
	);

	/* The fields the filter being edited names, from the stored filter so the columns do not
	   reshuffle under the pointer as values go on and off. */
	const editingFields = $derived.by(() => {
		const open = editingHere;
		if (open === null) return [];
		const kept = savedSearches.items.find((one) => one.id === open.id);
		return kept ? partsOf(kept.query, filterNames).map((part) => part.field) : [];
	});

	/*
	 * Which kept filter this screen is, by comparing rather than remembering the press, so the name
	 * goes the instant the screen stops being that filter. Only once the list is loaded: it is loaded
	 * by the panel, and asking here would cost every page load.
	 */
	const appliedKept = $derived.by(() => {
		if (!savedSearches.loaded) return null;
		/* What is being filtered, a Theater cell included. */
		const here = partsOf(describing.read().toString(), keptNames);
		if (here.length === 0) return null;
		/* This wall's own kept filters only: two walls may hold one name. */
		return (
			savedSearches.on(subject).find((kept) => sameFilters(partsOf(kept.query, keptNames), here)) ??
			null
		);
	});

	/* And said to whoever else needs it: a sitting opened over this screen records which kept
	   filter it was showing. See `screenBar.keptInForce`. Taken back when the bar goes, so a screen
	   drawn without one never inherits the last one's answer. */
	$effect(() => {
		screenBar.keptInForce = appliedKept?.id ?? null;
		return () => {
			screenBar.keptInForce = null;
		};
	});

	/*
	 * Putting a kept filter on, which is pressing the pill, and a finished decision, so the panel
	 * shuts. It goes on whatever is being filtered: a Theater cell, or this screen, which is always a
	 * wall the filter means something on (sending every filter to `/browse` would take a person's
	 * Files tab out to the whole library).
	 */
	function applyKept(query: string) {
		const asked = new URLSearchParams(asAQuestion(query));
		if (tools.narrowing !== undefined) {
			(tools.narrowing.choose ?? tools.narrowing.write)(asked);
			return;
		}
		describing.write(keptOnThisScreen(describing.read(), asked, keptNames));
		screenBar.close();
	}

	let editing = $state<number | null>(null);
	let draft = $state<Rule | null>(null);

	function edit(at: number, clause: ParsedClause) {
		const row = rowFor(clause);
		if (row === null) return;
		editing = editing === at ? null : at;
		draft = editing === null ? null : { ...row };
	}

	function applyEdit(at: number) {
		if (draft === null) return;
		replaceClause(at, ruleText(draft));
	}

	/** Whether a clause excludes rather than includes, which has to read without hovering it. */
	function excludes(clause: ParsedClause): boolean {
		return clause.negated || clause.present === false;
	}
</script>

<!--
	The bar is here on every screen, empty where there is nothing to put in it: a row that comes and
	goes is one people stop looking at. It must never offer a control that does nothing. The panel
	hangs from this row, so it travels with the bar to the bottom edge while the window is filled.
-->
<!-- The facets, drawn in the desktop's drawer or the phone's sheet: one panel, two frames. -->
{#snippet facetPanel()}
	<!-- The kept filters, at the foot of the columns, each wall drawing its own. `current` is the
	     address's filters alone: the screen's constraint travels in the query a filter was saved with. -->
	{#snippet savedFoot()}
		<SavedFilters
			current={describing.read().toString()}
			kind={subject}
			fields={filterNames}
			onapply={applyKept}
			onflip={(field, value) => flipValue(keptDraft, field, value)}
			ondrop={(field, value) => dropValue(keptDraft, field, value)}
			onswitchmatch={picksAreRepeated(subject)
				? undefined
				: (field, value) => switchMatch(keptDraft, field, value)}
			editable
		/>
	{/snippet}
	<FacetPanel
		{subject}
		head={tools.narrowingLead}
		query={panelQuery}
		onpick={pick}
		onset={setWhole}
		stance={stanceOf}
		chosen={chosenIn}
		lead={editingFields}
		total={tools.count}
		foot={savedFoot}
		pointing={columnsPoint}
		within={panelWithin}
		fixed={tools.fixed}
	/>
{/snippet}

<!--
	Every chip in force, drawn once: `live` on the bar, and off in the sheet that keeps them, where
	the same chips are drawn with nothing on them acting.
-->
{#snippet inForce(live: boolean)}
	{#if narrowedToUsername}
		<!-- The username `?username=` filters to; its body opens the person behind it. -->
		<FilterChip
			field="username"
			values={[narrowedToUsername.label]}
			href={live ? narrowedToUsername.href : undefined}
			onremove={live ? dropUsername : undefined}
		/>
	{/if}

	<!-- One History line's files, one chip per line. See `filings`. -->
	{#each names.filingChips as one (`${one.parameter} ${one.value}`)}
		<FilterChip
			field={one.parameter}
			shown={one.shown}
			values={[one.label]}
			href={live ? one.href : undefined}
			onremove={live ? () => dropFiling(one.parameter, one.value) : undefined}
		/>
	{/each}

	{#if appliedKept}
		<Chip
			icon="filter_alt"
			onremove={live ? clearAll : undefined}
			removeLabel="Clear the saved filter {appliedKept.name}"
		>
			{appliedKept.name}
		</Chip>
	{/if}

	<!-- Every chip here is the shared `FilterChip`, one per value so each is refused on its own;
	     keyed by facet and value, so a flipped chip is the same element. All-of only on the file
	     wall, because the entity routes read a repeat as either. -->
	{#each chips as entry (entry.key)}
		{@const name = entry.field}
		{@const one = entry.value}
		{@const value = entry.from[1]}
		{@const held = taken(value)}
		<!-- A chip naming a thing with verbs of its own (one artist on the Music wall) answers a
		     right-click with that thing's menu: the verbs the screen declares (`chipVerbs`). -->
		{@const thingVerbs = live
			? (tools.chipVerbs?.(name, one, facetValueLabel(name, one)) ?? [])
			: []}
		{#snippet chip()}
			<FilterChip
				field={name}
				shown={subject === 'asset' ? null : facetLabel(name, subject)}
				values={[name === 'in' ? names.folderSaid(one) : one]}
				of={held.values.length}
				all={held.all}
				excluded={held.excluded}
				onselect={live ? () => flipValue(describing, name, one) : undefined}
				onremove={live ? () => dropValue(describing, name, one) : undefined}
				onswitch={!live || picksAreRepeated(subject)
					? undefined
					: () => switchMatch(describing, name, value)}
			/>
		{/snippet}
		{#if thingVerbs.length > 0}
			<ContextMenu label="Actions for {facetValueLabel(name, one)}">
				{@render chip()}
				{#snippet items()}
					<VerbMenuItems ids={[one]} subjectId={one} verbs={menuVerbs(thingVerbs)} />
				{/snippet}
			</ContextMenu>
		{:else}
			{@render chip()}
		{/if}
	{/each}

	<!-- Keyed by position as well as text: the server's clauses are not deduped. -->
	{#each parsed.clauses as clause, at (`${at}:${clause.query}`)}
		{@const editable = rowFor(clause) !== null}
		<!-- A typed filter, in the same chip: it may name no dimension, a press opens its editor, and the
		     strike follows the negation, so a "has none" question is not struck through. -->
		{#snippet clauseChip()}
			<FilterChip
				field={clause.field}
				values={clause.present !== null ? [chipLabel(clause)] : clause.values}
				all={clause.present === null && clause.match !== 'any'}
				excluded={excludes(clause)}
				struck={clause.negated}
				problem={problemFor(clause)}
				onselect={live && editable ? () => edit(at, clause) : undefined}
				onremove={live ? () => dropClause(at) : undefined}
			/>
		{/snippet}
		{#if problemFor(clause)}
			<!-- The reason on hover, and the chip drawn as a problem: the filter matches
		     nothing, which is the honest answer to a value nobody could act on, and the
		     wall underneath says the same. -->
			<Tooltip label={problemFor(clause) ?? ''} placement="bottom">
				{@render clauseChip()}
			</Tooltip>
		{:else}
			{@render clauseChip()}
		{/if}

		{#if live && editing === at && draft !== null}
			<!-- In the flow of the bar, so the results underneath stay visible while they change. -->
			<div class="editor" role="group" aria-label="Edit this filter">
				<label>
					<span>Match</span>
					<Select
						value={draft.how}
						options={MATCHES}
						label="How this filter matches"
						onValueChange={(next) => draft && (draft.how = next)}
					/>
				</label>
				{#if draft.how !== 'present' && draft.how !== 'empty'}
					<label>
						<span>Values</span>
						<TextInput bind:value={draft.values} placeholder="beach, sunset" />
					</label>
				{/if}
				<Button tone="primary" onclick={() => applyEdit(at)}>Apply</Button>
			</div>
		{/if}
	{/each}

	<!-- The words a wall is searched by, one shape wherever they are kept: the typed part of
	     `q`, or the name the wall's own box writes (`tools.words`). The cross takes them off. -->
	{#if parsed.text}
		<Chip
			tone="quiet"
			onremove={live ? dropText : undefined}
			removeLabel="Remove the words {parsed.text}"
		>
			{#snippet lead()}<span class="field">words:</span>{/snippet}
			{parsed.text}
		</Chip>
	{/if}
	{#if ownWords}
		<Chip
			tone="quiet"
			onremove={live ? dropOwnWords : undefined}
			removeLabel="Remove the words {ownWords}"
		>
			{#snippet lead()}<span class="field">words:</span>{/snippet}
			{ownWords}
		</Chip>
	{/if}
{/snippet}

<div class="bar-row" bind:this={row} style:--panel-clear="{clear}px">
	<!-- The row opens as the drawer does and drops into place on the spring; `inert` while folded. -->
	<div class="bar-fold" class:folded={nothing} inert={nothing || undefined}>
		<div class="bar-clip">
			<div class="bar" class:nothing class:quiet={tools.quiet} class:handed={handedDown}>
				<!-- The screen's menus, here while the window is filled (the top bar is not drawn) and
				     where the top bar has no room (`ROOM_ON_THE_TOP_BAR`): one component, in one place. -->
				{#if menusHere}
					<ScreenMenus />
				{/if}

				<!-- The chips, on the screens the query language applies to and on a screen whose address
				     carries filters for a tab. -->
				{#if ableTo(tools.filterable) || named.length > 0}
					<div class="filters">
						<!-- The screen's own constraint is not drawn: the heading says it better. What is filtered,
						     when it is not this screen (a Theater cell), comes first, with no cross. -->
						{#if tools.narrowingName}
							<Chip icon="filter_alt">{tools.narrowingName}</Chip>
						{/if}

						<!-- Whatever the screen wants BESIDE that chip. The one caller is Theater, whose two controls
				     about the whole wall have nowhere else to be while the screen is filled: the top bar they
				     live on is not drawn then. See `besideTheName`. -->
						{@render tools.besideTheName?.()}

						<!-- Every chip that filters, in one box that lays nothing out (`display: contents`), so
				     pointing at any of them lights what they narrow. The name chip before it and the
				     wall's two verbs are left outside: neither filters anything. -->
						<span {...chipsPoint} class="narrows">
							{@render inForce(true)}

							<!-- A word at the end of the chips rather than a slab among them: this acts on the row it
				     sits in and it is not one of the things ON that row. See the button's `quiet` tone. -->
							{#if anything}
								<Button tone="quiet" onclick={clearAll}>Clear all</Button>
							{/if}
						</span>
					</div>
				{/if}

				<div class="tools">
					{#if anything}
						<Tooltip label="Add to saved filters">
							<Button
								tone="ghost"
								icon="filter_plus"
								aria-label="Add to saved filters"
								onclick={() => (naming = true)}
							/>
						</Tooltip>
					{/if}

					<!-- The panels this screen drops open, one at a time. -->
					{#each trailing as one (one.id)}
						<Tooltip label={one.label}>
							<Button
								tone="ghost"
								icon={one.icon}
								aria-label={one.label}
								pressed={screenBar.open === one.id}
								onclick={() => screenBar.toggle(one.id)}
							/>
						</Tooltip>
					{/each}

					<!-- Whatever this screen has that no other does. Components only, by convention: a snippet is
			     styled where it is RENDERED, so a bare element carrying the screen's own class would
			     arrive here undressed. -->
					{@render tools.extra?.()}
				</div>
			</div>
		</div>
	</div>

	<!-- Keeping this filter under a name, with the chips it keeps in front of whoever names it. -->
	{#snippet keptChips()}{@render inForce(false)}{/snippet}
	<KeepFilter bind:open={naming} query={query_now} {subject} chips={keptChips} />

	<BarDrawers
		{showing}
		panel={openPanel}
		facets={facetPanel}
		filterable={ableTo(tools.filterable)}
		{sheetOpen}
		orders={phoneOrders}
		order={phoneOrder}
		onsort={(next) => tools.onSort?.(next)}
	/>
</div>

<style>
	/*
	 * While the screen is filled this row is the other half of the bar at the bottom, so it takes the
	 * pill's dress: the facts panel's surface and blur, the selection bar's corner and shadow. Only
	 * the row: the panel under it keeps its width, as a place somebody works.
	 */
	:global(.screen-box:fullscreen) .bar:not(.nothing) {
		inline-size: fit-content;
		max-inline-size: calc(100% - var(--space-8));
		margin-inline: auto;
		padding: var(--space-2) var(--space-3);
		border: 1px solid var(--sift-line);
		border-radius: var(--radius-xl);
		background: var(--sift-scrim);
		backdrop-filter: blur(var(--blur-glass));
		box-shadow: var(--elev-3);
		/* Its controls sit on that scrim too, as `BarPanel`'s do. */
		--sift-surface-3: var(--sift-scrim);
	}

	/* What the panel hangs from. */
	.bar-row {
		position: relative;
	}

	.bar-row > :global(.drawer) {
		inset-block-start: calc(100% + var(--panel-clear));
	}

	/* Faded and out of reach, as the wall's own bar fades. */
	.bar.quiet {
		opacity: 0;
		pointer-events: none;
	}

	.bar {
		transition: opacity var(--dur-slow) var(--ease);
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: var(--space-2);
		/* A control's height and the padding, so an empty row is as tall as a full one. */
		min-block-size: calc(var(--control-height) + var(--space-1) * 2);
		/* The panel's inset, ground, edge and corner: two halves of one thing. */
		margin-inline: var(--space-6);
		margin-block: var(--space-2);
		padding-inline: var(--space-3);
		padding-block: var(--space-1);
		border: 1px solid var(--sift-line);
		border-radius: var(--radius-lg);
		background: var(--sift-surface-2);
	}

	/*
	 * Nothing in it, so folded away (52px above every unfiltered title is not worth a row), as a
	 * one-row grid whose fraction animates, so it can be seen going. The spring is on the row inside.
	 */
	.bar-fold {
		display: grid;
		grid-template-rows: 1fr;
		transition: grid-template-rows var(--dur-base) var(--ease);
	}

	.bar-fold.folded {
		grid-template-rows: 0fr;
	}

	/* The clipped row. `min-block-size: 0` so the fraction is what decides its height. */
	.bar-clip {
		overflow: hidden;
		min-block-size: 0;
	}

	/* Arriving, the row drops into place on the spring; going, it rises on the plain ease. */
	.bar-fold.folded .bar {
		translate: 0 calc(var(--space-6) * -1);
		opacity: 0;
		transition:
			translate var(--dur-base) var(--ease),
			opacity var(--dur-base) var(--ease);
	}

	.bar-fold:not(.folded) .bar:not(.quiet) {
		transition:
			translate var(--dur-slow) var(--ease-spring),
			opacity var(--dur-slow) var(--ease);
	}

	/* Holding what the top bar handed down (the menus, the Tile size press), the row is its place at
	   this width, not something arriving: an auto row, whole immediately, so the title never slides. */
	.bar-clip:has(> .bar.handed) {
		grid-row: 2;
	}

	.bar-clip:has(> .bar.handed) > .bar {
		transition: opacity var(--dur-slow) var(--ease);
	}

	:global(:root[data-motion='reduce']) .bar-fold .bar {
		translate: none;
	}

	/* The chips' own box lays nothing out: it exists for the pointer. */
	.narrows {
		display: contents;
	}

	.filters {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: var(--space-2);
		flex: 1;
		min-width: 0;
	}

	.tools {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		margin-inline-start: auto;
	}

	/* The prefix on the words chip, the one chip here that is not a `FilterChip`: free text names no
	   dimension and comes off in the search box. */
	.field {
		font: var(--text-data);
		opacity: 0.72;
	}

	.editor {
		display: flex;
		align-items: flex-end;
		gap: var(--space-3);
		padding: var(--space-3);
		border: 1px solid var(--sift-line);
		border-radius: var(--radius-md);
		background: var(--sift-surface-2);
		box-shadow: var(--elev-2);
	}

	.editor label {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		color: var(--sift-ink-3);
		font: var(--text-label);
	}
</style>
