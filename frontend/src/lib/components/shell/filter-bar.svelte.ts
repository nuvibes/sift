/**
 * What the filter bar works out without drawing anything: the address as a counting query, which
 * of the three states one value of a column is in, how a pick is written back, how a kept filter
 * sits on the screen it is pressed on, and the names a chip shows for the ids an address carries.
 *
 * Nothing here understands the query language: a value is taken apart and written back by the one
 * reader in `query-parts`, and a typed query is the server's to parse.
 */

import { api, type ApiPath } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import type { CheckState } from '$lib/components/common/Checkbox.svelte';
import { SAME_MUSIC_FIELD } from '$lib/player/music';
import { LIKE_FIELD, fileNameOf } from '$lib/search/like';
import { taken, written, type Named } from '$lib/search/query-parts';
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

/**
 * Every parameter, keeping a repeat as a repeat: a column with two values in it writes the name
 * twice, and keeping only the last would count every other column against half the pick.
 */
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
 * What one facet holds, split by sign. The server reads a repeated parameter as AND, so
 * `?person=ada&person=-grace` is "Ada, and not Grace". Merged rather than last-wins, because a
 * hand-written link may spell one sign across several parameters.
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

	const wanted = where.read();
	wanted.delete(facet);
	if (picksAreRepeated(subject)) {
		/* One parameter per value, the grammar the entity routes read: a repeat is "either of
		   these", and a refused value is its own parameter with the minus in front. */
		for (const one of included.values) wanted.append(facet, one);
		for (const one of excluded.values) wanted.append(facet, `-${one}`);
		where.write(wanted);
		return;
	}
	if (included.values.length > 0) wanted.append(facet, written(included));
	if (excluded.values.length > 0) wanted.append(facet, written(excluded));
	where.write(wanted);
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
 * The names a chip shows for the ids an address carries: a folder `?in=` filters to, a network or a
 * tag on a wall of things, one file `like` and `same_music` are about, one person `unnamed_face`
 * is about, and one History line's thing (`?filed=`, `?tagged=`, `?named=`).
 *
 * Each is asked of the thing's own scoped read, once per id while the bar lives, which is the one
 * answer to "may this viewer be told what this is called". One they may not see answers as an id
 * nobody minted does: the chip draws the id or a plain word for it, its wall is empty for the same
 * reason, and its cross is still the way out. Constructed while a component initialises, because it
 * keeps effects of its own.
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

	constructor(from: ChipSources) {
		this.from = from;
		$effect(() => this.askFolders());
		$effect(() => this.askFacetNames());
		$effect(() => this.askFileNames());
		$effect(() => this.askPersonNames());
		$effect(() => this.askFilings());
	}

	/** Whether this key is asked for the first time, and so to be asked now. */
	private first(key: string): boolean {
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

	/** A network or a tag on a wall of things, into the one memory the panel's counts fill. */
	private askFacetNames(): void {
		const subject = this.from.subject();
		for (const [name, value] of this.from.named()) {
			const kind = facetNames(subject, name);
			if (kind === undefined) continue;
			for (const one of taken(value).values) {
				if (!AN_ID.test(one) || facetNameKnown(name, one)) continue;
				if (!this.first(`${kind} ${name} ${one}`)) continue;
				const path = kind === 'site' ? `/sites/${one}` : `/tags/${one}`;
				api
					.get<Pick<components['schemas']['SiteView'] | components['schemas']['TagView'], 'name'>>(
						path as ApiPath
					)
					.then((found) => rememberFacetNames(name, [{ value: one, count: 0, label: found.name }]))
					.catch(() => undefined);
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
