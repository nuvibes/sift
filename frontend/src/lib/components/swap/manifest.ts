// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * The Manifest: one list of what a swap carries, grouped the way Sift files things, written before
 * anything moves. Every step draws the same rows; only the state column changes.
 */

import { counted, filesSaid, SIZE_SEPARATOR } from '$lib/entity/entity-counts';
import { dimensions, length, size } from '$lib/library/facts';
import { sayDuration } from '$lib/shell/duration';
import { NOT_ENOUGH_TO_SAY, sayWindow } from '$lib/shell/when';
import type { IconName } from '$lib/design/icons';
import { pageOf } from '$lib/entity/related.svelte';
import { inFours } from './swap';
import type {
	GroupKind,
	GroupState,
	Learns,
	ManifestFile,
	ManifestGroup,
	RefusedFile
} from './wire';

/** The order Sift files things in. */
const GROUP_ORDER: readonly GroupKind[] = [
	'person',
	'site',
	'collection',
	'photo_set',
	'songs',
	'loose'
];

const KINDS: Record<GroupKind, { icon: IconName; one: string; many: string }> = {
	person: { icon: 'person', one: 'person', many: 'People' },
	site: { icon: 'public', one: 'Site', many: 'Sites' },
	collection: { icon: 'box', one: 'Collection', many: 'Collections' },
	photo_set: { icon: 'photo_library', one: 'Photo Set', many: 'Photo Sets' },
	songs: { icon: 'music_note_2', one: 'Songs', many: 'Songs' },
	loose: { icon: 'article', one: 'Other files', many: 'Other files' }
};

export function kindIcon(kind: GroupKind): IconName {
	return KINDS[kind].icon;
}

/** In filing order, the server's order kept within a kind. */
export function inFilingOrder(groups: readonly ManifestGroup[]): ManifestGroup[] {
	return GROUP_ORDER.flatMap((kind) => groups.filter((one) => one.kind === kind));
}

export function groupName(group: Pick<ManifestGroup, 'kind' | 'name'>): string {
	return group.name ?? KINDS[group.kind].one;
}

/** What the group is, under its name: the match here, the Site's Usernames, how many faces. */
export function groupContext(group: ManifestGroup): string {
	const parts: string[] = [];
	if (group.kind === 'person') {
		if (group.match_name) {
			const by =
				group.matched_by === 'alias'
					? ', by another name'
					: group.matched_by === 'box'
						? ', by stash-box'
						: '';
			parts.push(`Matches your ${group.match_name}${by}`);
		} else if (group.have !== null) {
			parts.push('New to your library');
		}
	} else if (group.kind === 'site') {
		parts.push('Site');
		if (group.usernames > 0)
			parts.push(counted(group.usernames) + plural(group.usernames, ' Username', ' Usernames'));
	} else if (group.kind === 'collection' || group.kind === 'photo_set') {
		parts.push(KINDS[group.kind].one);
	}
	if (group.faces > 0) parts.push(counted(group.faces) + plural(group.faces, ' face', ' faces'));
	return parts.join(SIZE_SEPARATOR);
}

function plural(count: number, one: string, many: string): string {
	return count === 1 ? one : many;
}

export function filesAndBytes(files: number, bytes: number): string {
	const weight = size(bytes);
	return weight ? `${filesSaid(files)}${SIZE_SEPARATOR}${weight}` : filesSaid(files);
}

/** Why a row will not go, in the drawer's words for the two marks. */
export function leftOutWords(group: ManifestGroup): string {
	const mark = group.left_out === 'local' ? 'Kept local' : "Don't swap";
	return group.files > 0 ? `${mark}${SIZE_SEPARATOR}${filesSaid(group.files)} not offered` : mark;
}

/** "You have 203", "409 to take": what is here already, and what taking it would bring. */
export function haveWords(group: ManifestGroup): string | null {
	if (group.have === null) return null;
	return `You have ${counted(group.have)}${SIZE_SEPARATOR}${counted(group.to_take)} to take`;
}

/** The after number: what this library holds of the group now, and once it is taken. */
export function afterNumber(group: ManifestGroup, taking: boolean): string | null {
	if (group.have === null) return null;
	const after = taking ? group.have + group.to_take : group.have;
	return `${counted(group.have)} \u2192 ${counted(after)}`;
}

/** The state a row shows: the server's fact, or this side's tick before Take is pressed. */
export function stateOf(group: ManifestGroup, ticked: boolean | null): GroupState {
	if (group.left_out) return 'left_out';
	if (ticked === null) return group.state;
	if (group.to_take === 0 && group.files > 0) return 'held';
	return ticked ? 'taking' : 'skipped';
}

type Pill = 'queued' | 'running' | 'done' | 'canceled' | 'failed';

/** The state as a badge: its colour and its word. */
export function pillOf(state: GroupState, group: ManifestGroup): { tone: Pill; word: string } {
	switch (state) {
		case 'offered':
			return { tone: 'queued', word: 'Offered' };
		case 'taking':
			return { tone: 'queued', word: 'Taking' };
		case 'waiting':
			return { tone: 'queued', word: 'Waiting' };
		case 'crossing':
			return {
				tone: 'running',
				word: `${counted(group.done)} of ${counted(group.to_take || group.files)}`
			};
		case 'landed':
			return { tone: 'done', word: 'Landed' };
		case 'sent':
			return { tone: 'done', word: 'Sent' };
		case 'skipped':
			return { tone: 'canceled', word: 'Skipped' };
		case 'held':
			return { tone: 'canceled', word: 'Already here' };
		case 'left_out':
			return { tone: 'failed', word: group.left_out === 'local' ? 'Kept local' : "Don't swap" };
	}
}

/** One file's facts: its kind, its size, and how long it runs or its width and height. */
export function fileFacts(file: ManifestFile): string {
	const shape = length(file.duration_ms) ?? dimensions(file.width, file.height);
	return [file.kind, size(file.size), shape].filter(Boolean).join(SIZE_SEPARATOR);
}

export function heldFileWords(held: ManifestFile['held']): string | null {
	if (held === 'same') return 'You have this one';
	if (held === 'look') return 'You have one that looks the same';
	return null;
}

/** The held row, its two reasons apart. */
export function heldWords(same: number, look: number): string | null {
	const total = same + look;
	if (total === 0) return null;
	const parts = [
		same > 0 ? `${counted(same)} the very same ${plural(same, 'file', 'files')}` : '',
		look > 0 ? `${counted(look)} that ${plural(look, 'looks', 'look')} the same` : ''
	].filter(Boolean);
	return `You already have ${counted(total)} of these, so they aren't sent: ${parts.join(' and ')}.`;
}

/** How many of each kind go: "3 People, 1 Site, 1 Collection". */
export function kindsWords(groups: readonly ManifestGroup[]): string {
	const going = groups.filter((one) => !one.left_out);
	return GROUP_ORDER.filter((kind) => kind !== 'songs' && kind !== 'loose')
		.map((kind) => {
			const n = going.filter((one) => one.kind === kind).length;
			if (n === 0) return '';
			return `${counted(n)} ${n === 1 ? KINDS[kind].one : KINDS[kind].many}`;
		})
		.filter(Boolean)
		.join(', ');
}

/** Parts of one line, with the middle dot between them. */
export function joined(...parts: (string | null | undefined)[]): string {
	return parts.filter(Boolean).join(SIZE_SEPARATOR);
}

/** A swap of one thing, said as a sentence instead of a list of one row. */
export function oneThing(groups: readonly ManifestGroup[]): ManifestGroup | null {
	const going = groups.filter((one) => !one.left_out);
	return groups.length === 1 && going.length === 1 ? going[0] : null;
}

/** How long it takes at the tunnel's last rate: a window, or nothing to say. */
function timeAt(bytes: number, rateBps: number | null | undefined): string | null {
	if (!rateBps || rateBps <= 0 || bytes <= 0) return null;
	const seconds = (bytes * 8) / rateBps;
	const said = sayWindow(seconds, seconds);
	return said === NOT_ENOUGH_TO_SAY ? null : said;
}

/** The time a swap of this size takes through this tunnel, at the rate it last measured. */
export function throughWords(
	bytes: number,
	rateBps: number | null | undefined,
	tunnel: string | null
): string | null {
	const time = timeAt(bytes, rateBps);
	if (!time) return null;
	return tunnel ? `${time} through ${tunnel}` : time;
}

/** What the other side learns from an offer, said once at Start. */
export const LEARNS_SENTENCE =
	"They learn these names, aliases, Sites and Usernames, and how many faces each person has. They never learn a file's path, your ratings, your notes or your History.";

/** The counts behind it, when the weigh has them. */
export function learnsCounts(learns: Learns | null | undefined): string | null {
	if (!learns) return null;
	const parts = [
		learns.people > 0 ? `${counted(learns.people)} ${plural(learns.people, 'name', 'names')}` : '',
		learns.aliases > 0
			? `${counted(learns.aliases)} ${plural(learns.aliases, 'alias', 'aliases')}`
			: '',
		learns.sites > 0 ? `${counted(learns.sites)} ${plural(learns.sites, 'Site', 'Sites')}` : '',
		learns.usernames > 0
			? `${counted(learns.usernames)} ${plural(learns.usernames, 'Username', 'Usernames')}`
			: '',
		learns.faces > 0 ? `${counted(learns.faces)} ${plural(learns.faces, 'face', 'faces')}` : ''
	].filter(Boolean);
	return parts.length > 0 ? parts.join(SIZE_SEPARATOR) : null;
}

/** What the duration words say for a moment: below it, a screen says its own sentence. */
const UNDER_A_MINUTE = sayDuration(0);

/** How long, in the app's one set of duration words; null for under a minute. */
function overAMinute(seconds: number): string | null {
	const said = sayDuration(seconds);
	return said === null || said === UNDER_A_MINUTE ? null : said;
}

/** How long it has been going: "9 min in". */
export function elapsedWords(since: number | null | undefined, now: number): string | null {
	if (!since) return null;
	const gone = overAMinute(now - since);
	return `${gone ?? UNDER_A_MINUTE} in`.replace(/^./, (first) => first.toUpperCase());
}

/** How long a token is still good for. */
export function goodForWords(expires: number | null | undefined, now: number): string | null {
	if (!expires) return null;
	if (expires <= now) return 'The token has run out.';
	const left = overAMinute(expires - now);
	return left ? `The token is good for another ${left}.` : 'The token is about to run out.';
}

/** How long a cut-off swap can still be joined again, counted down. */
export function rejoinWords(
	until: number | null | undefined,
	now: number,
	role: 'host' | 'guest' | string
): string | null {
	if (!until || until <= now) return null;
	const left = overAMinute(until - now);
	const time = left ? `another ${left}` : UNDER_A_MINUTE;
	return role === 'host' ? `They can rejoin for ${time}.` : `Sift keeps trying for ${time}.`;
}

export function swappedBeforeWords(count: number | null | undefined): string | null {
	if (count === null || count === undefined) return null;
	if (count === 0) return "You haven't swapped with this device before.";
	if (count === 1) return "You've swapped with this device once before.";
	return `You've swapped with this device ${counted(count)} times before.`;
}

/** The other device, by its name where there is one, and its id grouped as it is read aloud. */
export function deviceWords(
	name: string | null | undefined,
	id: string | null | undefined
): string | null {
	if (!id) return name ?? null;
	return name ? `${name} (${inFours(id)})` : inFours(id);
}

/** The guest dialling: whom, and through which tunnel. */
export function reachingWords(device: string | null, tunnel: string | null | undefined): string {
	const whom = device ?? 'them';
	return tunnel ? `Reaching ${whom} through ${tunnel}` : `Reaching ${whom}`;
}

/** What a transfer is moving at: the rate, and over how many streams. */
export function rateWords(
	rateBps: number | null | undefined,
	streams: number | null | undefined
): string | null {
	if (!rateBps || rateBps <= 0) return null;
	const perSecond = `${size(rateBps / 8)}/s`;
	return streams && streams > 0
		? `${perSecond} on ${counted(streams)} ${plural(streams, 'stream', 'streams')}`
		: perSecond;
}

export function refusedWords(reason: RefusedFile['reason']): string {
	if (reason === 'landing') return "It couldn't be added to your library.";
	if (reason === 'strip') return "Its hidden details couldn't be removed, so it wasn't let in.";
	return "Its bytes didn't match what was offered.";
}

/** The guest's next try, counted down in whole seconds. */
export function retryWords(seconds: number): string {
	return seconds > 0 ? `Trying again after ${counted(seconds)} s` : 'Trying again';
}

/** The countdown to a moment, in whole seconds: "Trying again after 15 s". */
export function secondsTo(at: number | null | undefined, now: number): number | null {
	if (!at) return null;
	return Math.max(0, Math.ceil(at - now));
}

/** What a landed group opens: its person or Site here, once it is filed. */
export function landedHref(group: ManifestGroup): string | null {
	if (!group.local_id) return null;
	if (group.kind === 'person' || group.kind === 'site') return pageOf(group.kind, group.local_id);
	return null;
}
