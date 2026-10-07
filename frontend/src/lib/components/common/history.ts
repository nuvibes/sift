/*
 * What a history event IS, and the two answers every row needs before it can be drawn.
 *
 * A plain module, so the row, the list, the fetch and the tests name one shape, which the server
 * sends. The glyphs are a table, one place to add a kind, and an unknown kind falls through to a
 * mark saying "something happened": an older client meeting a newer server is ordinary for a
 * self-hosted app.
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

/**
 * One group of things a folded line stands for: its kind, the words the sentence counted it in, and
 * the things themselves. `words` is the server's phrase ("19 people"), so the heading and the count
 * in the line are one string.
 */
export type HistoryDetail = components['schemas']['HistoryDetail'];

/**
 * This account's own act, as the server writes it: the one `actor` word anything draws
 * differently, written once.
 */
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
	/* Each source wears the mark the app already uses for the same thing elsewhere. */
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
	/* Each wears a mark the app already has for the same thing. */
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
	/* The ledger acts that are not workbench decisions, each wearing its own control's glyph. */
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
	/* One swap, so the same arrows on both ends; the line says which in words. */
	swap_started: 'swap_horiz',
	swap_ended: 'swap_horiz',
	/* The library put back to a backup: the Backup and restore section's own glyph. */
	restored: 'settings_backup_restore',
	/* A library created from a database file: the glyph its Import button wears. */
	adopted: 'upload',
	/* A Theater wall sent to another device: the Send to desktop control's screen glyph. */
	wall_sent: 'connected_tv',
	/* "Do not swap" put on and taken off: the swap's own arrows, and the line says which in words. */
	kept_from_swaps: 'swap_horiz',
	allowed_in_swaps: 'swap_horiz',
	/* An act on the computer running Sift, asked from a window: each its control's own glyph. */
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

/**
 * The glyph for the VERB that made a copy, where the row says which verb it was: the editor's own
 * marks, the same at both ends of the row. In FRONT of `copied_from`/`copied_into`, so an unknown
 * operation (or several edits together) falls through to the copy glyph.
 */
const COPY_MARKS: Record<string, IconName> = {
	trim: 'content_cut',
	clip: 'content_cut',
	gif: 'gif',
	compress: 'compress',
	crop: 'crop',
	rotate: 'rotate_right'
	/* No `resize`: the font has no glyph for it that does not read as a crop. */
};

/**
 * The mark for one event: what did it where the row can say, and otherwise what kind it is.
 *
 * `via` first (a stash-box, a face, a folder name), read from `facetValueIcon('enriched', ...)`
 * so the row and the `enriched:` filter agree; a made tag's act wears `madeIcon`. `how` second, a
 * separate vocabulary (the editor's verbs). Unknowns fall through to the kind, then to "info".
 */
export function markOf(kind: string, via?: string | null, how?: string | null): IconName {
	const said = via ? madeIcon(via, how) : undefined;
	const did = how ? COPY_MARKS[how] : undefined;
	return said ?? did ?? MARKS[kind] ?? 'info';
}

/**
 * The words for one row's mark, as its tooltip: the server's `event.means` for the kind, and for a
 * `via` mark the words beside its glyph (`madeLabel`), one table shared with the `enriched:` filter.
 */
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
	/** The mark it wears wherever it is drawn as itself. */
	mark: IconName;
	/** Its own entity kind, where it has a cover to ask for; null for the kinds that are not. */
	entity: EntityKind | null;
}

/**
 * WHAT THIS BUILD KNOWS ABOUT EACH KIND: its page, its mark, and whether it has a cover.
 *
 * One table, so a new kind cannot be added to one question and missed by another. Entities go
 * through `pageOf` and `iconOf`, the one place for each; the others (a folder, a file, a group of
 * faces, a username, a set) say what their address is here. Anything else is drawn as plain words.
 */
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
	/* A song: a piece of music some files carry, with a page of its own like a Photo Set's. */
	song: { page: (id) => pageOf('song', id), mark: iconOf('song'), entity: 'song' },
	folder: {
		page: (id) => `/browse?in=${encodeURIComponent(id)}`,
		mark: 'folder',
		entity: null
	},
	/* A file has no wall, so its address is written here. A bare anchor to it would tear down the
	   panel; `HistoryRow` handles that (see the note on its anchor). */
	asset: { page: (id) => `/asset/${id}`, mark: 'article', entity: null },
	/* A group of faces not yet named: its place in the organizer, with its piles' own mark. */
	face_pile: {
		page: (id) => `/organize/faces-to-name/${id}`,
		mark: 'familiar_face_and_zone',
		entity: null
	},
	/*
	 * A username's fallback page is its files; the server's `href` (the person's page when somebody
	 * is behind it) wins. `files` is a set, whose address only the server knows, so its fallback
	 * is nowhere.
	 */
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

/**
 * The address one link goes to, or null where there is nowhere for it to go: the server's `href`
 * first (a set only the counting read knows), then the kind's page, else plain words.
 */
export function hrefOf(link: HistoryLink): string | null {
	return link.href ?? DRAWN[link.kind]?.page(link.id) ?? null;
}

/**
 * The mark one KIND of named thing wears, or null for a kind this build cannot draw: the heading
 * of a group of things, the same mark the rail and that page use.
 */
export function markOfLinkKind(kind: string): IconName | null {
	return DRAWN[kind]?.mark ?? null;
}

/** That kind as an entity with a cover to ask for (`entityPicture`), or null where it has none. */
export function entityKindOf(kind: string): EntityKind | null {
	return DRAWN[kind]?.entity ?? null;
}

/** One run of a History line, exactly as the server builds it. See `HistorySentence`. */
export type HistoryPiece = components['schemas']['HistoryPiece'];

/**
 * Where one piece of a line goes, or null: the server's address, then the kind's page. A thing
 * that has GONE goes nowhere and is drawn struck through. Words with an address and no kind are a
 * place on a screen (an Organize card on Insights) and go there; plain words carry neither.
 */
export function hrefOfPiece(piece: HistoryPiece): string | null {
	if (piece.gone) return null;
	if (piece.kind === null) return piece.href ?? null;
	return piece.href ?? DRAWN[piece.kind]?.page(piece.id ?? '') ?? null;
}

/*
 * History lines arrive as pieces built by the server (`kernel/access/sentences.py`), drawn by
 * `HistorySentence`. `sentenceParts` remains for the Organize cards, whose questions are still a
 * sentence and a list of names.
 */

/** One run of an Organize card's sentence: plain words, or words that stand for something. */
interface SentencePart {
	text: string;
	/** Null for plain words. */
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

/**
 * One sentence, cut into the runs that name something and the runs that do not.
 *
 * Every character comes out in order. At each position the longest name wins (so a name that is
 * a prefix of another never splits it), every occurrence is linked, and a name counts only where
 * no letter or digit touches it (`saidIn`, the server's `said_in`). A pass down the string, since
 * a typed name may hold `(` or `.`.
 */
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

/**
 * When it happened, as a person would read it (`onRecord`). A null time means the row predates
 * the recording of moments, said in words rather than as a false date. `now` is for tests.
 */
export function whenText(at: number | null, now?: number): string {
	if (at === null) return 'Before this was recorded';
	return onRecord(at, { now });
}

/**
 * When a line that stands for a run began (`since`; `at` is its last act), or null for one act.
 */
export function sinceOf(event: HistoryEvent): number | null {
	return typeof event.since === 'number' ? event.since : null;
}

/**
 * When a run happened: from its first act to its last, or `whenText` where it is one moment. The
 * rule (one date said once, both ends in full across the reader's midnight) is `span`'s.
 */
export function spanText(at: number | null, since: number | null, now?: number): string {
	if (at === null || since === null || since >= at) return whenText(at, now);
	return span(since, at, now);
}
