/**
 * What the filter bar works out without drawing anything. It never parses the query language: a
 * value goes through `query-parts`, and a typed query is the server's.
 */

import { api, type ApiPath } from '$lib/api/client';
import { libraryChanges, whenChanged } from '$lib/library/changes.svelte';
import type { components } from '$lib/api/schema';
import type { CheckState } from '$lib/components/common/Checkbox.svelte';
import { SAME_MUSIC_FIELD } from '$lib/player/music';
import { LIKE_FIELD, fileNameOf } from '$lib/search/like';
import { taken, written, type Named } from '$lib/search/query-parts';
import { parameterValue } from '$lib/search/quoting';
import { goneLabel } from '$lib/search/saved-searches.svelte';
import type { ParsedClause } from '$lib/search/search.svelte';
import {
	FILING_PARAMETERS,
	facetNameKnown,
	facetNames,
	filingField,
	filingLabel,
	picksAreRepeated,
	readFiling,
	rememberFacetNames,
	wordsAreANamePrefix,
	type FilingParameter,
	type Subject
} from './facet-labels';
import type { Narrowing } from './screen-bar.svelte';
import { WORDS } from './wall-words';

/** Every parameter, a repeat kept as a repeat: a column of two values writes the name twice. */
export function asQuery(params: URLSearchParams): Record<string, string | string[]> {
	const out: Record<string, string | string[]> = {};
	for (const name of new Set(params.keys())) {
		const held = params.getAll(name);
		out[name] = held.length === 1 ? held[0] : held;
	}
	return out;
}

/**
 * The question as the counts route reads it: a wall of things is searched by its box's words, which
 * its routes take as a name matched anywhere (`prefix` with `anywhere`), not as `q`.
 */
export function asCounted(
	subject: Subject,
	held: Record<string, string | string[]>
): Record<string, string | string[]> {
	if (!wordsAreANamePrefix(subject)) return held;
	const { [WORDS]: words, ...rest } = held;
	const said = (Array.isArray(words) ? words[0] : words)?.trim() ?? '';
	return said ? { ...rest, prefix: said, anywhere: 'true' } : rest;
}

/**
 * What one facet holds, split by sign: `?person=ada&person=-grace` is "Ada, and not Grace", merged
 * rather than last-wins, since a hand-written link may spell one sign across several parameters.
 */
export function sides(facet: string, where: Narrowing): { included: Named; excluded: Named } {
	const included: Named = { excluded: false, all: false, values: [] };
	const excluded: Named = { excluded: true, all: false, values: [] };
	for (const raw of where.read().getAll(facet)) {
		const held = taken(raw);
		const into = held.excluded ? excluded : included;
		// The joiner belongs to the included list: an excluded one is "none of these" either way.
		if (!held.excluded && held.all) into.all = true;
		for (const one of held.values) if (!into.values.includes(one)) into.values.push(one);
	}
	return { included, excluded };
}

/** Which of the three a single value is in. */
export function stanceOf(facet: string, value: string, where: Narrowing): CheckState {
	const { included, excluded } = sides(facet, where);
	if (included.values.includes(value)) return 'on';
	if (excluded.values.includes(value)) return 'out';
	return 'off';
}

/**
 * A press on a row picks it and a second press takes it off, on every wall. Refusing a value is the
 * chip's own verb; a row already refused is taken off by a press.
 */
export function cycle(now: CheckState): CheckState {
	return now === 'off' ? 'on' : 'off';
}

/**
 * Move one value of one facet between the three states, leaving the rest of the facet alone. The
 * joiner survives (an all-of column stays all-of), and the sign is the value's own.
 */
export function pick(
	subject: Subject,
	facet: string,
	value: string,
	next: CheckState | undefined,
	where: Narrowing
): void {
	const { included, excluded } = sides(facet, where);
	const going = next ?? cycle(stanceOf(facet, value, where));

	included.values = included.values.filter((one) => one !== value);
	excluded.values = excluded.values.filter((one) => one !== value);
	if (going === 'on') included.values.push(value);
	if (going === 'out') excluded.values.push(value);

	where.write(placed(where.read(), facet, spelled(subject, included, excluded)));
}

/**
 * A pick drawn on the press, until the address it wrote says the same, keyed by facet and value.
 * Another screen is another address: nothing pressed on the last one stands there.
 */
export class PendingPicks {
	#held = $state<Record<string, CheckState>>({});
	#on = '';
	readonly #now: () => { subject: Subject; narrowing: Narrowing; path: string };

	constructor(now: () => { subject: Subject; narrowing: Narrowing; path: string }) {
		this.#now = now;
		$effect(() => this.#settle());
	}

	stance(facet: string, value: string, where: Narrowing): CheckState {
		const held = where === this.#now().narrowing ? this.#held[`${facet}\n${value}`] : undefined;
		return held ?? stanceOf(facet, value, where);
	}

	pick(facet: string, value: string, next: CheckState | undefined, where: Narrowing): void {
		const going = next ?? cycle(stanceOf(facet, value, where));
		const now = this.#now();
		if (where === now.narrowing) {
			this.#on = now.path;
			this.#held = { ...this.#held, [`${facet}\n${value}`]: going };
		}
		pick(now.subject, facet, value, going, where);
	}

	/* Let go of every pick the address now agrees with. */
	#settle(): void {
		const held = this.#held;
		const keys = Object.keys(held);
		if (keys.length === 0) return;
		const now = this.#now();
		if (now.path !== this.#on) {
			this.#held = {};
			return;
		}
		const read = now.narrowing.read();
		const where: Narrowing = { read: () => read, write: () => {} };
		const still = keys.filter((key) => {
			const [facet = '', value = ''] = key.split('\n');
			return stanceOf(facet, value, where) !== held[key];
		});
		if (still.length < keys.length) {
			this.#held = Object.fromEntries(still.map((key) => [key, held[key] as CheckState]));
		}
	}
}

/** One facet's parameters as the noun spells them. */
function spelled(subject: Subject, included: Named, excluded: Named): string[] {
	if (picksAreRepeated(subject)) {
		/* One parameter per value, the grammar the entity routes read: a repeat is "either of
		   these", and a refused value is its own parameter with the minus in front. */
		const refused = excluded.values.map((one) => `-${parameterValue(one)}`);
		return [...included.values.map(parameterValue), ...refused];
	}
	return [included, excluded].filter((one) => one.values.length > 0).map(written);
}

/**
 * The address with one facet's parameters replaced where its first one stood, so a chip keeps its
 * place; a facet not there yet goes at the end.
 */
export function placed(
	params: URLSearchParams,
	facet: string,
	values: readonly string[]
): URLSearchParams {
	const next = new URLSearchParams();
	let done = false;
	for (const [name, value] of params) {
		if (name !== facet) next.append(name, value);
		else if (!done) for (const one of values) next.append(facet, one);
		done ||= name === facet;
	}
	if (!done) for (const one of values) next.append(facet, one);
	return next;
}

/** The named-thing columns whose "Has" and "No" rows the server counts at the head. */
export const PRESENCE_COLUMNS: readonly string[] = [
	'tags',
	'people',
	'sites',
	'collections',
	'photo_sets',
	'songs'
];

/** The facets whose `any` and `none` ask whether a file has one at all. */
const PRESENCE: readonly string[] = [...PRESENCE_COLUMNS, 'loops'];

const OPPOSITE: Record<string, string> = { any: 'none', none: 'any' };

/** A presence value's swap ("Has tags" for "No tags"), or undefined. */
export function opposite(subject: Subject, facet: string, value: string): string | undefined {
	// A wall of things asks presence of its Tags column alone.
	return (subject === 'asset' ? PRESENCE.includes(facet) : facet === 'tags')
		? OPPOSITE[value]
		: undefined;
}

/** A press on a chip: a presence value swaps; any other is refused or taken back. */
export function flip(subject: Subject, facet: string, value: string, where: Narrowing): void {
	const other = opposite(subject, facet, value);
	if (other === undefined) {
		pick(subject, facet, value, stanceOf(facet, value, where) === 'out' ? 'on' : 'out', where);
		return;
	}
	const { included, excluded } = sides(facet, where);
	for (const held of [included, excluded]) {
		held.values = held.values.map((one) => (one === value ? other : one));
	}
	where.write(placed(where.read(), facet, spelled(subject, included, excluded)));
}

/** One chip: a value of a facet, the parameter it came from, and a key that outlives a flip. */
export interface Placed<T> {
	field: string;
	value: string;
	key: string;
	from: T;
}

/**
 * Each facet's chips in the order the bar first saw them, so a refused value (a parameter of its
 * own) or a swapped one (`same`) is drawn where it stood, under its key.
 */
export class ChipOrder {
	private seen = new Map<string, string[]>();

	lay<T>(
		held: readonly T[],
		read: (one: T) => { field: string; values: readonly string[] },
		same: (field: string, value: string) => string = (_field, value) => value
	) {
		const byField = new Map<string, Omit<Placed<T>, 'key'>[]>();
		for (const one of held) {
			const { field, values } = read(one);
			const row = byField.get(field) ?? [];
			byField.set(field, row);
			for (const value of values) row.push({ field, value, from: one });
		}
		for (const field of [...this.seen.keys()]) if (!byField.has(field)) this.seen.delete(field);
		const out: Placed<T>[] = [];
		for (const [field, row] of byField) {
			const now = row.map((chip) => same(field, chip.value));
			const order = (this.seen.get(field) ?? []).filter((one) => now.includes(one));
			for (const one of now) if (!order.includes(one)) order.push(one);
			this.seen.set(field, order);
			const times = new Map<string, number>();
			const at = (chip: { value: string }) => order.indexOf(same(field, chip.value));
			const sorted = [...row].sort((a, b) => at(a) - at(b));
			for (const chip of sorted) {
				const slot = same(field, chip.value);
				const n = times.get(slot) ?? 0;
				times.set(slot, n + 1);
				out.push({ ...chip, key: `${field}=${slot}#${n}` });
			}
		}
		return out;
	}
}

/** The parameters that ask the question beside a wall's filters, and how they are read. */
const ASKED_WITH: readonly string[] = ['q', 'depth', 'meaning'];
const ORDER: readonly string[] = ['sort', 'seed'];
/** A place in the previous answer, which is a place in a different question. */
const PLACE: readonly string[] = ['from', 'offset'];

/**
 * How the question a kept filter asks sits on the screen it was pressed on. Its filters and words
 * replace the screen's outright (two merged would be a third nobody chose); its order wins where it
 * has one; everything else is the screen's, above all which tab of a person's page is open.
 */
export function keptOnThisScreen(
	screen: URLSearchParams,
	kept: URLSearchParams,
	keptNames: readonly string[]
): URLSearchParams {
	const question = new Set<string>([...keptNames, ...ASKED_WITH]);
	const keptOrder = ORDER.some((name) => kept.has(name));
	const next = new URLSearchParams();
	for (const [name, value] of screen) {
		if (question.has(name) || PLACE.includes(name)) continue;
		if (keptOrder && ORDER.includes(name)) continue;
		next.append(name, value);
	}
	for (const [name, value] of kept) {
		if (question.has(name) || ORDER.includes(name)) next.append(name, value);
	}
	return next;
}

/** An id as the server mints them, which is what a chip asks a name for. */
const AN_ID = /^[0-9A-HJKMNP-TV-Z]{26}$/;

/** The parameters that take one file's id. */
const FILE_FIELDS: readonly string[] = [LIKE_FIELD, SAME_MUSIC_FIELD];

/** The parameters that take one person's id. */
const PERSON_FIELDS: readonly string[] = ['unnamed_face'];

/** Where a wall of files reads the name of a thing its address keeps by id. */
const FILE_WALL_READS: Record<string, string> = {
	people: '/people/',
	tags: '/tags/',
	sites: '/sites/',
	collections: '/collections/',
	photo_sets: '/photo-sets/',
	songs: '/songs/'
};

type NamedThing =
	| components['schemas']['PersonView']
	| components['schemas']['TagView']
	| components['schemas']['SiteView']
	| components['schemas']['CollectionSummary']
	| components['schemas']['PhotoSetSummary']
	| components['schemas']['SongSummary'];

const GONE_AS: Record<'site' | 'tag', string> = { site: 'sites', tag: 'tags' };

/** The page one History line's thing is read from, scoped to the viewer like the wall. */
function filingPage(parameter: FilingParameter, id: string): ApiPath {
	const at = encodeURIComponent(id);
	if (parameter === 'filed') return `/sites/${at}`;
	if (parameter === 'tagged') return `/tags/${at}`;
	return `/people/${at}`;
}

/** What the three reads answer with; the name is all a chip takes from them. */
type FilingThing =
	| components['schemas']['SiteView']
	| components['schemas']['TagView']
	| components['schemas']['PersonView'];

/** What the bar reads to name the ids its chips carry. */
interface ChipSources {
	subject(): Subject;
	/** What the chips describe. */
	describing(): Narrowing;
	/** The named filters in force, as pairs. */
	named(): [string, string][];
	/** The typed query's clauses, as the server parsed them. */
	clauses(): ParsedClause[];
}

/**
 * The names a chip shows for the ids an address carries, each asked of the thing's own scoped
 * read once per id, so an id this viewer may not see stays an id. Built during component init, for
 * its effects.
 */
export class ChipNames {
	private from: ChipSources;
	folders = $state<Record<string, string | null>>({});
	private filingNames = $state<Record<string, string | null>>({});
	private asked = new Set<string>();

	/** One History line per value, with what its parameter reads as. */
	readonly filings = $derived.by(() => {
		if (this.from.subject() !== 'asset') return [];
		const held = this.from.describing().read();
		return FILING_PARAMETERS.flatMap((parameter) =>
			held
				.getAll(parameter)
				.filter((value) => value.trim() !== '')
				.map((value) => ({ parameter, value, filing: readFiling(parameter, value) }))
		);
	});

	/** What each line's chip says, and where its body goes: the thing's own page. */
	readonly filingChips = $derived(
		this.filings.map(({ parameter, value, filing }) => {
			const key = filing ? `${parameter} ${filing.subject}` : '';
			const name = filing ? (this.filingNames[key] ?? null) : null;
			return {
				parameter,
				value,
				shown: filingField(parameter),
				label: filing ? filingLabel(filing, name) : value,
				href: filing && name !== null ? filingPage(parameter, filing.subject) : undefined
			};
		})
	);

	/* Moved by the library bell, so every name is asked again: a rename elsewhere reaches the chips. */
	private round = $state(0);

	constructor(from: ChipSources) {
		this.from = from;
		whenChanged(libraryChanges, () => {
			this.asked.clear();
			this.round += 1;
		});
		$effect(() => this.askFolders());
		$effect(() => this.askFacetNames());
		$effect(() => this.askFileNames());
		$effect(() => this.askPersonNames());
		$effect(() => this.askFilings());
	}

	/** Whether this key is asked for the first time, and so to be asked now. */
	private first(key: string): boolean {
		void this.round;
		if (this.asked.has(key)) return false;
		this.asked.add(key);
		return true;
	}

	/** A folder by id (a History line's link writes the id, which names a library's top folder too). */
	private askFolders(): void {
		for (const value of this.from.describing().read().getAll('in')) {
			for (const one of taken(value).values) {
				if (!AN_ID.test(one) || !this.first(`folder ${one}`)) continue;
				api
					.get<components['schemas']['FolderDetail']>(`/library/folders/${encodeURIComponent(one)}`)
					.then((found) => (this.folders = { ...this.folders, [one]: found.name || null }))
					.catch(() => (this.folders = { ...this.folders, [one]: null }));
			}
		}
	}

	/** A thing kept by id on any wall, into the one memory the panel's counts fill. */
	private askFacetNames(): void {
		const subject = this.from.subject();
		for (const [name, value] of this.from.named()) {
			const kind = facetNames(subject, name);
			const read =
				subject === 'asset'
					? FILE_WALL_READS[name]
					: kind && (kind === 'site' ? '/sites/' : '/tags/');
			if (!read) continue;
			const field = kind ? GONE_AS[kind] : name;
			for (const one of taken(value).values) {
				if (!AN_ID.test(one) || facetNameKnown(name, one)) continue;
				if (!this.first(`${read} ${name} ${one}`)) continue;
				api
					.get<Pick<NamedThing, 'name'>>(`${read}${encodeURIComponent(one)}` as ApiPath)
					.then((found) => rememberFacetNames(name, [{ value: one, count: 0, label: found.name }]))
					.catch((error: { status?: number } | undefined) => {
						if (error?.status === 404)
							rememberFacetNames(name, [{ value: one, count: 0, label: goneLabel(field, one) }]);
					});
			}
		}
	}

	/** One file a filter is about, whether the address or the typed query names it. */
	private askFileNames(): void {
		for (const [name, one] of this.idsOf(FILE_FIELDS)) {
			if (!AN_ID.test(one) || facetNameKnown(name, one)) continue;
			if (!this.first(`file ${name} ${one}`)) continue;
			api
				.get<components['schemas']['AssetDetail']>(`/assets/${encodeURIComponent(one)}` as ApiPath)
				.then((found) => {
					const said = fileNameOf(found);
					if (said) rememberFacetNames(name, [{ value: one, count: 0, label: said }]);
				})
				.catch(() => undefined);
		}
	}

	/** One person a filter is about, whether the address or the typed query names them. */
	private askPersonNames(): void {
		for (const [name, one] of this.idsOf(PERSON_FIELDS)) {
			if (!AN_ID.test(one) || facetNameKnown(name, one)) continue;
			if (!this.first(`person ${name} ${one}`)) continue;
			api
				.get<components['schemas']['PersonView']>(`/people/${encodeURIComponent(one)}` as ApiPath)
				.then((found) => rememberFacetNames(name, [{ value: one, count: 0, label: found.name }]))
				.catch(() => undefined);
		}
	}

	/** Every (field, id) pair the address or the typed query gives one of these fields. */
	private idsOf(fields: readonly string[]): [string, string][] {
		return [
			...this.from
				.named()
				.filter(([name]) => fields.includes(name))
				.flatMap(([name, value]) =>
					taken(value).values.map((one): [string, string] => [name, one])
				),
			...this.from
				.clauses()
				.flatMap((clause) =>
					clause.field !== null && fields.includes(clause.field)
						? clause.values.map((one): [string, string] => [clause.field as string, one])
						: []
				)
		];
	}

	/** The thing behind each History line. */
	private askFilings(): void {
		for (const { parameter, filing } of this.filings) {
			if (!filing) continue;
			const key = `${parameter} ${filing.subject}`;
			if (!this.first(`filing ${key}`)) continue;
			api
				.get<FilingThing>(filingPage(parameter, filing.subject))
				.then((found) => (this.filingNames = { ...this.filingNames, [key]: found.name }))
				.catch(() => (this.filingNames = { ...this.filingNames, [key]: null }));
		}
	}

	/** What an `in` value reads as on its chip: a folder's name for its id, a path as written. */
	folderSaid(value: string): string {
		if (!AN_ID.test(value)) return value;
		return this.folders[value] ?? 'a folder';
	}
}
