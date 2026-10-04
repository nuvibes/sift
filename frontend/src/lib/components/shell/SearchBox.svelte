<script lang="ts">
	/* The search box and the list under it. The server fills the groups (the filters matching what
	   is typed, the things that match it, past searches), so the query language documents itself:
	   this sends the line and draws the answer, and never decides where a token starts. The query
	   lives in the address; only the half-typed text is held here. */
	/* `Chip` is aliased because this file already has a `Chip`: the type of one parsed filter. */
	import {
		Chip as ChipView,
		TextInput,
		Pressable,
		Tabs,
		type TabChoice
	} from '$lib/components/common';
	import Scroller from '$lib/components/common/Scroller.svelte';
	import { onDestroy, onMount, tick, untrack } from 'svelte';
	import { afterNavigate, goto } from '$app/navigation';
	import { page } from '$app/state';
	import Icon from '$lib/components/Icon.svelte';
	// Shared, because the settings pane can switch searching by meaning off under an open box.
	import { availability } from '$lib/jobs/semantic-runs.svelte';
	import { screenBar } from './screen-bar.svelte';
	import SearchSuggestions from '$lib/components/shell/SearchSuggestions.svelte';
	import { leaves as leavesFor, pageFor, placeOf } from './search-places';
	import { matches, shortcut } from '$lib/shell/shortcuts';
	import {
		chipPrefixLength,
		chipText,
		chipValueText,
		chipsFromParsed,
		dropToken,
		parseQuery,
		quoted,
		readAddress,
		addressQuery,
		sameChip,
		searchBox,
		serialise,
		type Chip,
		type Remembered,
		type Row
	} from '$lib/search/search.svelte';

	interface Props {
		/** Take the suggestion list for as long as this box exists, so a box behind it draws none.
		 *  Set by the Ctrl-F overlay, which draws this same component over the top bar's copy. */
		exclusive?: boolean;
	}

	let { exclusive = false }: Props = $props();

	/** This box's identity, for the claim above. A symbol so two boxes can never collide. */
	const me = Symbol('search-box');

	$effect(() => {
		if (!exclusive) return;
		searchBox.claim(me);
		return () => searchBox.release(me);
	});

	let input = $state<HTMLInputElement | null>(null);
	/** Whether the box is being used, which is when the keyboard hint stops being a help. */
	let focused = $state(false);

	/* The query in two halves: the filters decided, as chips, and what is still being typed. Only
	   `serialise` joins them, chips first, which is what maps the server's offsets back to
	   positions here. */
	let chips = $state<Chip[]>([]);
	let typed = $state('');

	/* A field chosen with no value yet, drawn as a chip at once so one Backspace takes it off. Not
	   a `Chip`: a chip is a field and a value. */
	let pending = $state<string | null>(null);

	/* Free text typed before a field was chosen, serialised before the token (`holiday tags:`, not
	   `tags:holiday`). Set only while `pending` is. */
	let leading = $state('');

	/** What the server is being told the box contains. The pending field is part of the query: it is
	 *  what makes the dropdown offer values for THAT field rather than words from everywhere. */
	const whole = $derived(
		serialise(chips, pending ? `${leading ? `${leading} ` : ''}${pending}:${typed}` : typed)
	);

	/* Where `typed` begins inside `whole`: the chips, any leading text and the pending `field:`. */
	const typedStartsAt = $derived(
		chipPrefixLength(chips) +
			(leading ? leading.length + 1 : 0) +
			(pending ? pending.length + 1 : 0)
	);

	/** Empty the box, in all four places a query is kept, when a picked row leaves for a page that
	   carries no query. */
	function forgetWhatIsTyped(): void {
		chips = [];
		pending = null;
		leading = '';
		typed = '';
	}

	/** Hand the leading text back to the box. Called wherever a pending field stops being pending. */
	function releaseLeading(): string {
		const held = leading;
		leading = '';
		return held;
	}

	/* The last query followed out of the address; `null` so the first run follows. */
	let followed = $state<string | null>(null);

	/* The address is the truth, so Back, a pasted link and a picked suggestion behave alike. It
	   follows a CHANGE of query only: paging and the order write the address too. */
	$effect(() => {
		/* Nothing, on a wall with a box of its own: `q` is that box's words there. */
		const asked = addressQuery(page.url.searchParams, screenBar.ownBox);
		const what = readAddress(
			asked,
			untrack(() => followed),
			untrack(() => whole)
		);
		if (what === 'ignore') return;
		followed = asked;
		if (what === 'record') return;
		void untrack(async () => {
			const parsed = await parseQuery(asked);
			/* An address followed since this one left (a wall's box claimed the screen, Back was
			   pressed twice) owns the box now; the older answer arriving late must not overwrite it. */
			if (followed !== asked) return;
			const split = chipsFromParsed(parsed);
			chips = split.chips;
			typed = split.text;
			// The address is the whole truth about the query, and a half-finished filter is not in it.
			pending = null;
		});
	});

	/* A typed filter the server recognizes (`/search/parse`: the client has no parser and must not
	   grow one) becomes a chip at a deliberate end of a word (a space, or leaving the box), never
	   mid-word (`tags:be`) and never while a field is pending. */
	async function chipWhatIsTyped(): Promise<void> {
		if (pending !== null) return;
		const query = whole.trim();
		if (!query) return;

		const parsed = await parseQuery(query);
		const split = chipsFromParsed(parsed);
		// Nothing recognized, or nothing that would change: leave the box and the caret as they are.
		if (split.chips.length === 0) return;
		if (serialise(split.chips, split.text).trim() !== query) return;
		if (
			split.chips.length === chips.length &&
			split.chips.every((chip, at) => sameChip(chip, chips[at]))
		) {
			return;
		}

		chips = split.chips;
		typed = split.text;
		await tick();
		const end = input?.value.length ?? 0;
		input?.setSelectionRange(end, end);
	}

	const showing = $derived(searchBox.open && searchBox.rows.length > 0 && searchBox.ownedBy(me));

	/* The letters the rows are bold for, from the query the answer was computed for (from
	   `replace_from`), so the bold belongs to the rows drawn. */
	const typing = $derived.by(() => {
		const from = nameSpan(searchBox.suggestions);
		if (from === null) return '';
		/* Unquoted: the quote is punctuation about the query, not a letter of the value. */
		return searchBox.suggestions.for_query.slice(from).trim().replace(/^["']/, '');
	});

	/* Where a matched NAME starts, which a name with a space need not share with the filter's span.
	   Null where the server sent one span. */
	function nameSpan(offered: typeof searchBox.suggestions): number | null | undefined {
		return offered.matched_from ?? offered.replace_from;
	}

	/* Whether a row leaves the page, asked about the list's own token. See `search-places`. */
	const leaves = (row: Row): boolean => leavesFor(row, searchBox.suggestions.token);

	/** Put a chip in, unless it is already there, and take what made it out of the text. */
	function addChip(chip: Chip, from: number) {
		if (!chips.some((each) => sameChip(each, chip))) chips = [...chips, chip];
		// The word that was being typed becomes the chip, so it must not also stay behind as text.
		const at = from - chipPrefixLength(chips.slice(0, -1));
		typed = at >= 0 && at <= typed.length ? typed.slice(0, at) : '';
	}

	/* The joining word stays in the text where it was typed: chips serialise in front, so a token
	   completed after `or` would otherwise join nothing. */
	const JOIN = 'or';

	function endsWithJoin(text: string): boolean {
		const words = text.trim().split(/\s+/);
		return words[words.length - 1].toLowerCase() === JOIN;
	}

	/** Splice a completed token into the text where it was typed, and let the parser re-read it. */
	function writeAndReparse(before: string, field: string, value: string) {
		typed = `${before}${field}:${quoted(value)}`;
		void chipWhatIsTyped();
	}

	/** Take one back off. */
	function removeChip(at: number) {
		chips = chips.filter((_, index) => index !== at);
		searchBox.suggest(whole);
		input?.focus();
	}

	/** Abandon a filter that was chosen and never given a value. One press, the whole thing. */
	function dropPending() {
		pending = null;
		/* The half-typed value goes with the field; anything typed before the field comes back. */
		const held = releaseLeading();
		typed = held ? `${held} ` : '';
		searchBox.suggest(whole);
		input?.focus();
	}

	/* Backing into the last chip takes it apart: the field stays pending and the value comes back
	   as quoted text. The next press past an empty value drops the field (`dropPending`). */
	async function unchipLast() {
		const last = chips[chips.length - 1];
		if (last === undefined) return;
		chips = chips.slice(0, -1);
		// A choice or an exclusion goes back whole, as the text it was written from: how it comes
		// apart is the parser's decision.
		if (last.simple === false) {
			pending = null;
			typed = chipText(last);
		} else {
			pending = last.field;
			typed = chipValueText(last);
		}
		// Asked anyway, though the query is unchanged: that is `serialise`'s property, not this one's.
		searchBox.suggest(whole);
		// After the value is in the field, or the caret lands in an empty box.
		await tick();
		input?.focus();
		const end = input?.value.length ?? 0;
		// The end, so the next press carries on deleting the value.
		input?.setSelectionRange(end, end);
	}

	/* Searching by meaning, offered only where this install can. Its own address parameter, not the
	   sort, or every order in the Sort panel would be dead; `sort=similarity` is still read for
	   older links, never written. */
	const MEANING = 'meaning';
	const BY_MEANING_SORT = 'similarity';

	/* The two ways of searching, as the positions of one switch; the sparkle's wave moves while the
	   model is answering. */
	const MODES: readonly TabChoice[] = [
		{
			id: 'meaning',
			label: 'Search by what things look like',
			icon: 'auto_awesome',
			wave: true,
			shortcut: 'search.byMeaning'
		},
		{ id: 'words', label: 'Search for the words', icon: 'search', shortcut: 'search.forWords' }
	];
	const canAskByMeaning = $derived(availability.available);
	const byMeaning = $derived(
		page.url.searchParams.get(MEANING) === '1' ||
			page.url.searchParams.get('sort') === BY_MEANING_SORT
	);

	/* Read once per page load, and again by whoever changes the answer. */
	onMount(() => {
		void availability.load();
	});

	/* Switching how to search navigates, so the caret is owed back once the page settles:
	   `afterNavigate`, and the effect below; `tick()` alone is too early. */
	let owedTheCaret = false;

	/** Put the caret back where it was, at the end of what is typed. */
	function takeTheCaretBack() {
		input?.focus();
		const end = input?.value.length ?? 0;
		input?.setSelectionRange(end, end);
	}

	afterNavigate(() => {
		if (!owedTheCaret) return;
		owedTheCaret = false;
		takeTheCaretBack();
	});

	/* And when the mode itself changes, which is the switch having taken effect: work after the
	 * navigation can re-create the field. It writes only a plain flag, so it cannot re-trigger. */
	$effect(() => {
		byMeaning;
		if (!owedTheCaret) return;
		untrack(() => {
			owedTheCaret = false;
			takeTheCaretBack();
		});
	});

	async function setMeaning(on: boolean) {
		/* Pressing the mode already answering still leaves the caret in the box. */
		if (on === byMeaning) {
			takeTheCaretBack();
			return;
		}
		// Claimed before the navigation, while "was this box being typed in" can still be answered.
		owedTheCaret = true;
		const url = new URL(page.url);
		if (on) url.searchParams.set(MEANING, '1');
		else url.searchParams.delete(MEANING);
		/* The legacy spelling comes off either way: on, so the Sort panel stays; off, so the address
		   stops searching by meaning. */
		if (url.searchParams.get('sort') === BY_MEANING_SORT) url.searchParams.delete('sort');
		// A different question is a different page, so the offset goes.
		url.searchParams.delete('offset');
		// The sheet closes on a query change; this is not one. See `SearchBox.switching`.
		searchBox.switching = true;
		/* Only ever add what is in the box: the box seeds itself from `q` asynchronously, and clearing
		   `q` from a box not yet seeded would wipe the search on screen. */
		const carried = whole.trim();
		/* Not onto a wall with a box of its own, where `q` is that box's words. */
		if (carried && !screenBar.ownBox) url.searchParams.set('q', carried);
		/* A second way to run a search, and the only one by meaning, so it is noted for Recent too. */
		if (carried) void searchBox.remember(carried);
		await goto(url);
		/* Cleared as soon as this navigation is done: a shared flag left standing would make the
		   next navigation, whatever it was, read as a mode switch. */
		searchBox.switching = false;
		await tick();
		takeTheCaretBack();
	}

	function run(query: string) {
		// The library, not a results page: one grid to arrive at.
		const url = new URL('/browse', page.url.origin);
		// On Browse the filters already on screen carry over, since box and bar are one query; from
		// anywhere else Browse gets the words and its own defaults.
		const carries = page.url.pathname === '/browse';
		for (const [key, value] of carries ? page.url.searchParams : []) {
			// Not the offset: a new query has its own result set.
			if (key !== 'q' && key !== 'offset') url.searchParams.append(key, value);
		}
		if (query.trim()) url.searchParams.set('q', query.trim());
		/* Every search goes through here, so this is where it is noted for Recent. */
		void searchBox.remember(query);
		searchBox.dismiss();
		void goto(url);
	}

	function submit(event: Event) {
		event.preventDefault();
		// With a row highlighted, Enter completes it: somebody arrowing down meant that row.
		if (searchBox.highlighted >= 0) {
			choose(searchBox.rows[searchBox.highlighted]);
			return;
		}
		run(whole);
	}

	/* Act on the row that was DRAWN, never an index into the store, which an answer reaches before
	   the markup does. The keyboard reads the clamped `rows[highlighted]`. */
	function choose(row: Row | undefined) {
		// The list can shrink under a highlight (clearing the history does), so the index may be past
		// the end.
		if (row === undefined) return;

		/* It reveals more of its band, and is first because everything below dismisses the list. */
		if (row.kind === 'more') {
			searchBox.showMore(row.group);
			return;
		}

		/* The words as words: the same call Enter makes. */
		if (row.kind === 'text') {
			searchBox.dismiss();
			run(whole);
			return;
		}

		/* Straight to the thing a row is: no chip, the box emptied, and recorded on the way so
		   Recent holds it. */
		const picked = placeOf(row, searchBox.suggestions.token);
		const destination = picked === null ? null : pageFor(picked);
		if (picked !== null && destination !== null) {
			void searchBox.rememberPick(picked);
			searchBox.dismiss();
			forgetWhatIsTyped();
			void goto(destination);
			return;
		}

		if (row.kind === 'recent') {
			// It does again what it was: a search runs, a thing takes you back to it.
			const back = pageFor(row.remembered);
			if (back !== null) {
				searchBox.dismiss();
				// Emptied, exactly as picking a fresh match is.
				forgetWhatIsTyped();
				void goto(back);
				return;
			}
			/* A whole query, run as it stands and parsed on the way back in: quoted whole, a
			   remembered `tags:beach party` would become one tag. */
			chips = [];
			pending = null;
			leading = '';
			typed = row.remembered.subject;
			run(row.remembered.subject);
			return;
		}

		const offered = searchBox.suggestions;
		if (offered.replace_from == null) return;

		// Refuse a completion computed for text that has since changed, which would delete it.
		if (!searchBox.describes(whole)) {
			searchBox.suggest(whole);
			return;
		}

		// The server's offsets are into the whole query; the chips are its front.
		const into = offered.replace_from - typedStartsAt;
		/* A name's span, used only by the branches that build a chip from a match. */
		const nameFrom = nameSpan(offered) ?? offered.replace_from;

		if (row.kind === 'filter') {
			// The field is decided and the value is the next question, so it is a pending chip, and the
			// word being typed became it. What came before the token becomes leading free text.
			const before = dropToken(typed, into).trim();

			/* An excluded field stays text, because a pending chip cannot hold "and not": finishing the
			   value chips the whole thing, exclusion included. */
			if (before.endsWith('-')) {
				typed = `${before}${row.filter.field}:`;
			} else {
				if (before) leading = leading ? `${leading} ${before}` : before;
				typed = '';
				pending = row.filter.field;
			}
		} else if (pending !== null) {
			// The value for the field already chosen: the whole of what is typed.
			const field = pending;
			pending = null;
			// The words waiting in front of the filter go back to being words.
			const before = releaseLeading();
			if (endsWithJoin(before)) {
				// After a joining word, the filter goes where it was typed. See `writeAndReparse`.
				writeAndReparse(`${before} `, field, row.match.value);
			} else {
				addChip({ field, value: row.match.value }, 0);
				typed = before;
			}
		} else {
			// The row's own field, or the list's single token: both the server's, never worked out here.
			const field = row.match.field ?? offered.token;
			if (field == null) return;

			/* An excluded field's value is written into the text with its `-` and parsed, never built
			   as a plain chip, which would turn "not this" into "this". */
			const upTo = typed.slice(0, Math.max(0, nameFrom - typedStartsAt));
			if (upTo.trimEnd().endsWith(`-${field}:`)) {
				typed = `${upTo}${quoted(row.match.value)}`;
				void chipWhatIsTyped();
			} else if (endsWithJoin(upTo)) {
				// After a joining word: see `writeAndReparse`.
				writeAndReparse(upTo, field, row.match.value);
			} else {
				addChip({ field, value: row.match.value }, nameFrom);
			}
		}

		searchBox.highlighted = -1;
		searchBox.suggest(whole);
		input?.focus();
	}

	function onInput() {
		searchBox.open = true;
		searchBox.suggest(whole);
	}

	/* A filter typed by hand becomes the chip a pick makes, read off the server's `token`, only
	   while the value is empty. No loop: `whole` is the same before and after. */
	$effect(() => {
		const field = searchBox.suggestions.token;
		if (field == null || pending !== null) return;
		// The answer has to describe what is in the box now.
		if (!searchBox.describes(whole)) return;
		/* The token at the end of the text; anything before it stays as words. */
		const text = untrack(() => typed);
		const token = `${field}:`;
		if (!text.trimEnd().endsWith(token)) return;
		const before = text.trimEnd().slice(0, -token.length).trim();

		/* Not when the field is excluded: `-tags:` would leave the `-` outside the chip. Finishing the
		   word chips the whole of it. */
		if (before.endsWith('-') || text.trimEnd().endsWith(`-${token}`)) return;
		untrack(() => {
			if (before) leading = leading ? `${leading} ${before}` : before;
			typed = '';
			pending = field;
		});
	});

	/* Everything in the box gone: the chips, a half-chosen field and the words, where the browser's
	 * own clear emptied only the words. */
	function clearAll() {
		chips = [];
		pending = null;
		leading = '';
		typed = '';
		input?.focus();
		onInput();
	}

	/* Select all, where all is more than the text field: the whole query becomes one editable
	 * string and is selected, so typing replaces it, Delete empties it and Ctrl+C copies all of it.
	 * Re-running the string rebuilds the same chips. */
	function selectWholeQuery() {
		const all = whole;
		chips = [];
		pending = null;
		leading = '';
		typed = all;
		input?.focus();
		// After the value has landed on the element, or there is nothing to select yet.
		requestAnimationFrame(() => input?.setSelectionRange(0, all.length));
	}

	function onKey(event: KeyboardEvent) {
		/* Ctrl + Left and Right move the switch in front of the field, the way each arrow points,
		 * where the switch is drawn; elsewhere they are the browser's word jumps. */
		if (canAskByMeaning) {
			const toMeaning = matches(event, 'search.byMeaning');
			if (toMeaning || matches(event, 'search.forWords')) {
				event.preventDefault();
				void setMeaning(toMeaning);
				return;
			}
		}

		/* Ctrl/Cmd+A with anything held outside the text field; a plain text query keeps the browser's. */
		if (
			(event.key === 'a' || event.key === 'A') &&
			(event.ctrlKey || event.metaKey) &&
			!event.altKey &&
			(chips.length > 0 || pending !== null || leading !== '')
		) {
			event.preventDefault();
			selectWholeQuery();
			return;
		}

		/* Backspace at the very start takes the last decision off, only with the caret at the start
		 * and nothing selected, so it never eats a chip while somebody edits the text. */
		if (
			event.key === 'Backspace' &&
			(pending !== null || chips.length > 0) &&
			input?.selectionStart === 0 &&
			input?.selectionEnd === 0
		) {
			event.preventDefault();
			// The pending field first, as the nearest and latest decision; a finished chip is taken apart.
			if (pending !== null) dropPending();
			else void unchipLast();
			return;
		}
		if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
			if (!showing) return;
			event.preventDefault();
			searchBox.move(event.key === 'ArrowDown' ? 1 : -1);
			return;
		}
		if (event.key === 'Escape') {
			// Refused always: some browsers clear a search field on Escape. Kept from anything enclosing
			// only while there is a list to close, so Escape still reaches the search overlay.
			if (showing) event.stopPropagation();
			searchBox.dismiss();
			return;
		}
		if (event.key === 'Tab' && showing && searchBox.highlighted >= 0) {
			event.preventDefault();
			choose(searchBox.rows[searchBox.highlighted]);
			return;
		}

		/* A space finishes a word, which is when a typed filter can be read as one. */
		if (event.key === ' ') void chipWhatIsTyped();
	}

	/* On the way down, as every press in this list is, so the input keeps the caret. */
	async function removeRecent(event: Event, remembered: Remembered) {
		event.preventDefault();
		event.stopPropagation();
		try {
			await searchBox.forgetRecent(remembered);
		} catch {
			// As Clear: a refusal leaves the row, which is the truth.
		}
		input?.focus();
	}

	async function clearRecent(event: Event) {
		event.preventDefault();
		event.stopPropagation();
		try {
			await searchBox.clearHistory();
		} catch {
			// A refusal leaves the list as it was; the next keystroke re-reads it anyway.
		}
		input?.focus();
	}

	/* The store outlives the box, so its debounce timer and request are cancelled here. */
	onDestroy(() => searchBox.teardown());
</script>

<div class="wrap" class:roomy={exclusive}>
	<!-- `focusin`/`focusout` on the whole field: pressing a suggestion moves focus to its row, and the
	     hint is about whether the box is in use. -->
	<form
		class="search"
		onsubmit={submit}
		role="search"
		onfocusin={() => (focused = true)}
		onfocusout={(event) => {
			const to = event.relatedTarget as Node | null;
			focused = to !== null && event.currentTarget.contains(to);
		}}
	>
		<Scroller>
			<div class="fields">
				<!-- The two ways of searching, side by side at the head of the box, as one choice: whichever
				     is lit is answering, and pressing the other comes back. -->
				{#if canAskByMeaning}
					<!-- The pair sits tight together and is spaced off what follows, so it reads as one
					     choice with two states rather than as two unrelated controls. -->
					<span class="modes">
						<Tabs
							look="segmented"
							size="panel"
							label="How to search"
							keepFocus
							tabs={MODES}
							current={byMeaning ? 'meaning' : 'words'}
							onselect={(id) => void setMeaning(id === 'meaning')}
						/>
					</span>
				{:else}
					<Icon name="search" size={18} />
				{/if}

				<!-- The filters as things: each a button, named with what it takes away. Keyed by position as
				     well as text, because a clause typed twice arrives twice. -->
				{#each chips as chip, index (`${index}:${chipText(chip)}`)}
					<!-- Square, because a filter token is a fact about the search rather than a
					     label somebody applied (see the chip's note on the two shapes). The cross
					     is its own target rather than the whole chip: a chip removing itself on any
					     press is a control whose only action is to disappear. -->
					<ChipView
						size="sm"
						shape="square"
						selected
						class="token"
						onremove={() => removeChip(index)}
						removeLabel="Remove the filter {chip.field}: {chip.value}"
					>
						{#snippet lead()}<span class="chip-field">{chip.field}:</span>{/snippet}
						{chip.value}
					</ChipView>
				{/each}

				<!-- Words that were in the box when a filter was chosen, drawn so they do not vanish, before
				     the pending chip as in the query. Backspace at the start hands them back to the field. -->
				{#if pending && leading}
					<span class="leading">{leading}</span>
				{/if}

				<!-- The filter waiting for its value: a span with its own remove control, because the caret
				     sits right after it and an input cannot sit inside a button. -->
				{#if pending}
					<ChipView
						size="sm"
						shape="square"
						selected
						class="token pending"
						onremove={dropPending}
						removeLabel="Remove the {pending} filter"
					>
						<span class="chip-field">{pending}:</span>
					</ChipView>
				{/if}

				<!-- type=search, so it is announced as a search field rather than as a text box. -->
				<TextInput
					bind:element={input}
					type="search"
					name="q"
					bind:value={typed}
					oninput={onInput}
					onkeydown={onKey}
					onfocus={onInput}
					onblur={() => void chipWhatIsTyped()}
					placeholder="Search"
					aria-label="Search"
					autocomplete="off"
					spellcheck="false"
					role="combobox"
					aria-expanded={showing}
					aria-controls={showing ? 'search-suggestions' : undefined}
					aria-autocomplete="list"
					aria-activedescendant={searchBox.highlighted >= 0
						? `search-suggestion-${searchBox.highlighted}`
						: undefined}
				/>

				<!-- The keyboard hint, read off the declaration, shown until the box is used, `aria-hidden`
				     because it is a hint about a key. -->
				{#if !exclusive && !focused && !whole && !searchBox.sheetOpen}
					<!-- Part of the box: a press on it focuses the field, on the way down, since the hint is gone
					     by the time a click would land. -->
					<!-- svelte-ignore a11y_no_static_element_interactions -->
					<span
						class="shortcut"
						aria-hidden="true"
						onpointerdown={(event) => {
							event.preventDefault();
							input?.focus();
						}}
						onmousedown={(event) => event.preventDefault()}
					>
						{shortcut('app.search').shown}
					</span>
				{/if}
				{#if whole}
					<!-- `Pressable`, not `Button`: the shared `.field-clear` rule loses to Button's
					     own scoped sizing, which would make the cross a large ghost slab. -->
					<Pressable
						class="field-clear"
						pad="sm"
						feedback="none"
						radius="sm"
						onclick={clearAll}
						aria-label="Clear the search"
					>
						<Icon name="close" size={16} />
					</Pressable>
				{/if}
			</div>
		</Scroller>
	</form>

	<SearchSuggestions
		{showing}
		{typing}
		{leaves}
		onchoose={choose}
		onremoverecent={(event, remembered) => void removeRecent(event, remembered)}
		onclearrecent={(event) => void clearRecent(event)}
	/>
</div>

<!-- A press outside puts the list away, heard on the way DOWN (the phone's Search square opens this
     box from its own click). Inside or outside is read off the press's own path; the keyboard's
     click still closes it. -->
<svelte:window
	onpointerdown={(event) => {
		const box = input?.closest('.wrap');
		if (!box || !event.composedPath().includes(box)) searchBox.dismiss();
	}}
	onclick={(event) => {
		if (event.detail !== 0 || !(event.target instanceof Node)) return;
		if (!input?.closest('.wrap')?.contains(event.target)) searchBox.dismiss();
	}}
/>

<style>
	/* The two ways of searching, held close, first in the field and clear of its clipping corner. */
	.modes {
		display: flex;
		flex: none;
		margin-inline-start: var(--space-1);
	}

	/* The track on the box's own tone: sunk to the rail's step it would be the loudest thing in the
	   bar. The answering mode is lifted on the chip's step, as the shortcut's key cap is. */
	.search .modes :global(.segmented .track) {
		background: transparent;
	}

	/* The key cap: a label naming a keystroke, at a key cap's size rather than a button's.
	   `:global` because the class is handed to a component; scoped under `.search`. */
	.search :global(.shortcut) {
		display: grid;
		place-items: center;
		flex: none;
		block-size: 28px;
		min-block-size: 0;
		padding-inline: var(--space-2);
		border: 1px solid var(--sift-line);
		/* `--radius-sm`: concentric with the field's `--radius-lg` corner, 9 in on both axes. */
		border-radius: var(--radius-sm);
		background: var(--sift-surface-4);
		color: var(--sift-ink);
		font: var(--text-micro);
		white-space: nowrap;
		user-select: none;
		/* The box's own cursor: a press here is a press in the field. */
		cursor: text;
	}

	/* The cross's box and colour are `.field-clear` in `app.css`, shared with the settings search. */

	/* The width is the top bar's column; the cap guards anywhere else this is mounted. */
	.wrap {
		position: relative;
		inline-size: 100%;
		max-width: var(--search-width);
	}

	/* Except in the bigger search, where the point is room (`exclusive` marks the sheet). */
	.wrap.roomy {
		max-width: none;
	}

	.search {
		display: grid;
		/* A container, so the hint can be dropped when there is no room. On the form, not `.wrap`:
		   containment makes a stacking context, which would trap the list's `z-index`. */
		container: search-field / inline-size;
		/* Grows with its chips, to a ceiling, and `minmax(0, auto)` so the row honours it. */
		grid-template-rows: minmax(0, auto);
		/* The control row's height: a finger's on a phone, where every box is. */
		min-block-size: var(--control-height);
		padding-block: var(--space-1);
		/* Narrow padding, so the mode pair sits near the edge inside the shape that clips it. */
		padding-inline: var(--space-2);
		/* `--radius-lg`, so the cap 9 in from this corner is concentric on `--radius-sm`. */
		border-radius: var(--radius-lg);
		background: var(--sift-surface-3);
		border: 1px solid var(--border);
		color: var(--sift-ink-3);
	}

	/* The ceiling is on the box that SCROLLS; the row inside it is the flex line the chips wrap on,
	   since the form's border, focus ring and drop target may not scroll away. */
	.search :global(.scroll-root) {
		max-block-size: 76px;
	}

	.fields {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: var(--space-2);
		min-block-size: 28px;
	}

	/* At a phone's width the mode pair and the field share one line, and the field takes what the
	   pair leaves. */
	@media (max-width: 767px) {
		.fields {
			flex-wrap: nowrap;
		}

		.search :global(.text-input) {
			flex: 1 1 0;
			min-inline-size: 0;
		}
	}

	/* Too narrow for the hint, so the hint goes rather than the box growing a row.
	   `ROOM_FOR_THE_MENUS` keeps the desktop window above it; this holds for a narrower browser. */
	@container search-field (max-width: 210px) {
		.search :global(.shortcut) {
			display: none;
		}
	}

	.search:focus-within {
		/* The border goes transparent under the ring, so the ring stands alone and nothing shifts. */
		border-color: transparent;
		box-shadow: var(--focus-ring);
	}

	/* The field's name, quieter than its value. In a `lead` snippet, which compiles here. */
	.chip-field {
		opacity: 0.75;
	}

	/* One filter, as one object, sized to sit in the bar's line rather than to stand out from it:
	   these are the query, not a decoration on it. `:global` because the class is handed to the
	   chip. */
	.search :global(.token) {
		flex: none;
		max-inline-size: 220px;
	}

	/* Waiting for its value: the same chip as an open question, with a dashed edge, since a chip
	   that looks finished when it is not is worse than none. */
	.search :global(.pending) {
		border: 1px dashed var(--sift-accent);
	}

	.search :global(.pending .chip-field) {
		opacity: 1;
	}
	/* The words that were typed before a filter was chosen. Ordinary text, because that is what they
	   are: a chip would say a decision had been made about them, and none has. */
	.leading {
		flex: none;
		color: var(--foreground);
		font: var(--text-body);
		white-space: nowrap;
	}

	/* No edge and no ground: the frame around it draws both. `:global`: `TextInput`'s element. */
	.search :global(.text-input) {
		flex: 1;
		/* Enough to type into once a row of chips has taken most of the bar; below this it wraps to
		   its own line instead of becoming a two-character slot. */
		min-width: 8ch;
		border: 0;
		background: none;
		outline: none;
	}

	/* One focus ring, the wrapper's: the input's own would draw a second, tighter one inside it. */
	.search :global(.text-input:focus-visible) {
		box-shadow: none;
	}

	.search :global(.text-input::placeholder) {
		color: var(--sift-ink-3);
	}
</style>
