/*
 * The Files wall filtered to what is similar to one file, and the name a chip for one file reads.
 *
 * The query language's own word (`like:<id>`), written as a named parameter so a link to it is an
 * ordinary address and the bar draws it as an ordinary chip, the shape `same_music` already has.
 * The server answers it as the strip under a file does: Smart Search where the file has been
 * described, perceptual hashes where it has not, and only files the viewer may see.
 */

import type { components } from '$lib/api/schema';

/** The parameter the Files wall filters by to show the files similar to one file. */
export const LIKE_FIELD = 'like';

/** The Files wall showing the files similar to this one, closest first. */
export function likeWall(assetId: string): string {
	return `/browse?${new URLSearchParams({ [LIKE_FIELD]: assetId })}`;
}

/**
 * What one file is called on a chip that names it: its title, else the name it has on disk.
 *
 * The rule the file page's own strips name it by, so a chip reads the same words whether the
 * strip's press named it or the bar asked the file.
 */
export function fileNameOf(
	file: Pick<components['schemas']['AssetDetail'], 'title' | 'filename' | 'original_filename'>
): string | null {
	return file.title || file.filename || file.original_filename || null;
}
