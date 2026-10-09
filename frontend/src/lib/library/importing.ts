/* What happens to a file when it arrives: the keys that govern it, and the Build that fills it
 * in. */
import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { WATERMARKS_READ_ON_IMPORT_KEY } from '$lib/library/watermarks.svelte';

/** The master over building the pictures a file is drawn with. */
export const GENERATE_KEY = 'importing.generate';
/** The master over working out what is in a file. */
export const IDENTIFY_KEY = 'importing.identify';

/** The master over Sift going looking: the walk, the whole-library pass and the catch-up. */
const SCAN_KEY = 'importing.scan';

/** What a scan decides while it reads a folder: the parts that are optional. */
export const SCAN_KEYS = [
	'photo_sets.from_folders',
	'photo_sets.from_archives',
	'suggestions.scan',
	'suggestions.file_from_filenames',
	'suggestions.read_metadata',
	'dedup.scan'
] as const;

/** Reading a file's sound for its music fingerprint. */
const MUSIC_FINGERPRINT_KEY = 'music.fingerprint';

/** The products a folder may answer for. The thumbnail is not among them: every file gets one. */
export const GENERATE_KEYS = [
	'performance.generate_previews',
	'performance.generate_sprites',
	'performance.generate_fingerprints',
	/* Reading a file's sound, so Sift can say which song it is. */
	MUSIC_FINGERPRINT_KEY,
	'performance.repair_playback'
] as const;

/** Working out what is in a file. One per feature that does it. */
export const IDENTIFY_KEYS = [
	'performance.scan_faces_on_import',
	'semantic.describe_on_import',
	WATERMARKS_READ_ON_IMPORT_KEY
] as const;

/* Which products each stage's "now" button goes over the library for. */
export const GENERATE_PRODUCTS = ['thumbnails', 'previews', 'sprites', 'fingerprints'] as const;
export const IDENTIFY_PRODUCTS = ['faces', 'meaning', 'watermarks'] as const;

export type FolderList = components['schemas']['FolderList'];
export type FolderAnswers = components['schemas']['FolderAnswers'];
export type BuildSheet = components['schemas']['BuildSheet'];
export type BuildRow = components['schemas']['BuildRow'];
export type BuildStarted = components['schemas']['BuildStarted'];

export function fetchFolderAnswers(): Promise<FolderList> {
	return api.get<FolderList>('/importing/folders');
}

/** Answer for one folder. A key set to null puts it back to following the library. */
export function setFolderAnswers(
	rootId: string,
	answers: Record<string, boolean | null>
): Promise<FolderAnswers> {
	return api.put<FolderAnswers>(`/importing/folders/${rootId}`, { body: { answers } });
}

/** What a Build would do: one row per product with its count and its price. Queues nothing. */
export function fetchBuildSheet(): Promise<BuildSheet> {
	return api.get<BuildSheet>('/importing/build');
}

export type RetryResult = components['schemas']['RetryResult'];

/** Forget what these products have given up on, so the next Build offers those files again. */
export function retryBuild(products: string[]): Promise<RetryResult> {
	return api.post<RetryResult>('/importing/build/retry', { body: { products } });
}

/** Start a Build for the ticked products, now or when the quiet hours begin. */
export function startBuild(products: string[], tonight: boolean): Promise<BuildStarted> {
	return api.post<BuildStarted>('/importing/build', { body: { products, tonight } });
}
