/* Searching, from the client's side: the dropdown, the results, and the recent list.
 *
 * The important thing this file does is REFUSE to understand the query language. It never splits a
 * token, never works out which one the caret is in, never decides what `rating:4+` means. All of
 * that lives on the server, in one parser, and a copy here would be a second grammar: it would
 * disagree about quoting first, and then about ranges, and a person would get one set of
 * suggestions and a different set of results with nothing to say which was right.
 *
 * So the whole line is sent as typed and the server says what to show. That costs a request per
 * keystroke, which is why there is a debounce and a generation counter below, and it buys the only
 * thing worth having here: there is exactly one definition of the language in the product.
 *
 * The address bar is where the query lives, not a variable in here (see the screens). A search
 * that can be sent to somebody or reopened tomorrow is a link, and a link is the state.
 */

import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { quoted, valuesIn } from '$lib/search/quoting';

export { quoted };

/* LIVE: nothing moves it (suggestions for what is being typed, asked again on every keystroke) */

export type Suggestion = components['schemas']['SuggestionOut'];

/** One thing the box remembers: a search that was run, or a thing that was picked out of it. */
export type Remembered = components['schemas']['RecentOut'];

/** What a remembered row's kind is when it is a search somebody typed rather than a thing they
 *  picked. Named once here and on the server, because two spellings of it is a row that draws as
 *  one kind and acts as the other. */
export const QUERY_KIND = 'query';

/** One filter the query language understands, described for somebody meeting it. */
type FilterHelp = components['schemas']['FilterOut'];

export type Suggestions = components['schemas']['Suggestions'];

/** One of the things a query asks for, as the server describes it. */
export type ParsedClause = components['schemas']['ParsedClause'];

/** What the server made of a typed query. See `/search/parse` and the Filters screen. */
export type ParsedQuery = components['schemas']['ParsedQuery'];

/* Ask the server what a typed query means.
 *
 * The client never pulls a query apart itself. That is not caution for its own sake: a second
 * parser disagrees with the real one (about quoting first, then about ranges), and when the box
 * and the modal disagree there is nothing to say which is right.
 */
export async function parseQuery(query: string): Promise<ParsedQuery> {
	if (!query.trim()) return { text: '', clauses: [], terms: {}, problems: [] };
	try {
		return await api.get<ParsedQuery>('/search/parse', { query: { q: query } });
	} catch {
		// The screen still opens, showing only the clicked filters. Better than refusing to open.
		return { text: query, clauses: [], terms: {}, problems: [] };
	}
}

/**
 * What the library holds under one of its own vocabularies, matching a prefix.
 *
 * For the boxes on a record form that NAME something: a site's other names, the network it is
 * part of, a person's other names. They complete from this rather than from a list of their own,
 * because a second list of what is in the library is a second answer to one question and nothing
 * would say which was right.
 *
 * The field and the prefix are sent APART rather than composed into a query. Composing one means
 * knowing where a value has to be quoted, and the grammar is the one thing this file refuses to
 * hold a second copy of. See the note at the top.
 */
export async function suggestionsFor(field: string, prefix: string): Promise<Suggestion[]> {
	const wanted = prefix.trim();
	if (!wanted) return [];
	const answer = await api.get<Suggestions>('/search/suggest', {
		query: { field, prefix: wanted }
	});
	return answer.matches;
}

/* Long enough that typing a word is one request rather than five, short enough that the list feels
 * attached to the keyboard. */
const DEBOUNCE_MS = 120;

/**
 * One row of the dropdown, whatever kind it is.
 *
 * The three kinds do three different things when picked: a filter puts `tags:` in the box and
 * leaves the caret there, a match completes the word into a whole token, a remembered row does
 * again whatever it was that put it there: runs a search, or goes back to a thing. They
 * are one list because the arrow keys walk one list, and telling them apart by index into three
 * arrays is how a row ends up doing what the row above it does.
 */
export type Row =
	| { kind: 'filter'; filter: FilterHelp }
	| { kind: 'match'; match: Suggestion }
	| { kind: 'recent'; remembered: Remembered }
	/* The words themselves, offered as a row of their own.
	 *
	 * Typing a name that IS somebody offers that person, and picking them goes to their page, which
	 * is the right answer to "who is this" and the wrong one to "show me everything mentioning this".
	 * Both questions are asked with the same keystrokes, so both have to be on the list: this row is
	 * the second one, and it does exactly what pressing Enter does. */
	| { kind: 'text'; query: string }
	/* "Show 5 more", as a row of the list rather than as furniture beside it.
	 *
	 * A row, and that is the decision worth recording. The obvious alternative is a button in the
	 * group's heading, next to Clear. But the arrow keys walk `rows`, `aria-activedescendant`
	 * names a row by index, and anything that is not in this array is unreachable by keyboard. The
	 * heading's Clear button already has that fault; a control somebody needs in order to SEE the
	 * rest of the list must not. So it is an option like any other, and choosing it reveals more
	 * instead of running something. */
	| { kind: 'more'; group: Group; reveals: number };

/** A band of the dropdown that can be longer than it is worth drawing in one go. */
export type Group = 'match' | 'filter' | 'recent';

/*
 * How many rows of one band are drawn at first, and how many each "show more" adds.
 *
 * One number for both, because they are one promise: the row says "Show 5 more" and it shows five
 * more. Two numbers would let the label and the behaviour drift apart, which is the kind of small
 * lie nobody reports and everybody notices.
 *
 * The same five `FacetPanel` cuts its columns at, for the same reason: a band longer than about
 * five stops being a list somebody reads and becomes one they scroll past.
 */
const FIRST = 5;

const EMPTY: Suggestions = {
	token: null,
	filters: [],
	matches: [],
	recent: [],
	replace_from: null,
	matched_from: null,
	for_query: ''
};

export class SearchBox {
	#suggestions = $state<Suggestions>(EMPTY);
	/** How much of each band is on screen. See `#band`. */
	#shown = $state<Record<Group, number>>({ match: FIRST, filter: FIRST, recent: FIRST });

	get suggestions(): Suggestions {
		return this.#suggestions;
	}

	/*
	 * A new answer, and five of each band again.
	 *
	 * Through a setter rather than by resetting the counts at each of the three places an answer is
	 * assigned. Those three exist because `#fetch` deliberately does not go through `#settle` (it
	 * must not bump the generation it is checking against), and "remember to reset it in all three"
	 * is a rule that holds until somebody adds a fourth. Here it cannot be forgotten, and a test can
	 * hand the box an answer the same way the application does.
	 *
	 * The reset itself is not housekeeping. Left standing, a history somebody had expanded would keep
	 * its full height under the NEXT query's five filters, so the dropdown would grow as they typed:
	 * exactly backwards for a list whose whole point is to converge.
	 */
	set suggestions(next: Suggestions) {
		this.#suggestions = next;
		this.#shown = { match: FIRST, filter: FIRST, recent: FIRST };
	}
	/** Which row the arrow keys have landed on, or -1 for none. */
	highlighted = $state(-1);
	open = $state(false);

	/* Which box the suggestion list belongs to, or null for "whichever is asking".
	 *
	 * There is one of this state and there can be more than one box: the top bar draws one, and
	 * the Ctrl-F overlay draws the SAME component over the top of it. Without an owner both
	 * render the list from the same state, so opening the overlay would light up its list and the
	 * one behind it at the same time: two identical panels, one sharp and one blurred through the veil.
	 *
	 * Null rather than "the top bar" so that nothing changes on a page with one box: exclusivity
	 * only exists while something claims it, and the claim is released when that box goes away.
	 */
	owner = $state<symbol | null>(null);

	/* Whether the Ctrl-F sheet is up.
	 *
	 * Shared rather than the sheet's own, because the box in the TOP BAR needs it: its keyboard
	 * hint is advice about a key, and leaving it on screen while the thing that key opens is open
	 * is advice about something already done.
	 */
	sheetOpen = $state(false);

	/* A request to open that sheet, from something that is not the keyboard.
	 *
	 * The hint chip in the top bar is a control sitting where a control sits, so it gets pressed,
	 * and a label that teaches a shortcut and then ignores the press is worse than no label. It
	 * cannot call the sheet directly: the sheet is a component and this is a store, so it raises a
	 * counter and the sheet watches it. A counter rather than a flag, because two presses in a row
	 * are two requests and a boolean already true is silent.
	 */
	sheetWanted = $state(0);

	/** Ask for the Ctrl-F sheet. */
	askForSheet(): void {
		this.sheetWanted += 1;
	}

	/* That the next navigation is a change of HOW to search rather than a search being run.
	 *
	 * The sheet closes itself whenever the query in the address moves, which is right for running a
	 * search and wrong for this: switching between words and meaning carries what is in the box into
	 * the address, so it looks exactly like a search being run and would shut the sheet under
	 * somebody mid-sentence. One-shot, cleared by whoever reads it, so it cannot leak into a later navigation
	 * that really was a search.
	 */
	switching = $state(false);

	/** Take the suggestion list, so no other box draws it. */
	claim(who: symbol): void {
		this.owner = who;
	}

	/** Give it back, if it was still ours. The guard matters: two overlays opening and closing out
	 *  of order would otherwise leave the list owned by one that has gone. */
	release(who: symbol): void {
		if (this.owner === who) this.owner = null;
	}

	/** Whether this box is the one that should draw the list. */
	ownedBy(who: symbol): boolean {
		return this.owner === null || this.owner === who;
	}

	/* Rising counter, so a slow response cannot overwrite a newer one. Without it, typing quickly
	 * and pausing shows the suggestions for a prefix that is no longer in the box, which looks
	 * like the server being wrong rather than like two requests finishing out of order. */
	#generation = 0;
	#timer: ReturnType<typeof setTimeout> | null = null;
	#abort: AbortController | null = null;

	/** Ask what to show under the box, given everything typed into it. Debounced. */
	suggest(query: string): void {
		if (this.#timer) clearTimeout(this.#timer);
		this.#timer = setTimeout(() => void this.#fetch(query), DEBOUNCE_MS);
	}

	/* Whatever changes the offered rows has to put the cursor back somewhere that exists.
	 *
	 * The arrow keys hold an index into `rows`, and every mutator below shortens that list. Left
	 * where it was, the next Enter reads past the end and hands `undefined` to the completion,
	 * which is a crash, not a no-op. Clamping in one place means a new mutator cannot forget. */
	#settle(next: Suggestions): void {
		this.suggestions = next;
		// A newer answer must not be overwritten by a request already in flight.
		this.#generation += 1;
		if (this.highlighted >= this.rows.length) this.highlighted = -1;
	}

	async #fetch(query: string): Promise<void> {
		const generation = ++this.#generation;
		try {
			const answer = await api.get<Suggestions>('/search/suggest', {
				query: { q: query },
				signal: this.#abort?.signal
			});
			if (generation !== this.#generation) return;
			this.suggestions = answer;
			this.highlighted = -1;
		} catch {
			// A dropdown that could not be filled is an empty dropdown, not an error message. The
			// box still works: what was typed can still be submitted, and the server is the thing
			// that answers it.
			if (generation !== this.#generation) return;
			this.suggestions = { ...EMPTY, for_query: query };
		}
	}

	/* Whether the rows on screen still describe what is in the box.
	 *
	 * There is a debounce and a round trip between asking and answering, and the dropdown stays
	 * clickable throughout. Applying a completion computed for `tags:be` to a box now reading
	 * `tags:be cat` would rewrite the line as though `cat` had never been typed, silently
	 * deleting it, with no undo.
	 */
	describes(query: string): boolean {
		return this.suggestions.for_query === query;
	}

	/** Stop any pending work. Called when the component holding this goes away. */
	teardown(): void {
		if (this.#timer) clearTimeout(this.#timer);
		this.#timer = null;
		this.#abort?.abort();
		this.#generation += 1;
	}

	/** Everything currently offered, in the order it is drawn, so the arrow keys can walk it.
	 *
	 * Three groups in one list: the filters that match what is being typed, then the things in the
	 * library that match it, then the searches already run. Filters first because they are the
	 * narrowest and the most useful thing to learn: picking one turns a vague word into a question
	 * about one field.
	 *
	 * Inside a token there is exactly one question being asked, so only its matches are offered.
	 * The server decides that, not this: `filters` simply comes back empty.
	 */
	/*
	 * The order the groups come in, and why the library goes first.
	 *
	 * What is IN the library is what somebody is looking for. The filters are the language
	 * documenting itself, worth having on the list, because nothing else in the interface ever
	 * says the query language exists, but they are a reference rather than an answer. Typing `du`
	 * to reach Nadia Vance and being shown `Duration` above her puts a lesson in front of the
	 * thing that was asked for.
	 *
	 * It also reads worse the more useful the box gets: the filters band is longest when the least
	 * has been typed, so the matches somebody is homing in on are pushed furthest down at exactly
	 * the moment the list should be converging on them.
	 *
	 * The headings follow the rows rather than being counted out of these three lists (see
	 * `headingAt` in the box), so changing this order files each heading over the right group
	 * without anything else being touched.
	 */
	get rows(): Row[] {
		const { filters, matches, recent } = this.suggestions;
		return [
			...this.#textRow(),
			...this.#band(
				matches.map((match): Row => ({ kind: 'match', match })),
				'match'
			),
			...this.#band(
				filters.map((filter): Row => ({ kind: 'filter', filter })),
				'filter'
			),
			...this.#band(
				recent.map((remembered): Row => ({ kind: 'recent', remembered })),
				'recent'
			)
		];
	}

	/*
	 * One band, cut to what is being shown of it, with a row offering the rest.
	 *
	 * ## Why the cut is HERE and not in the markup
	 *
	 * `rows` is what the arrow keys walk and what `aria-activedescendant` names by index. A list
	 * cut in the component would be a second list, one row longer than the one the keyboard is
	 * moving through, and the two would disagree about what row 7 is. So Enter would pick a
	 * different thing from the one under the highlight. One list, cut once, is the only arrangement
	 * in which that cannot happen.
	 *
	 * ## Why not all of it
	 *
	 * Every filter the language has and every search in the history, offered on a click into an
	 * empty box, is a wall of text over the page before a single letter has been typed. The point
	 * of the list is that it converges as somebody types; opening at full height says the opposite.
	 */
	#band(rows: Row[], group: Group): Row[] {
		const shown = this.#shown[group];
		if (rows.length <= shown) return rows;
		/* `reveals` is what pressing it WILL show, not how many are hidden, so the label can be
		   `Show {reveals} more` with no arithmetic of its own and no second copy of `FIRST` in the
		   markup. A band with two left says two, and the row keeps its promise. */
		return [
			...rows.slice(0, shown),
			{ kind: 'more', group, reveals: Math.min(FIRST, rows.length - shown) }
		];
	}

	/** Reveal another `FIRST` of one band. Nothing else about the box moves. */
	showMore(group: Group): void {
		this.#shown = { ...this.#shown, [group]: this.#shown[group] + FIRST };
	}

	/*
	 * The typed words as a row, offered only where picking a MATCH would do something else.
	 *
	 * Once an entity row navigates to that thing's page, the list has two kinds of answer on it and
	 * the difference is not something a person should have to infer from an icon: "the person called
	 * this" and "everything mentioning this" are both reasonable readings of the same word. So the
	 * plain reading is spelled out as its own row rather than left as the thing Enter happens to do.
	 *
	 * FIRST in the list deliberately. It is what Enter does, so the row a hand lands on first does
	 * what the box would do anyway.
	 *
	 * Not offered when there is nothing typed, and not offered INSIDE a token: `people:ja` is a
	 * filter being completed, every row there completes it, and a row that ran `people:ja` as a
	 * phrase would be a different question wearing the same clothes.
	 */
	#textRow(): Row[] {
		const query = this.suggestions.for_query.trim();
		if (!query) return [];
		if (this.suggestions.token !== null) return [];
		if (this.suggestions.matches.length === 0) return [];
		return [{ kind: 'text', query }];
	}

	/* Walk the list, wrapping through "nothing selected".
	 *
	 * The slots are the rows PLUS one for -1, so arrowing off either end lands back on what was
	 * typed rather than on the far row. That matters more than it sounds: a dropdown you cannot
	 * get out of with the same key you got into it with is one people escape with the mouse.
	 */
	move(delta: number): void {
		const slots = this.rows.length + 1;
		if (slots === 1) return;
		const at = this.highlighted + 1;
		this.highlighted = ((((at + delta) % slots) + slots) % slots) - 1;
	}

	dismiss(): void {
		this.open = false;
		this.highlighted = -1;
	}

	/**
	 * Note that this search was made, so it comes back under Recent.
	 *
	 * The dropdown's Recent group reads what this writes; without it the history is only ever
	 * emptied, and a group that is always empty reads as a feature that does not work. Every way a
	 * search runs (a typed query, Enter, a picked suggestion) comes through here.
	 *
	 * Fire and forget, and silent when it fails. It is a convenience about what somebody did a
	 * moment ago; a search that ran is not worth an error message because a note about it did not
	 * save.
	 */
	async remember(query: string): Promise<void> {
		const cleaned = query.trim();
		if (!cleaned) return;
		// A typed search is its own subject and its own label: the words identify it and the words
		// are what the row shows.
		await this.#keep({ kind: 'query', subject: cleaned, label: cleaned });
	}

	/**
	 * Note that this account picked a thing straight out of the dropdown.
	 *
	 * The other half of the box's memory. Picking a person, a Site, a collection, a tag, a folder,
	 * a track or a file takes somebody to it, and without this Recent would hold only what had been
	 * typed and entered: the half of what people do in that box that needs remembering least.
	 *
	 * `subject` is what taking somebody back there needs, never the address itself: an address is a
	 * route, and a route stored in a database is one that breaks the day it moves.
	 */
	async rememberPick(remembered: Remembered): Promise<void> {
		if (!remembered.subject || !remembered.label) return;
		await this.#keep(remembered);
	}

	/* Fire and forget, and silent when it fails. It is a convenience about what somebody did a
	   moment ago; what they did still happened, and it is not worth an error message because a note
	   about it did not save. */
	async #keep(remembered: Remembered): Promise<void> {
		try {
			await api.post('/search/history', { body: remembered });
		} catch {
			// Whatever it was still happened. Nothing here is worth telling anybody about.
		}
	}

	/* Empty this account's history.
	 *
	 * The server call is awaited before the list is emptied, so a refusal leaves the dropdown
	 * showing what is really stored rather than a list the server still has. The caller catches:
	 * an unhandled rejection here would be a silent failure with a cleared-looking screen.
	 */
	async clearHistory(): Promise<void> {
		await api.del('/search/history');
		this.#settle({ ...this.suggestions, recent: [] });
	}

	/* Take one entry off this account's history, by the subject it was kept under, which is what
	 * the server matches. The same order as above: the list loses the row only once the server has
	 * let it go, and the caller catches. */
	async forgetRecent(remembered: Remembered): Promise<void> {
		await api.del('/search/history', { query: { q: remembered.subject } });
		this.#settle({
			...this.suggestions,
			recent: this.suggestions.recent.filter((one) => one.subject !== remembered.subject)
		});
	}
}

/* Replacing the token being typed with the suggestion that was picked.
 *
 * The token's name and the offset it starts at BOTH come from the server, which is the point. A
 * boundary found here by looking for the last colon would be a one-line tokenizer, blind to
 * quoting, disagreeing with the real one the moment a value contains a colon: completing
 * `in:c:/photos` would rewrite the line as `in:c:` plus the value. There is one tokenizer and it
 * is not in this file.
 *
 * What is left here is text editing: splice the chosen value in, quoted if it needs to be. The
 * quoting rule is the parser's, and it is the only thing about the language this file knows.
 */
/** What reading the address means for the box. See `readAddress`. */
type AddressRead = 'ignore' | 'record' | 'follow';

/**
 * Whether an address the box has just read should be followed back into it.
 *
 * The address is the truth about the query, and following it is what makes the back button, a
 * pasted link and a suggestion picked off the list behave the same way. What it must not do is
 * follow an address that has not moved.
 *
 * That is not a hypothetical distinction. The address is written by other things on the page
 * (paging, the order, where the grid was left), and every one of those makes the box read it again.
 * Compared against what the box HOLDS, a write that only changed the offset finds `q` still empty
 * while somebody is half way through typing, and empties the box under them: text that will not go
 * in, intermittently, and more often on a machine that is busy enough for the write to land late.
 *
 * So the comparison is against the last query FOLLOWED. `null` means none has been, which is how an
 * empty address gets to say "the box is empty" exactly once.
 *
 * `record` rather than `follow` when the address and the box already agree: there is nothing to
 * re-read, and re-parsing would throw away a half-finished filter for no reason. But the address
 * has moved, so it still has to be written down or the next read of the same one is a change.
 */
export function readAddress(asked: string, followed: string | null, whole: string): AddressRead {
	if (asked === followed) return 'ignore';
	if (asked === whole.trim()) return 'record';
	return 'follow';
}

/**
 * The query the top box reads out of an address: `q`, unless the screen has a box of its own.
 *
 * Where a wall draws its own search box, `q` holds THAT box's words, which narrow the wall and
 * nothing else. The top box searches the library, so it reads nothing there and keeps what was
 * typed into it. See `screenBar.claimOwnBox`.
 */
export function addressQuery(params: URLSearchParams, screenHasItsOwnBox: boolean): string {
	if (screenHasItsOwnBox) return '';
	return params.get('q') ?? '';
}

export function applySuggestion(
	typed: string,
	value: string,
	token: string,
	replaceFrom: number
): string {
	// An offset outside the text cannot be describing it, so nothing is spliced. Clamping instead
	// would look tidier and be worse: clamping to the end appends a second copy of the token, so
	// completing `tags:` produces `tags:tags:beach`. Refusing leaves the box as the person left it.
	if (replaceFrom < 0 || replaceFrom > typed.length) return typed;
	return `${typed.slice(0, replaceFrom)}${token}:${quoted(value)} `;
}

/**
 * Putting a chosen filter into the box: the token, its colon, and the caret left after it.
 *
 * No trailing space, unlike completing a value. The two are different moments: picking `beach` off
 * the list finishes a thought, and the space is what says so. Picking `tags:` starts one: the
 * question is now "which tag", the dropdown is about to answer it, and a space would end the token
 * before anything was said and send the next keystroke off as free text.
 *
 * The offset comes from the server, like every other offset here, for the same reason.
 */
export function applyFilter(typed: string, field: string, replaceFrom: number): string {
	// An offset outside the text cannot be describing it. Leaving the box alone beats splicing at a
	// guess. See `applySuggestion`, which follows the same rule.
	if (replaceFrom < 0 || replaceFrom > typed.length) return typed;
	return `${typed.slice(0, replaceFrom)}${field}:`;
}

/**
 * The text with the word being typed taken off the end, for a filter that has become a chip.
 *
 * The companion to `applyFilter`, for the box that holds a chosen filter beside the text instead of
 * inside it. The word somebody was typing turned into the chip, so it must not also stay behind as
 * text. Otherwise `people` half-typed and then chosen would leave `people` in the box under a
 * chip that already says it.
 *
 * The offset is the server's, like every other offset here. Refusing an impossible one rather than
 * clamping it, for the same reason `applySuggestion` does: clamping deletes text nobody asked to
 * lose, and leaving the box alone is always recoverable.
 */
export function dropToken(typed: string, replaceFrom: number): string {
	if (replaceFrom < 0 || replaceFrom > typed.length) return typed;
	return typed.slice(0, replaceFrom);
}

/**
 * Every field the query language names, in the order the server reads them.
 *
 * Here rather than in a screen because more than one thing needs it, and two lists would drift:
 * a field added to one and not the other is a filter that works when it is typed and vanishes when
 * it is saved.
 */
export const FIELDS = [
	'tags',
	'people',
	'sites',
	'collections',
	/* The pictures that arrived together, which the Photo Sets page writes. A field missing HERE
	   is a filter with no chip: in force, changing what is on screen, and invisible. */
	'photo_sets',
	/* The song a file carries, by the song's name or id: what a Songs card and a song's Files tab
	   write. A field missing HERE is a filter with no chip: in force, changing what is on screen,
	   and invisible. */
	'songs',
	/* Videos with a marked moment. Presence only: a loop is a piece OF a file rather than
	   something the file is in, so there is no value to name. A field missing HERE is a filter with
	   no chip: in force, changing what is on screen, and invisible. */
	'loops',
	'in',
	'media',
	'filetype',
	'rating',
	/* The O counter's tally, which the facet panel writes a band of and the search box takes
	   typed. A field missing HERE is a filter with no chip: in force, changing what is on
	   screen, and invisible. */
	'o_count',
	'fav',
	'sharing',
	'added',
	'duration',
	'resolution',
	'size',
	'vcodec',
	'acodec',
	'filename',
	/* The name somebody GAVE a file, which is not the name it has on disk. A downloaded library
	   is full of files whose name on disk is a string nobody chose; the title is the only name
	   in it worth reading, and it deserves a field rather than free text. */
	'title',
	/* The track a file is set to. Its own field rather than free text: for a lot of a library the
	   music IS what the file is, and free text would also match a person whose name happens to be
	   the artist's. A field missing HERE is a filter with no chip: in force, changing what is on
	   screen, and invisible. */
	'music',
	/* Whether something has been watched, which the Unwatched preset writes. A field missing HERE
	   is a filter with no chip: in force, changing what is on screen, and invisible. That is the one thing this bar may not do. */
	'viewed',
	/* Which way up the picture is, and who wrote to the file without a person doing it. Both are
	   columns on the facet panel, and a field missing HERE is a filter with no chip. */
	'orientation',
	'enriched',
	/* When a box last described the file, as a band (never, today, this week...), and kept-local
	   as its own row. A field missing HERE is a filter with no chip: in force, changing what is
	   on screen, and invisible. */
	'enrichment',
	/* Who made the file: a copy Sift made, a swap, a download, or a folder of the library. A
	   column on the facet panel, and a field missing HERE is a filter with no chip. */
	'created',
	/* The file's own date as a year, and the network that put it out: facets the registry
	   declares, and a field missing HERE is a filter with no chip.

	   There is no production-date field: almost nothing in a downloaded library carries one, so
	   the filter would describe a handful of files and leave the rest out. */
	'released',
	'network',
	/* What the PEOPLE on a file are like: the person record's own facets, asked of the file
	   through the people on it. One facet is one thing everywhere, so these are the same six
	   questions the People wall filters by. */
	'gender',
	'hair',
	'eyes',
	'ethnicity',
	'nationality',
	'breasts',
	'height',
	'age',
	/* Whether somebody in it MAKES the edits. Yes or no rather than one of a set of words, because
	   the person's own column is a flag, the one of this family that reads like `fav:`. */
	'pmv',
	/* The files that share a song with one file. Its value is that file's ID rather than a name
	   (a file has no name a person would type, and two files can share a title), so it is written by
	   pressing the Same music strip's heading on the file page and read back as a chip. A field
	   missing HERE is a filter with no chip: in force, changing what is on screen, and invisible. */
	'same_music',
	/* The files similar to one file, by the same answer the strip under a file draws. An ID for the
	   reason `same_music` is one, written by that strip's heading and by the file menu. */
	'like',
	/* The files one product gave up on: the Importing pane's "24 files couldn't have thumbnails
	   generated and are left out", whose count opens the Files wall filtered by this. A field
	   missing HERE is a filter with no chip: in force, changing what is on screen, and invisible. */
	'left_out',
	/* A person's folder files with a face still unnamed, by ID: their page's faces line writes it. */
	'unnamed_face',
	/* Every OTHER spelling that means one of the above: each filter's LABEL, plus retired tokens.
	 * Kept for good and kept LAST.
	 *
	 * Saved searches are stored in this shape, so a name dropped from here is a filter that stops
	 * being read out of every search already saved under it, which is not an error anybody sees:
	 * it is a search that quietly widens to the whole library. The server reads every one of these
	 * for the same reason; these two lists have to agree, and a gate says so.
	 *
	 * Last so that a stored query carrying both comes back with the current spelling written first.
	 */
	/* `platforms` is the old word for Sites. Every filter anybody saved naming a site is stored
	   as `platforms=...`, so the old word stays a spelling for good, and the server's
	   `ALIASES` carries the same entry for the same reason. */
	'platforms',
	'type',
	'folder',
	'file_type',
	'favorites',
	'sharing_status',
	'file_size',
	'video_codec',
	'audio_codec',
	'file_name',
	'name',
	'enriched_by',
	'created_by',
	/* The label of the asks column, whose older label was "Enrichment". A label is a real
	   spelling in this language, so the name the list draws has to be one the parser takes. */
	'asked_a_stash-box',
	'hair_colour',
	'eye_colour',
	'breast_type',
	/* The REGISTRY's spelling of the same six, which is the key the People wall filters by. Here
	   so that one facet really is one thing everywhere: a panel drawn over people writes
	   `hair_color`, and the search box has to read what the panel writes. */
	'hair_color',
	'eye_color',
	'country',
	'height_cm',
	'pmv_creator',
	'release_date',
	/* The label of the view-status filter, whose older label was "Viewed". A saved search
	   spelled `viewed:...` still reads; this is what makes the current label a spelling too,
	   which is the rule every filter in this language follows. */
	'view_status',
	/* The label of `like`, the name the strip and the file menu give it. */
	'similar_to_this',
	/* The one-thing spelling of `songs`: a file carries one song, so `song:` is how somebody
	   types it, and the parser takes it. */
	'song',
	'face_still_unnamed'
] as const;

/**
 * A saved search, as the query it stands for.
 *
 * Saved searches are stored as a query STRING, and there are two spellings of one in the wild. The
 * older ones are named parameters: `tags=beach&type=video`. Anything saved now carries its whole
 * query in `q`, because a choice has no spelling as a named parameter.
 *
 * Both are read, and the older shape is turned into the tokens that mean the same thing, which is
 * exactly what the server does with those parameters anyway. Read rather than migrated: a saved
 * search is one row somebody named, and silently rewriting it is worse than reading two spellings
 * for as long as either exists.
 */
export function savedQuery(stored: string): string {
	const params = new URLSearchParams(stored);
	const typed = params.get('q');
	const parts = typed ? [typed] : [];
	for (const name of FIELDS) {
		for (const value of params.getAll(name)) {
			// The minus goes on the token: inside the quotes it would be part of the value.
			const refused = value.startsWith('-') && value.length > 1;
			const { values, any } = valuesIn(refused ? value.slice(1) : value);
			if (values.length === 0) continue;
			const joined = values.map(quoted).join(any ? '|' : ',');
			parts.push(`${refused ? '-' : ''}${name}:${joined}`);
		}
	}
	return parts.join(' ');
}

/** A query kept under a name, in the shape a saved search is stored in. */
export function storedQuery(query: string): string {
	return new URLSearchParams({ q: query }).toString();
}

/** One row of the Filters screen: a field, how it should match, and the values typed into it. */
export interface Rule {
	field: string;
	/** `any` / `all` / `none` of the values, or `empty` / `present` for the dimension itself. */
	how: string;
	/** What was typed into the row, comma separated, exactly as the query language takes it. */
	values: string;
}

/**
 * A row as the query language writes it.
 *
 * The only thing about the language this file decides, and it is here rather than in the screen
 * for the reason the quoting rule is: a second place that spells a query is a second thing to
 * disagree with the parser. A row with nothing named comes to nothing, which is how a half-filled
 * row is left out rather than turned into a filter matching nothing.
 */
export function ruleText(rule: Rule): string {
	if (rule.how === 'empty') return `-${rule.field}`;
	if (rule.how === 'present') return `${rule.field}:any`;

	const values = rule.values
		.split(',')
		.map((value) => value.trim())
		.filter(Boolean);
	if (values.length === 0) return '';

	if (rule.how === 'none') {
		return values.map((value) => `-${rule.field}:${quoted(value)}`).join(' ');
	}
	if (rule.how === 'all') return `${rule.field}:${values.map(quoted).join(',')}`;
	return values.map((value) => `${rule.field}:${quoted(value)}`).join(' OR ');
}

/**
 * The clause as a row the Filters screen can edit, or null if it cannot edit it.
 *
 * The check is the point, and it is a CHECK rather than a list of shapes it recognizes. A row is
 * built, written back out, and compared with what the server sent; anything that does not match
 * character for character is a clause this screen does not understand.
 *
 * Enumerating the shapes goes wrong in ways that each let more through: an excluded kind coming
 * back as a plain one (`-type:video` turning into `type:video`), a choice of exclusions naming no
 * field and vanishing on the next Apply, a list inside a choice flattened (`tags:a,b OR tags:c`
 * becoming "any of a, b, c"). Each silently changes the query.
 *
 * A check cannot go stale as the language grows. A list of shapes has to be remembered.
 */
export function rowFor(clause: ParsedClause): Rule | null {
	if (clause.field == null) return null;
	const rule: Rule =
		clause.present !== null
			? { field: clause.field, how: clause.present ? 'present' : 'empty', values: '' }
			: {
					field: clause.field,
					how: clause.negated ? 'none' : clause.match,
					values: clause.values.join(', ')
				};
	return ruleText(rule) === clause.query ? rule : null;
}

/**
 * What a one-value control should read for this clause, or null if it cannot say it.
 *
 * `rating:none` and `rating:any` are the presence question, which those controls have words for.
 * An EXCLUDED value is not: there is no "not video" in a Kind list, and reading it as "video"
 * would turn the filter into its own opposite the moment Apply was pressed. Held to the same
 * written-back-out check as a row, for the same reason.
 */
export function singleFor(clause: ParsedClause): string | null {
	if (clause.field === null) return null;
	if (clause.present !== null) {
		const word = clause.present ? 'any' : 'none';
		return `${clause.field}:${word}` === clause.query ? word : null;
	}
	// The negation test is redundant with the comparison below: an excluded value is written with
	// a leading minus, so it can never equal what a plain one writes. It stays because it says the
	// REASON, and the comparison is the thing that would still be right if the spelling changed.
	if (clause.negated || clause.values.length !== 1) return null;
	const value = clause.values[0];
	return `${clause.field}:${quoted(value)}` === clause.query ? value : null;
}

/* A filter that has been decided, held as a filter rather than as text.
 *
 * The box shows these as chips: one object you can see, aim at and remove in one press, instead of
 * a run of characters somebody has to select exactly. `tags:"beach party"` is nine keystrokes of
 * quoting to retype and one chip to delete.
 *
 * The field and the value are kept apart and only ever put together at the last moment, by the
 * function below. That is what keeps the quoting rule in one place: a chip carrying the string
 * `tags:"beach party"` would have to be parsed to be edited, and parsing is the one thing this file
 * does not do.
 */
export interface Chip {
	field: string;
	value: string;
	/*
	 * Exactly how the server writes this filter, when the server is the one that made the chip.
	 *
	 * A chip built here by picking a name off the dropdown is one field and one value, and this
	 * file can spell that. A chip that came back from parsing what is in the address may be a
	 * choice or an exclusion, which has a spelling this file deliberately does not know. So the
	 * server sends it, and it is put back verbatim. Composing it here would be a second writer to
	 * disagree with the parser, which is the same mistake as a second parser wearing a hat.
	 */
	query?: string;
	/*
	 * Whether it is one field with one value, which is the only shape the box can take apart and
	 * hand back as editable text. Anything else goes back into the box whole.
	 */
	simple?: boolean;
}

/** One chip as the query language writes it. */
export function chipText(chip: Chip): string {
	return chip.query ?? `${chip.field}:${quoted(chip.value)}`;
}

/**
 * A chip's value alone, written the way it would have been typed.
 *
 * The companion to `chipText`, for taking a finished chip back APART. Backspace turns one into the
 * field held beside the box and its value as ordinary editable text, and that text has to be
 * spelled exactly as `chipText` would have spelled it or the query changes meaning under somebody
 * pressing an undo key: unquoted, `tags:"beach party"` comes back as the tag `beach` and a loose
 * word `party`, which is a different search and finds different things.
 *
 * Same `quoted` as `chipText` uses, deliberately: one rule, one place, so the two directions
 * cannot drift apart.
 */
export function chipValueText(chip: Chip): string {
	return quoted(chip.value);
}

/**
 * The whole query: the chips, then whatever is still being typed.
 *
 * This is what goes to the server, both to search and to ask what to suggest, so the parser sees
 * one ordinary query string and has no idea any of it was ever a chip. Chips first and always, so
 * that an offset the server sends back for the text being typed can be shifted by a length this
 * knows exactly.
 */
export function serialise(chips: Chip[], text: string): string {
	const front = chips.map(chipText).join(' ');
	if (!front) return text;
	return text ? `${front} ${text}` : `${front} `;
}

/** How many characters of a serialised query the chips take up, including their trailing space. */
export function chipPrefixLength(chips: Chip[]): number {
	const front = chips.map(chipText).join(' ');
	return front ? front.length + 1 : 0;
}

/** Whether two chips mean the same filter, so one is not added twice.
 *
 * Compared on what they come to rather than on their parts, because a chip the server wrote and
 * one built here can spell the same filter without carrying the same fields. */
export function sameChip(one: Chip, other: Chip): boolean {
	return chipText(one) === chipText(other);
}

/**
 * Turn what the server made of a typed query into chips and the free text left over.
 *
 * One chip per clause, and each carries the server's own spelling of it, so a compound query
 * survives being drawn as chips, edited and put back. Built from the field and value alone, a
 * choice would have come back as two separate chips that both had to hold, which is a different
 * search from the one that was typed.
 */
export function chipsFromParsed(parsed: ParsedQuery): { chips: Chip[]; text: string } {
	const chips = parsed.clauses.map((clause) => ({
		field: clause.field ?? '',
		value: chipLabel(clause),
		query: clause.query,
		simple:
			clause.field !== null &&
			clause.values.length === 1 &&
			!clause.negated &&
			clause.present === null
	}));
	return { chips, text: parsed.text };
}

/**
 * What a clause says, in words, for the face of a chip.
 *
 * Only ever a LABEL. What the clause means is `query`, which the server wrote and which is what
 * goes back into the box, so this can read however it reads best without any of it being load
 * bearing.
 */
export function chipLabel(clause: ParsedClause): string {
	if (clause.present !== null) return clause.present ? 'any' : 'none';
	return valuesLabel(clause.values, {
		all: clause.match !== 'any',
		excluded: clause.negated
	});
}

/**
 * A list of values as somebody would say it: the joining word, and the refusal, in words.
 *
 * Used by the typed chips through `chipLabel` and by the clicked ones on the bar, because they sit
 * side by side on the same row and a filter added by typing has to read like the same filter added
 * by clicking. Written twice they drift, and each copy keeps its own test, so breaking one leaves
 * the other right and nothing fails.
 *
 * `not` rather than the minus the address uses. `-Alice and Bea` reads as though the minus belonged
 * to the first name.
 *
 * An excluded list is always "or", whatever it was joined with. Excluded, the two joiners mean the
 * same thing (none of these), so writing "and" there would be the chip inventing a distinction
 * the query does not have.
 */
export function valuesLabel(
	values: string[],
	{ all = false, excluded = false }: { all?: boolean; excluded?: boolean } = {}
): string {
	const joined = values.join(excluded || !all ? ' or ' : ' and ');
	return excluded ? `not ${joined}` : joined;
}

export const searchBox = new SearchBox();
