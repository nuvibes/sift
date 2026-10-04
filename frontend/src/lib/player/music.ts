/*
 * The music a file shares with others, as the file page asks for it.
 *
 * One read, and nothing here decides anything. The group is worked out by the server from the
 * fingerprints it paired, and every file in the answer has already been resolved against whoever is
 * asking. So a file this account may not see never arrives, and is never counted either. A file
 * the account may not see, or one that does not exist, answers the same empty group rather than a
 * refusal: a reply that differed would be a way of asking whether a file exists.
 */

import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';

/** The files sharing a song with one file, closest first, and how many there are. */
export type SameMusic = components['schemas']['SameMusic'];

/** One of them: what a tile in the strip needs to draw it. */
export type SameMusicFile = components['schemas']['SameMusicFile'];

/** Where a file's song name came from, and the file it was shared from. */
export type MusicFrom = components['schemas']['MusicFrom'];

/**
 * The parameter the Files wall filters by to show one file's same-music group.
 *
 * The query language's own word (`same_music:<id>`), written as a named parameter so a link to it
 * is an ordinary address and the bar draws it as an ordinary chip. Its value is the file's ID, not a
 * name: a file has no name a person would type, and two files can share a title.
 */
export const SAME_MUSIC_FIELD = 'same_music';

export function sameMusicOf(assetId: string): Promise<SameMusic> {
	return api.get<SameMusic>(`/assets/${encodeURIComponent(assetId)}/same-music`);
}

/** The Files wall showing every file that shares a song with this one. */
export function sameMusicWall(assetId: string): string {
	return `/browse?${new URLSearchParams({ [SAME_MUSIC_FIELD]: assetId })}`;
}
