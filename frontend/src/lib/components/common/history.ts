/*
 * What a history event is, its glyph and its links; an unknown kind falls through to a plain mark.
 */
import type { components } from '$lib/api/schema';
import type { IconName } from '$lib/design/icons';
import { facetValueIcon, madeIcon, madeLabel } from '$lib/components/shell/facet-labels';
import { iconOf, pageOf, type EntityKind } from '$lib/entity/related.svelte';
import { onRecord, span } from '$lib/shell/when';

/** One thing that happened to a file, exactly as the server describes it: the generated schema. */
export type HistoryEvent = components['schemas']['HistoryEvent'];

/** What would take one event back, and which door does it: `move` or `decision`. */
export type HistoryUndo = components['schemas']['UndoPoint'];

/** One thing a sentence names, and where it lives. Taken from the schema, like the event. */
export type HistoryLink = components['schemas']['HistoryLink'];

/** One group a folded line stands for, with the server's phrase for it. */
export type HistoryDetail = components['schemas']['HistoryDetail'];

/** This account's own act, as the server writes `actor`. */
export const BY_YOU = 'you';

/** The glyph each kind wears, by the family it belongs to rather than by the verb. */
const MARKS: Record<string, IconName> = {
	added: 'add',
	moved: 'drive_file_move',
	renamed: 'edit_square',
	undone: 'history',
	/* Organize's tray: the event says a thing was settled at the workbench, not approved. */
	decided: 'inbox',
	named: 'person',
	tagged: 'shoppingmode',
	filed: 'public',
	enriched: 'inventory_2',
	/* A box asked that answered nothing wears the search glyph, the box's own mark being for a find. */
	asked: 'backlight_low',
	face_run: 'familiar_face_and_zone',
	confirmed: 'person_add',
	/* Sift's attach off a reference picture: the mark comes from `via` (`faces`); this is the fallback. */
	matched: 'familiar_face_and_zone',
	rejected: 'group_off',
	copied_from: 'file_copy',
	/* `copied_from`'s glyph: one row read from both ends, and the sentence says which way round. */
	copied_into: 'file_copy',
	shared: 'group',
	watermark: 'position_bottom_right',
	downloaded: 'download',
	/* A download that gave up: the broken link, since nothing arrived. */
	download_failed: 'link_off',
	ready: 'error_med',
	/* A pass that gave up on the file: the app's warning. */
	left_out: 'warning',
	face_off: 'hide_image',
	ruled_out: 'block',
	hidden: 'visibility_off',
	kept_mine: 'data_info_alert',
	/* What Sift learnt to recognise somebody BY, on a person's thread: the face glyph. */
	taught: 'familiar_face_and_zone',
	removed: 'link_off',
	revealed: 'visibility',
	edited: 'edit',
	deleted: 'delete',
	merged: 'merge',
	kept_local: 'lock',
	allowed: 'public',
	scanned: 'document_scanner',
	/* A copy saved to somebody's own device: the floppy disk, drawn nowhere else. */
	saved: 'save',
	paused: 'pause',
	resumed: 'play_arrow',
	cookies_saved: 'cookie',
	cookies_replaced: 'cookie',
	cookies_forgotten: 'cookie',
	canceled: 'cancel',
	ran: 'event_repeat',
	/* A task somebody ran on one file from Run task: the play glyph that menu wears. */
	pressed: 'play_arrow',
	/* A song named from the page a file was downloaded from: the music note. */
	song_named: 'music_note_2',
	swap_started: 'swap_horiz',
	swap_ended: 'swap_horiz',
	/* The library put back to a backup: the Backup and restore section's own glyph. */
	restored: 'settings_backup_restore',
	/* A library created from a database file: the glyph its Import button wears. */
	adopted: 'upload',
	wall_sent: 'connected_tv',
	kept_from_swaps: 'swap_horiz',
	allowed_in_swaps: 'swap_horiz',
	sharing_turned_on: 'lan',
	sharing_turned_off: 'lan',
	start_with_windows_on: 'autoplay',
	start_with_windows_off: 'autostop',
	firewall_opened: 'shield',
	storage_moved: 'drive_file_move',
	update_started: 'download',
	library_opened: 'photo_library',
	restarted: 'sync'
};

/** The glyph for the verb that made a copy, before `copied_*`, which unknowns fall to. */
const COPY_MARKS: Record<string, IconName> = {
	trim: 'content_cut',
	clip: 'content_cut',
	gif: 'gif',
	compress: 'compress',
	crop: 'crop',
	rotate: 'rotate_right'
	/* No `resize`: the font has no glyph for it that does not read as a crop. */
};

/** The mark for one event: `via` (as the `enriched:` filter), then `how`, then the kind. */
export function markOf(kind: string, via?: string | null, how?: string | null): IconName {
	const said = via ? madeIcon(via, how) : undefined;
	const did = how ? COPY_MARKS[how] : undefined;
	return said ?? did ?? MARKS[kind] ?? 'info';
}

/** One row's mark words, for its tooltip. */
export function markWords(
	event: Pick<HistoryEvent, 'via' | 'means' | 'actor'> & { how?: string | null }
): string {
	if (event.actor === BY_YOU) return event.means || 'You did this';
	if (event.via && facetValueIcon('enriched', event.via) !== undefined) {
		return madeLabel(event.via, event.how);
	}
	return event.means || 'Something happened';
}

/** Everything this build knows how to draw one kind of named thing as. See `DRAWN`. */
interface KindDrawing {
	/** Where that kind's page is, or null for a kind that has none. See `files` in `DRAWN`. */
	page: (id: string) => string | null;
	mark: IconName;
	/** Its own entity kind, where it has a cover to ask for; null for the kinds that are not. */
	entity: EntityKind | null;
}

/** What this build knows about each kind: its page, mark and cover, in one table. */
const DRAWN: Record<string, KindDrawing> = {
	person: { page: (id) => pageOf('person', id), mark: iconOf('person'), entity: 'person' },
	site: { page: (id) => pageOf('site', id), mark: iconOf('site'), entity: 'site' },
	tag: { page: (id) => pageOf('tag', id), mark: iconOf('tag'), entity: 'tag' },
	collection: {
		page: (id) => pageOf('collection', id),
		mark: iconOf('collection'),
		entity: 'collection'
	},
	photo_set: {
		page: (id) => pageOf('photo_set', id),
		mark: iconOf('photo_set'),
		entity: 'photo_set'
	},
	song: { page: (id) => pageOf('song', id), mark: iconOf('song'), entity: 'song' },
	folder: {
		page: (id) => `/browse?in=${encodeURIComponent(id)}`,
		mark: 'folder',
		entity: null
	},
	/* A file's address; HistoryRow opens it in place. */
	asset: { page: (id) => `/asset/${id}`, mark: 'article', entity: null },
	/* A group of faces not yet named: its place in the organizer, with its piles' own mark. */
	face_pile: {
		page: (id) => `/organize/faces-to-name/${id}`,
		mark: 'familiar_face_and_zone',
		entity: null
	},
	/* A username falls back to its files; the server's `href` wins. */
	username: {
		page: (id) => `/browse?username=${encodeURIComponent(id)}`,
		mark: 'alternate_email',
		entity: null
	},
	files: { page: () => null, mark: 'article', entity: null },
	/* A field of the record, listed under a stash-box's "Show each": no page, a chip to read. */
	field: { page: () => null, mark: 'edit', entity: null },
	faces: { page: () => null, mark: 'familiar_face_and_zone', entity: null },
	/* A row on the downloads queue, for a download that never produced a file. */
	download: {
		page: (id) => `/downloads?row=${encodeURIComponent(id)}`,
		mark: 'download',
		entity: null
	}
};

/** Where one link goes: the server's `href`, then the kind's page, else nowhere. */
export function hrefOf(link: HistoryLink): string | null {
	return link.href ?? DRAWN[link.kind]?.page(link.id) ?? null;
}

/** The mark a kind wears, or null for one this build cannot draw. */
export function markOfLinkKind(kind: string): IconName | null {
	return DRAWN[kind]?.mark ?? null;
}

/** That kind as an entity with a cover to ask for (`entityPicture`), or null where it has none. */
export function entityKindOf(kind: string): EntityKind | null {
	return DRAWN[kind]?.entity ?? null;
}

/** One run of a History line, exactly as the server builds it. See `HistorySentence`. */
export type HistoryPiece = components['schemas']['HistoryPiece'];

/** Where one piece of a line goes, or null; a thing that has gone goes nowhere. */
export function hrefOfPiece(piece: HistoryPiece): string | null {
	if (piece.gone) return null;
	if (piece.kind === null) return piece.href ?? null;
	return piece.href ?? DRAWN[piece.kind]?.page(piece.id ?? '') ?? null;
}

/* History lines arrive as server-built pieces; `sentenceParts` is for the Organize cards. */

/** One run of an Organize card's sentence: plain words, or words that stand for something. */
interface SentencePart {
	text: string;
	link: HistoryLink | null;
}

/* A letter or a digit in any script: what may not touch a name for it to count as said. */
const WORD = /[\p{L}\p{N}]/u;

/** Whether `name` sits at `at` in `what` as a whole name: nothing of a word touching either end. */
function standsAlone(what: string, name: string, at: number): boolean {
	if (!what.startsWith(name, at)) return false;
	const before = what[at - 1];
	const after = what[at + name.length];
	return !(before && WORD.test(before)) && !(after && WORD.test(after));
}

/** One sentence cut into named and plain runs: the longest name wins, whole names only. */
export function sentenceParts(
	what: string,
	links: readonly HistoryLink[] = []
): readonly SentencePart[] {
	// Longest first; empty names dropped, since one would match everywhere and loop.
	const named = links
		.filter((one) => one.name.length > 0)
		.sort((a, b) => b.name.length - a.name.length);
	if (named.length === 0) return [{ text: what, link: null }];

	const parts: SentencePart[] = [];
	let plain = '';
	let at = 0;
	while (at < what.length) {
		const found = named.find((one) => standsAlone(what, one.name, at));
		if (found === undefined) {
			plain += what[at];
			at += 1;
			continue;
		}
		if (plain) parts.push({ text: plain, link: null });
		plain = '';
		parts.push({ text: found.name, link: found });
		at += found.name.length;
	}
	if (plain) parts.push({ text: plain, link: null });
	return parts;
}

/** When it happened, as a person reads it; null predates the recording, said in words. */
export function whenText(at: number | null, now?: number): string {
	if (at === null) return 'Before this was recorded';
	return onRecord(at, { now });
}

/** When a run began, or null for one act. */
export function sinceOf(event: HistoryEvent): number | null {
	return typeof event.since === 'number' ? event.since : null;
}

/** When a run happened, first to last (`span`'s rule). */
export function spanText(at: number | null, since: number | null, now?: number): string {
	if (at === null || since === null || since >= at) return whenText(at, now);
	return span(since, at, now);
}
