/* What happens to a file when it arrives: the keys that govern it, and the Build that fills in
 * whatever was skipped.
 *
 * The keys are named here rather than read from the server, and that is deliberate for the same
 * reason recognition's and Smart Search's consent keys are: the screen has to group them before any
 * request has come back, and a group whose membership arrived asynchronously would draw itself once
 * empty and once full. What the server owns is the VALUE; the arrangement is the screen's.
 *
 * They are checked against the server all the same: `keys` on the folder list is the same map
 * that gates the jobs, and a key in a group here that is not in that list would be a switch nothing
 * reads. See `check_settings_followed.js`.
 */
import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { WATERMARKS_READ_ON_IMPORT_KEY } from '$lib/library/watermarks.svelte';

/** The master over building the pictures a file is drawn with. */
export const GENERATE_KEY = 'importing.generate';
/** The master over working out what is in a file. */
export const IDENTIFY_KEY = 'importing.identify';

/**
 * The master over Sift going looking: the walk, the whole-library pass and the catch-up.
 *
 * The third of the three. Reading a file's dimensions is not switched by this: a file with no
 * probe has no dimensions, which is a fact about a different job.
 */
const SCAN_KEY = 'importing.scan';

/** What a scan decides while it reads a folder. Always run; these are the parts that are optional.
 *
 * Reading a file's own NAME is here rather than under Identify, and the difference is what is
 * opened: the folder rules and the filename rule both work on rows a scan already has in hand, and
 * neither decodes anything. Identify is the stage that opens the file. The one exception sits
 * under the filename rule: naming an account from two fields of a picture's metadata does open a
 * file, and it is drawn here because it is the filename rule's own last step, off when that is. */
export const SCAN_KEYS = [
	'photo_sets.from_folders',
	'photo_sets.from_archives',
	'suggestions.scan',
	'suggestions.file_from_filenames',
	'suggestions.read_metadata',
	'dedup.scan'
] as const;

/** Reading a file's sound for its music fingerprint. A folder's own answer to it is stored under
 *  this key, and that answer decides only what happens as a file ARRIVES in the folder (a press
 *  of the music task passes it over), so a folder's page calls it by that (`ImportingFolders`). */
const MUSIC_FINGERPRINT_KEY = 'music.fingerprint';

/** The products a folder may answer for. The thumbnail is not among them: every file gets one. */
export const GENERATE_KEYS = [
	'performance.generate_previews',
	'performance.generate_sprites',
	'performance.generate_fingerprints',
	/* Reading a file's sound, so Sift can say which song it is. Beside the other fingerprints
	   because that is what it is, and under Generate because it is made from the file rather than
	   worked out about it. A folder may answer it on its own, like the rest of this group. */
	MUSIC_FINGERPRINT_KEY,
	'performance.repair_playback'
] as const;

/** Working out what is in a file. One per feature that does it.
 *
 * The third is the odd one: it is the only one of the three that is OFF out of the box, because it
 * opens the file to look at the picture for a Site's mark. See `watermarks/settings.py`. */
export const IDENTIFY_KEYS = [
	'performance.scan_faces_on_import',
	'semantic.describe_on_import',
	WATERMARKS_READ_ON_IMPORT_KEY
] as const;

/*
 * Which products each stage's "now" button goes over the library for. The keys are the server's
 * product keys, in the order the server lists them; each stage's counts on the Importing pane are
 * read off the sheet by these keys, and its button starts exactly these.
 *
 * Fingerprints sit with Generate: they are made rather than worked out, they are always on, and
 * they are what finds duplicates. A file on a task is read once for every product on it; the pass
 * is started per stage, by the stage's own verb.
 */
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
