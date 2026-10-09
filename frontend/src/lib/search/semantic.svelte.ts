/*
 * Search by meaning, for the settings pane and the search box; every field is the server's answer.
 */

import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';

/** The consent gate, as recognition's (`$lib/people/faces.svelte`). */
export const SEMANTIC_ENABLED_KEY = 'semantic.enabled';

export const SEMANTIC_DEVICE_KEY = 'semantic.device';

export type SemanticStatus = components['schemas']['SemanticStatus'];

interface JobStarted {
	job_id: string;
}

export type IndexRemoved = components['schemas']['IndexRemoved'];

export function semanticStatus(): Promise<SemanticStatus> {
	return api.get<SemanticStatus>('/semantic/status');
}

/** `again` re-downloads files already on disk: the repair for a model Sift refuses to load. */
export function fetchSemanticModels(again = false): Promise<JobStarted> {
	return api.post<JobStarted>('/semantic/models/fetch', again ? { query: { again: true } } : {});
}

export function removeSemanticIndex(): Promise<IndexRemoved> {
	return api.del<IndexRemoved>('/semantic/index');
}

/*
 * Constants: a reader filtered by a job type that does not exist returns nothing, like a finished
 * job.
 */
export const FETCHING_MODELS = 'semantic_fetch_models';

export type SimilarItem = components['schemas']['SimilarItem'];

export type SimilarPage = components['schemas']['SimilarPage'];

export function findSimilar(assetId: string): Promise<SimilarPage> {
	return api.get<SimilarPage>(`/assets/${encodeURIComponent(assetId)}/similar`);
}

export type SemanticCoverage = components['schemas']['SemanticCoverage'];

/** Two counts scoped to whoever asks. */
export function semanticCoverage(): Promise<SemanticCoverage> {
	return api.get<SemanticCoverage>('/semantic/coverage');
}

export type SemanticAvailable = components['schemas']['SemanticAvailable'];

/** A courtesy, not a permission. */
export function semanticAvailable(): Promise<SemanticAvailable> {
	return api.get<SemanticAvailable>('/semantic/available');
}
