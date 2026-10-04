/* Search by meaning: what the install can do, and the three things somebody can ask it to do.
 *
 * One place rather than in the pane, because two screens read it: the settings pane, and the
 * control in the search box that has to know whether the feature is available at all
 * before it offers itself.
 *
 * **Nothing here decides anything.** Every field arriving from the server is the server's answer;
 * the browser draws it. In particular `supported` is a fact about the machine Sift runs on, and a
 * screen that guessed at it would offer somebody a switch that cannot work.
 */

import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';

/** The consent gate, named here for the reason recognition's is (see `$lib/people/faces.svelte`). */
export const SEMANTIC_ENABLED_KEY = 'semantic.enabled';

/** What describing and searching run on. Named here for the reason recognition's is. */
export const SEMANTIC_DEVICE_KEY = 'semantic.device';

export type SemanticStatus = components['schemas']['SemanticStatus'];

interface JobStarted {
	job_id: string;
}

export type IndexRemoved = components['schemas']['IndexRemoved'];

export function semanticStatus(): Promise<SemanticStatus> {
	return api.get<SemanticStatus>('/semantic/status');
}

/** Fetch the models. `again` re-downloads files that are already on disk, which is the repair path
 *  for a model that is present but is not the one Sift expects. It refuses to load and says so,
 *  and this is the only way to act on that from the app. */
export function fetchSemanticModels(again = false): Promise<JobStarted> {
	return api.post<JobStarted>('/semantic/models/fetch', again ? { query: { again: true } } : {});
}

export function removeSemanticIndex(): Promise<IndexRemoved> {
	return api.del<IndexRemoved>('/semantic/index');
}

/*
 * The two long jobs this feature can be asked to do, by the names the queue knows them by.
 *
 * Constants rather than typed strings at the call sites: a reader filtered by a job type that does
 * not exist returns nothing at all, which reads exactly like a job that has already finished.
 *
 * How far one of them has got is read off the jobs list by `$lib/jobs/watch-download`, which is
 * shared with the graphics-card download. Read rather than pushed, because the jobs list is already
 * the one place a job's progress lives and a second channel for the same number is a second number.
 */
export const FETCHING_MODELS = 'semantic_fetch_models';

export type SimilarItem = components['schemas']['SimilarItem'];

export type SimilarPage = components['schemas']['SimilarPage'];

export function findSimilar(assetId: string): Promise<SimilarPage> {
	return api.get<SimilarPage>(`/assets/${encodeURIComponent(assetId)}/similar`);
}

export type SemanticCoverage = components['schemas']['SemanticCoverage'];

/** How much of what this account can see has been described. Two counts, scoped to whoever asks;
 *  the sentence under a set of results found by meaning divides them. */
export function semanticCoverage(): Promise<SemanticCoverage> {
	return api.get<SemanticCoverage>('/semantic/coverage');
}

export type SemanticAvailable = components['schemas']['SemanticAvailable'];

/** Whether the search box should offer to search by meaning. A courtesy, not a permission: asking
 *  for the order anyway is safe and simply gets the ordinary one. */
export function semanticAvailable(): Promise<SemanticAvailable> {
	return api.get<SemanticAvailable>('/semantic/available');
}
