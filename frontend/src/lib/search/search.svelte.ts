/*
 * Searching, from the client's side: the dropdown, the results, the recent list. It REFUSES to
 * understand the query language: the whole line goes to the server's one parser, which says what to
 * show (hence the debounce and the generation counter). The query lives in the address.
 */

import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { quoted, valuesIn } from '$lib/search/quoting';

export { quoted };

/* LIVE: nothing moves it (suggestions for what is being typed, asked again on every keystroke) */

export type Suggestion = components['schemas']['SuggestionOut'];

export type Remembered = components['schemas']['RecentOut'];

/**
 * Named once here and on the server: two spellings is a row drawn as one kind and acting as
 * another.
 */
export const QUERY_KIND = 'query';

type FilterHelp = components['schemas']['FilterOut'];

export type Suggestions = components['schemas']['Suggestions'];

export type ParsedClause = components['schemas']['ParsedClause'];

export type ParsedQuery = components['schemas']['ParsedQuery'];

/* The client never pulls a query apart: a second parser would disagree with the real one. */
export async function parseQuery(query: string): Promise<ParsedQuery> {
	if (!query.trim()) return { text: '', clauses: [], terms: {}, problems: [] };
	try {
		return await api.get<ParsedQuery>('/search/parse', { query: { q: query } });
	} catch {
		// The screen still opens, showing only the clicked filters.
		return { text: query, clauses: [], terms: {}, problems: [] };
	}
}

/**
 * What the library holds under one of its vocabularies, matching a prefix, for the record form's
 * boxes. The field and prefix go APART, so no quoting rule is needed here.
 */
export async function suggestionsFor(field: string, prefix: string): Promise<Suggestion[]> {
	const wanted = prefix.trim();
	if (!wanted) return [];
	const answer = await api.get<Suggestions>('/search/suggest', {
		query: { field, prefix: wanted }
	});
	return answer.matches;
}

const DEBOUNCE_MS = 120;

/** One row of the dropdown: the arrow keys walk one list, so the kinds are one union. */
export type Row =
	| { kind: 'filter'; filter: FilterHelp }
	| { kind: 'match'; match: Suggestion }
	| { kind: 'recent'; remembered: Remembered }
	/*
	 * The words themselves as a row, for "everything mentioning this" beside a person's own page.
	 */
	| { kind: 'text'; query: string }
	/* "Show 5 more" is a row, not a button: anything outside `rows` is unreachable by keyboard. */
	| { kind: 'more'; group: Group; reveals: number };

export type Group = 'match' | 'filter' | 'recent';

/* Rows of a band drawn at first, and how many "show more" adds: one number for one promise. */
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
	#shown = $state<Record<Group, number>>({ match: FIRST, filter: FIRST, recent: FIRST });

	get suggestions(): Suggestions {
		return this.#suggestions;
	}

	/**
	 * A new answer, and five of each band again, through a setter so the reset cannot be forgotten;
	 * left standing, an expanded band would grow the dropdown as somebody typed.
	 */
	set suggestions(next: Suggestions) {
		this.#suggestions = next;
		this.#shown = { match: FIRST, filter: FIRST, recent: FIRST };
	}
	highlighted = $state(-1);
	open = $state(false);

	/* Which box draws the list: the Ctrl-F overlay draws the same component over the top bar's. */
	owner = $state<symbol | null>(null);

	/* Shared: the top bar's keyboard hint hides while the sheet it advertises is open. */
	sheetOpen = $state(false);

	/*
	 * A request to open the sheet from the hint chip; a counter, so two presses are two requests.
	 */
	sheetWanted = $state(0);

	askForSheet(): void {
		this.sheetWanted += 1;
	}

	/* The next navigation changes HOW to search, so the sheet must not close; one-shot. */
	switching = $state(false);

	claim(who: symbol): void {
		this.owner = who;
	}

	/** Only if it was still ours: overlays may open and close out of order. */
	release(who: symbol): void {
		if (this.owner === who) this.owner = null;
	}

	ownedBy(who: symbol): boolean {
		return this.owner === null || this.owner === who;
	}

	/* A slow response cannot overwrite a newer one. */
	#generation = 0;
	#timer: ReturnType<typeof setTimeout> | null = null;
	#abort: AbortController | null = null;

	suggest(query: string): void {
		if (this.#timer) clearTimeout(this.#timer);
		this.#timer = setTimeout(() => void this.#fetch(query), DEBOUNCE_MS);
	}

	/* Every change of rows clamps the cursor here, or the next Enter reads past the end. */
	#settle(next: Suggestions): void {
		this.suggestions = next;
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
			// A dropdown that could not be filled is empty, not an error.
			if (generation !== this.#generation) return;
			this.suggestions = { ...EMPTY, for_query: query };
		}
	}

	/*
	 * Applying a completion computed for an older box would silently delete what was typed since.
	 */
	describes(query: string): boolean {
		return this.suggestions.for_query === query;
	}

	teardown(): void {
		if (this.#timer) clearTimeout(this.#timer);
		this.#timer = null;
		this.#abort?.abort();
		this.#generation += 1;
	}

	/**
	 * Everything offered, in drawn order, for the arrow keys: the typed words, the library's
	 * matches, then the filters (a reference, not an answer), then the history. Headings follow the
	 * rows.
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

	/* One band, cut HERE, so the keyboard and the screen agree about which row is which. */
	#band(rows: Row[], group: Group): Row[] {
		const shown = this.#shown[group];
		if (rows.length <= shown) return rows;
		/* `reveals` is what pressing it WILL show, so the label needs no arithmetic of its own. */
		return [
			...rows.slice(0, shown),
			{ kind: 'more', group, reveals: Math.min(FIRST, rows.length - shown) }
		];
	}

	showMore(group: Group): void {
		this.#shown = { ...this.#shown, [group]: this.#shown[group] + FIRST };
	}

	/* The typed words, FIRST since they are what Enter does; never inside a token. */
	#textRow(): Row[] {
		const query = this.suggestions.for_query.trim();
		if (!query) return [];
		if (this.suggestions.token !== null) return [];
		if (this.suggestions.matches.length === 0) return [];
		return [{ kind: 'text', query }];
	}

	/* Wrapping through "nothing selected", so the same key gets out of the list as got in. */
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

	/** Note a search so it comes back under Recent; fire and forget. */
	async remember(query: string): Promise<void> {
		const cleaned = query.trim();
		if (!cleaned) return;
		await this.#keep({ kind: 'query', subject: cleaned, label: cleaned });
	}

	/**
	 * Note a thing picked out of the dropdown; `subject` is what reaching it needs, never a route.
	 */
	async rememberPick(remembered: Remembered): Promise<void> {
		if (!remembered.subject || !remembered.label) return;
		await this.#keep(remembered);
	}

	/* Fire and forget: what was done still happened. */
	async #keep(remembered: Remembered): Promise<void> {
		try {
			await api.post('/search/history', { body: remembered });
		} catch {
			// Whatever it was still happened.
		}
	}

	/*
	 * Awaited before the list empties, so a refusal leaves what is really stored. The caller
	 * catches.
	 */
	async clearHistory(): Promise<void> {
		await api.del('/search/history');
		this.#settle({ ...this.suggestions, recent: [] });
	}

	/* By the subject the server matches; the row leaves once the server lets it go. */
	async forgetRecent(remembered: Remembered): Promise<void> {
		await api.del('/search/history', { query: { q: remembered.subject } });
		this.#settle({
			...this.suggestions,
			recent: this.suggestions.recent.filter((one) => one.subject !== remembered.subject)
		});
	}
}

/* The token's boundary always comes from the server's tokenizer; only the splice is here. */
type AddressRead = 'ignore' | 'record' | 'follow';

/**
 * Whether an address the box has read should be followed back into it. Compared against the last
 * query FOLLOWED, not the box: paging writes the address too, and would empty a half-typed box.
 */
export function readAddress(asked: string, followed: string | null, whole: string): AddressRead {
	if (asked === followed) return 'ignore';
	if (asked === whole.trim()) return 'record';
	return 'follow';
}

/** `q`, unless the screen has a box of its own (`screenBar.claimOwnBox`). */
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
	// An offset outside the text is refused: clamping would append a second copy of the token.
	if (replaceFrom < 0 || replaceFrom > typed.length) return typed;
	return `${typed.slice(0, replaceFrom)}${token}:${quoted(value)} `;
}

/** The token, its colon and the caret, with no trailing space: the next keystroke is the value. */
export function applyFilter(typed: string, field: string, replaceFrom: number): string {
	// As in `applySuggestion`.
	if (replaceFrom < 0 || replaceFrom > typed.length) return typed;
	return `${typed.slice(0, replaceFrom)}${field}:`;
}

/** The text with the word being typed taken off the end, now that it has become a chip. */
export function dropToken(typed: string, replaceFrom: number): string {
	if (replaceFrom < 0 || replaceFrom > typed.length) return typed;
	return typed.slice(0, replaceFrom);
}

/**
 * Every field the query language names, in the server's order. A field missing here is a filter in
 * force with no chip.
 */
export const FIELDS = [
	'tags',
	'people',
	'sites',
	'collections',
	'photo_sets',
	'songs',
	/* Presence only: a loop is a piece OF a file. */
	'loops',
	'in',
	'media',
	'filetype',
	'rating',
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
	/* The name somebody GAVE a file, not its name on disk. */
	'title',
	/* Its own field: free text would also match a person named like the artist. */
	'music',
	'viewed',
	'orientation',
	'enriched',
	'enrichment',
	'created',
	/* No production-date field: almost nothing downloaded carries one. */
	'released',
	'network',
	/* The people on a file, by the People wall's own facets. */
	'gender',
	'hair',
	'eyes',
	'ethnicity',
	'nationality',
	'breasts',
	'height',
	'age',
	'pmv',
	/* By a file's ID: a file has no name a person would type. */
	'same_music',
	'like',
	'left_out',
	'unnamed_face',
	/*
	 * Every OTHER spelling: labels and retired tokens, kept for good and LAST, as the server keeps
	 * them, or saved searches would widen.
	 */
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
	'asked_a_stash-box',
	'hair_colour',
	'eye_colour',
	'breast_type',
	/* The registry's spelling, which a panel over people writes. */
	'hair_color',
	'eye_color',
	'country',
	'height_cm',
	'pmv_creator',
	'release_date',
	'view_status',
	'similar_to_this',
	'song',
	'face_still_unnamed'
] as const;

/** A saved search as its query; the older named-parameter shape is read, never migrated. */
export function savedQuery(stored: string): string {
	const params = new URLSearchParams(stored);
	const typed = params.get('q');
	const parts = typed ? [typed] : [];
	for (const name of FIELDS) {
		for (const value of params.getAll(name)) {
			const refused = value.startsWith('-') && value.length > 1;
			const { values, any } = valuesIn(refused ? value.slice(1) : value);
			if (values.length === 0) continue;
			const joined = values.map(quoted).join(any ? '|' : ',');
			parts.push(`${refused ? '-' : ''}${name}:${joined}`);
		}
	}
	return parts.join(' ');
}

export function storedQuery(query: string): string {
	return new URLSearchParams({ q: query }).toString();
}

export interface Rule {
	field: string;
	how: string;
	values: string;
}

/** A row as the query language writes it; a row naming nothing comes to nothing. */
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
 * The clause as an editable row, or null: built, written back out and compared with the server's
 * text, so an unknown shape is refused rather than silently changed.
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

/** What a one-value control should read, or null: an EXCLUDED value has no word there. */
export function singleFor(clause: ParsedClause): string | null {
	if (clause.field === null) return null;
	if (clause.present !== null) {
		const word = clause.present ? 'any' : 'none';
		return `${clause.field}:${word}` === clause.query ? word : null;
	}
	if (clause.negated || clause.values.length !== 1) return null;
	const value = clause.values[0];
	return `${clause.field}:${quoted(value)}` === clause.query ? value : null;
}

/* A decided filter, drawn as a chip; field and value are put together only by `chipText`. */
export interface Chip {
	field: string;
	value: string;
	/* The server's own spelling, for a chip made by parsing: this file does not compose choices. */
	query?: string;
	simple?: boolean;
}

export function chipText(chip: Chip): string {
	return chip.query ?? `${chip.field}:${quoted(chip.value)}`;
}

/** A chip's value alone, spelled as `chipText` would, so taking it apart keeps the meaning. */
export function chipValueText(chip: Chip): string {
	return quoted(chip.value);
}

/** Chips first, so an offset the server sends back can be shifted by a known length. */
export function serialise(chips: Chip[], text: string): string {
	const front = chips.map(chipText).join(' ');
	if (!front) return text;
	return text ? `${front} ${text}` : `${front} `;
}

export function chipPrefixLength(chips: Chip[]): number {
	const front = chips.map(chipText).join(' ');
	return front ? front.length + 1 : 0;
}

/** Compared on what they come to: the server's chip and one built here can spell one filter. */
export function sameChip(one: Chip, other: Chip): boolean {
	return chipText(one) === chipText(other);
}

/** One chip per clause, in the server's spelling, so a compound query survives as chips. */
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

/** Only ever a LABEL; the meaning is `query`. */
export function chipLabel(clause: ParsedClause): string {
	if (clause.present !== null) return clause.present ? 'any' : 'none';
	return valuesLabel(clause.values, {
		all: clause.match !== 'any',
		excluded: clause.negated
	});
}

/**
 * A list of values as somebody would say it, for typed and clicked chips alike. `not` rather than a
 * minus; an excluded list is always "or".
 */
export function valuesLabel(
	values: string[],
	{ all = false, excluded = false }: { all?: boolean; excluded?: boolean } = {}
): string {
	const joined = values.join(excluded || !all ? ' or ' : ' and ');
	return excluded ? `not ${joined}` : joined;
}

export const searchBox = new SearchBox();
