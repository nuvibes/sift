/*
 * THE FACET PANEL'S COUNTS, held ready before the panel opens.
 *
 * Asked when a filterable screen has drawn its first page, and again whenever the live feed says
 * the library moved, with the old numbers left drawn until the new ones land. "Counting..." is
 * then only for a question never asked on this tab. One batch in flight and one waiting, the
 * newest question, so a toggle asks each column once.
 */

import { untrack } from 'svelte';
import { api } from '$lib/api/client';
import { readStored, writeStored, clearStored } from '$lib/shell/remembered.svelte';
import { session } from '$lib/shell/session.svelte';
import { vault } from '$lib/shell/vault.svelte';
import { arrivals, libraryChanges } from '$lib/library/changes.svelte';
import {
	facetRoute,
	facetsFor,
	rememberFacetNames,
	type FacetCounts as Counted,
	type FacetValue,
	type Subject
} from './facet-labels';
import { ableTo, screenBar } from './screen-bar.svelte';

type Query = Record<string, string | string[]>;

/** One question the panel asks: which noun, which columns, and the set they are counted over. */
export interface FacetQuestion {
	noun: Subject;
	facets: readonly string[];
	query: Query;
	within?: Query;
}

/** Five columns at a time; five, not six, so a name still fits a column on a 1280 screen. */
export const COLUMNS = 5;

/** Where the columns each noun shows are remembered between visits. */
const COLUMNS_KEY = 'sift.filters.columns';

export function columnsKey(noun: Subject): string {
	return `${COLUMNS_KEY}.${noun}`;
}

/** The dimensions this account may ask about for a noun, less the ones the wall already is. */
export function offeredFacets(noun: Subject, fixed: readonly string[] = []) {
	return facetsFor(noun).filter(
		(one) => (!one.admin || session.isAdmin) && !fixed.includes(one.key)
	);
}

/** Whether a dimension is a span the panel draws itself rather than a set the server counts. */
export function isASpan(noun: Subject, key: string): boolean {
	return facetsFor(noun).some((one) => one.key === key && one.span === true);
}

/*
 * A note under the file wall's older key is moved to its own key rather than dropped: from
 * `remembered`, so a browser that never opens this is never written, and with no "done" flag.
 */
function movedToTheFileWall(): void {
	const old = readStored(COLUMNS_KEY);
	if (old === null) return;
	if (readStored(columnsKey('asset')) === null) writeStored(columnsKey('asset'), old);
	clearStored(COLUMNS_KEY);
}

/**
 * The five columns a noun opens on, as remembered: validated on the way back in (a dropped or
 * admin-only dimension, duplicates) and topped up from the ordinary order.
 */
export function rememberedColumns(noun: Subject, fixed: readonly string[] = []): string[] {
	const offered = offeredFacets(noun, fixed).map((one) => one.key);
	const firstFive = offered.slice(0, COLUMNS);
	movedToTheFileWall();
	const stored = readStored(columnsKey(noun));
	if (!stored) return firstFive;
	const kept: string[] = [];
	for (const key of stored.split(',')) {
		if (offered.includes(key) && !kept.includes(key)) kept.push(key);
	}
	for (const key of firstFive) {
		if (kept.length >= COLUMNS) break;
		if (!kept.includes(key)) kept.push(key);
	}
	return kept.slice(0, COLUMNS);
}

/** One column's own question: the screen's, less the column's own pick, within the target's. */
export function columnQuery(question: FacetQuestion, facet: string): Query {
	const { [facet]: _mine, ...rest } = question.query;
	const out: Query = { ...rest };
	for (const [name, value] of Object.entries(question.within ?? {})) {
		const held = [out[name] ?? [], value].flat();
		out[name] = held.length === 1 ? held[0] : held;
	}
	return out;
}

/** The question as a key, in name order so the same filters in another order are one question. */
function keyOf(question: FacetQuestion): string {
	const asked = [question.query, question.within ?? {}].map((one) =>
		Object.entries(one).sort(([name], [other]) => name.localeCompare(other))
	);
	return JSON.stringify([vault.generation, question.noun, question.facets, asked]);
}

/** Where the live feed stands: an answer older than this is drawn while it is asked again. */
function bells(): string {
	return `${libraryChanges.generation}|${arrivals.generation}`;
}

interface Held {
	counts: Record<string, FacetValue[]>;
	bells: string;
}

/** How many questions are kept, oldest out first: a tab's walls and their recent filters. */
const KEEP_AT_MOST = 24;

export class FacetCountStore {
	#held = $state<Record<string, Held>>({});
	#order: string[] = [];
	/** The question being asked now, as key and bells, and the newest one waiting behind it. */
	#asking: string | null = null;
	#waiting: FacetQuestion | null = null;

	/** The counts held for this question, current or about to be replaced; none if never asked. */
	answerTo(question: FacetQuestion): Record<string, FacetValue[]> | undefined {
		return this.#held[keyOf(question)]?.counts;
	}

	/** Ask, unless the answer held is current or the same question is already out. */
	ask(question: FacetQuestion): void {
		untrack(() => this.#ask(question));
	}

	#ask(question: FacetQuestion): void {
		if (question.facets.length === 0) return;
		const key = keyOf(question);
		const now = bells();
		if (this.#held[key]?.bells === now || this.#asking === `${key}|${now}`) return;
		if (this.#asking !== null) {
			this.#waiting = question;
			return;
		}
		this.#asking = `${key}|${now}`;
		void this.#fetch(question, key, now);
	}

	async #fetch(question: FacetQuestion, key: string, now: string): Promise<void> {
		try {
			const answers = await Promise.all(
				question.facets.map(async (facet) => {
					try {
						const answer = await api.get<Counted>(facetRoute(question.noun), {
							query: { ...columnQuery(question, facet), facet, limit: 200 }
						});
						/* The names beside the ids, kept for the chips, which hold only the id. */
						rememberFacetNames(facet, answer.values);
						return [facet, answer.values] as const;
					} catch {
						// A column that could not be filled draws nothing; the rest still holds.
						return [facet, [] as FacetValue[]] as const;
					}
				})
			);
			this.#keep(key, { counts: Object.fromEntries(answers), bells: now });
		} finally {
			this.#asking = null;
			const next = this.#waiting;
			this.#waiting = null;
			if (next !== null) this.#ask(next);
		}
	}

	#keep(key: string, held: Held): void {
		this.#order = [...this.#order.filter((one) => one !== key), key];
		const next = { ...this.#held, [key]: held };
		while (this.#order.length > KEEP_AT_MOST) {
			const oldest = this.#order.shift();
			if (oldest !== undefined) delete next[oldest];
		}
		this.#held = next;
	}

	/** Forget everything: a test's clean slate. */
	reset(): void {
		this.#held = {};
		this.#order = [];
		this.#asking = null;
		this.#waiting = null;
	}
}

export const facetCounts = new FacetCountStore();

/** What the bar asks ahead: the set the columns are counted over. */
interface AskedAhead {
	query: Query;
	within?: Query;
}

/**
 * Ask the counts for the columns the panel would open on, on a screen with filters, once it has
 * drawn its first page (or a second has passed), and again on every bell. Called from setup.
 */
export function countsAhead(asked: () => AskedAhead | null): void {
	let settled = $state(false);
	$effect(() => {
		void screenBar.tools.subject;
		settled = false;
		const waited = setTimeout(() => (settled = true), 1000);
		return () => clearTimeout(waited);
	});
	$effect(() => {
		void libraryChanges.generation;
		void arrivals.generation;
		const tools = screenBar.tools;
		const now = asked();
		if (now === null || !ableTo(tools.filterable)) return;
		if (tools.count === undefined && !settled) return;
		const noun = tools.subject ?? 'asset';
		const facets = rememberedColumns(noun, tools.fixed).filter((one) => !isASpan(noun, one));
		facetCounts.ask({ noun, facets, query: now.query, within: now.within });
	});
}
