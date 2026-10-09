// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * A swap with another Sift, as its screens ask and read. Every state is the server's; whether the
 * code was compared is never stored, so a guest who reloads is shown it again.
 */

import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { filesSaid } from '$lib/entity/entity-counts';
import { size } from '$lib/library/facts';
import { NOT_ENOUGH_TO_SAY, exactly, sayWindow } from '$lib/shell/when';
import { COPY as SITES } from '$lib/settings-ui/Sites.search';

export type SwapDirection = components['schemas']['SwapDirection'];

/* `two_way` swaps say whether this side answered; a one-way swap leaves both directions null. */
export type SwapSession = components['schemas']['SwapSession'];
export type SwapStarted = components['schemas']['SwapStarted'];
export type SwapJoined = components['schemas']['SwapJoined'];
export type SwapTunnel = components['schemas']['SwapTunnel'];
export type SwapDevice = components['schemas']['SwapDevice'];
export type OfferScreen = components['schemas']['OfferScreen'];
export type PersonRow = components['schemas']['PersonRow'];
export type HeldFile = components['schemas']['HeldFile'];
export type Chosen = components['schemas']['Chosen'];

// The POST paths are written in full: the reachability gate cannot follow a template.
const at = (id: string) => `/swap/sessions/${encodeURIComponent(id)}` as const;

/** With `receiveInto`, an exchange; what is taken lands there. */
export function startSwap(
	chosen: Chosen[],
	tunnelId: string,
	shareBoxes: boolean,
	receiveInto: string | null = null
): Promise<SwapStarted> {
	const both = receiveInto ? { two_way: true, dest_folder_id: receiveInto } : {};
	return api.post<SwapStarted>('/swap/start', {
		body: { chosen, tunnel_id: tunnelId, share_boxes: shareBoxes, ...both }
	});
}

export const GUEST_TUNNEL_KEY = 'swap.guest_tunnel';

export function joinSwap(token: string, folderId: string, tunnelId: string): Promise<SwapJoined> {
	return api.post<SwapJoined>('/swap/join', {
		body: { token: tokenFrom(token), dest_folder_id: folderId, tunnel_id: tunnelId }
	});
}

export type SwapWeight = components['schemas']['SwapWeight'];

/** The server's read of the picks, as whoever pressed, with Hidden shut. */
export function weighPicks(chosen: Chosen[]): Promise<SwapWeight> {
	return api.post<SwapWeight>('/swap/weigh', { body: { chosen } });
}

export function weighAnswer(
	id: string,
	skipped: readonly number[],
	unticked: readonly string[] = []
): Promise<SwapWeight> {
	return api.post<SwapWeight>(`/swap/sessions/${encodeURIComponent(id)}/weigh`, {
		body: { skipped: [...skipped], unticked: [...unticked] }
	});
}

export function readSession(id: string): Promise<SwapSession> {
	return api.get<SwapSession>(at(id));
}

/* A two-way guest's They match says what it sends (`offering`). */
export function answerCode(
	id: string,
	match: boolean,
	offering?: { chosen: Chosen[]; shareBoxes: boolean }
): Promise<SwapSession> {
	const sends = offering ? { chosen: offering.chosen, share_boxes: offering.shareBoxes } : {};
	return api.post<SwapSession>(`/swap/sessions/${encodeURIComponent(id)}/code`, {
		body: { match, ...sends }
	});
}

export function takeOffer(
	id: string,
	skipped: readonly number[],
	unticked: readonly string[] = []
): Promise<SwapSession> {
	return api.post<SwapSession>(`/swap/sessions/${encodeURIComponent(id)}/take`, {
		body: { skipped: [...skipped], unticked: [...unticked] }
	});
}

export function endSwap(id: string): Promise<SwapSession> {
	return api.post<SwapSession>(`/swap/sessions/${encodeURIComponent(id)}/end`);
}

export type SwapRefusal = components['schemas']['SwapRefusal'];

export type RefusalSubject = 'asset' | 'person' | 'site' | 'tag' | 'folder';

/** Where one thing stands with swaps, without changing it: what a menu row reads first. */
export function keptFromSwaps(subject: RefusalSubject, id: string): Promise<SwapRefusal> {
	return api.get<SwapRefusal>(
		`/swap/keep-out/${encodeURIComponent(subject)}/${encodeURIComponent(id)}`
	);
}

export function setKeptFromSwaps(
	subject: RefusalSubject,
	id: string,
	keptOut: boolean
): Promise<SwapRefusal> {
	return api.put<SwapRefusal>(
		`/swap/keep-out/${encodeURIComponent(subject)}/${encodeURIComponent(id)}`,
		{ body: { kept_out: keptOut } }
	);
}

export type HeldFaces = components['schemas']['HeldFaces'];

export function heldFaces(personId: string): Promise<HeldFaces> {
	return api.get<HeldFaces>(`/swap/people/${encodeURIComponent(personId)}/held-faces`);
}

export function addHeldFaces(personId: string): Promise<HeldFaces> {
	return api.post<HeldFaces>(`/swap/people/${encodeURIComponent(personId)}/held-faces`);
}

export function swapTunnels(): Promise<SwapTunnel[]> {
	return api.get<SwapTunnel[]>('/swap/tunnels');
}

export function swapDevice(): Promise<SwapDevice> {
	return api.get<SwapDevice>('/swap/device');
}

export function resetDevice(): Promise<SwapDevice> {
	return api.post<SwapDevice>('/swap/device/reset');
}

/** A pasted token as ONE string: every run of white space removed. */
export function tokenFrom(pasted: string): string {
	return pasted.replace(/\s+/g, '');
}

export function canHostWords(canHost: boolean | null | undefined): string {
	if (canHost === true) return 'Can host';
	if (canHost === false) return SITES.tunnels.hosting.cannot;
	return SITES.tunnels.hosting.untried;
}

/**
 * From the session's own measured rate, else `NOT_ENOUGH_TO_SAY` through `sayWindow`, as every
 * estimate.
 */
export function timeLeft(
	session: Pick<SwapSession, 'rate_bps' | 'wanted_bytes' | 'sent_bytes'>
): string {
	return leftWords(secondsLeft(session.rate_bps, session.wanted_bytes, session.sent_bytes));
}

function secondsLeft(
	rate: number | null | undefined,
	wanted: number | null | undefined,
	moved: number
): number | null {
	const measured = (rate ?? 0) > 0 && wanted !== null && wanted !== undefined;
	return measured ? (Math.max(0, (wanted ?? 0) - moved) * 8) / (rate ?? 1) : null;
}

function leftWords(seconds: number | null): string {
	const said = sayWindow(seconds, seconds);
	if (said === NOT_ENOUGH_TO_SAY) return said;
	return `${said.charAt(0).toUpperCase()}${said.slice(1)} left`;
}

/** Both ways: as long as the slower direction moving. */
export function timeLeftBothWays(session: Pick<SwapSession, 'sending' | 'receiving'>): string {
	const moving = [session.sending, session.receiving].filter((one): one is SwapDirection =>
		Boolean(one?.moving)
	);
	if (moving.length === 0) return NOT_ENOUGH_TO_SAY;
	const lefts = moving.map((one) => secondsLeft(one.rate_bps, one.wanted_bytes, one.bytes));
	if (lefts.some((one) => one === null)) return NOT_ENOUGH_TO_SAY;
	return leftWords(Math.max(...(lefts as number[])));
}

/* `compared` is the one fact the server lacks. THE CODE COMES BEFORE THE OFFER on both sides. */
export type Stage =
	| 'token'
	| 'connecting'
	| 'code'
	| 'waiting-for-them'
	| 'offer'
	| 'starting'
	| 'progress'
	| 'cut-off'
	| 'ended';

export function stageOf(session: SwapSession, compared: boolean, took = false): Stage {
	switch (session.state) {
		case 'done':
		case 'ended':
		case 'failed':
			return 'ended';
		case 'transferring':
			return session.two_way && answering(session, took) ? 'offer' : 'progress';
		case 'cut_off':
			return 'cut-off';
		case 'waiting':
			return session.role === 'host' && session.token ? 'token' : 'connecting';
	}
	if (!session.code) return 'connecting';
	if (!compared) return 'code';
	if (session.two_way) return answering(session, took) ? 'offer' : 'progress';
	if (session.role === 'guest' && session.state === 'offered' && session.offer) {
		return took ? 'starting' : 'offer';
	}
	return 'waiting-for-them';
}

function answering(session: SwapSession, took: boolean): boolean {
	return Boolean(session.offer) && !session.answered && !took;
}

export function isLive(session: SwapSession | null): boolean {
	return session !== null && !['done', 'ended', 'failed'].includes(session.state);
}

/** A tunnel cut off: not ended, and how long it can be joined again. */
export function cutOffWords(
	session: Pick<SwapSession, 'role' | 'rejoin_until'>,
	at: (when: number) => string = exactly
): string {
	const until = session.rejoin_until ? ` until ${at(session.rejoin_until)}` : '';
	return session.role === 'host'
		? `The connection to them was lost. They can rejoin with the same token${until}.`
		: `The connection to them was lost. Sift keeps trying to reach them${until}. Join again with the same token to try now.`;
}

/** In the reason's own words; an unknown reason is said as it came. */
export function endedWords(session: SwapSession): string {
	if (session.end_reason === 'older') {
		return "Their Sift can't send files back, so nothing was sent. Ask them to update Sift, or start a swap that only sends.";
	}
	if (session.two_way) return endedBothWays(session);
	const moved = session.sent_files;
	const verb = session.role === 'host' ? 'sent' : 'received';
	const files = moved === 1 ? '1 file' : `${moved.toLocaleString()} files`;
	switch (session.end_reason) {
		case 'done':
			return moved === 0
				? 'The swap is finished, and nothing needed to be sent.'
				: `The swap is finished: ${files} ${verb}.`;
		case 'ended by you':
			return `You ended the swap. ${files} ${verb}.`;
		case 'ended by them':
			return `They ended the swap. ${files} ${verb}.`;
		case 'lost':
			return `The connection to them was lost. ${files} ${verb}.`;
		case 'refused':
			return "The codes didn't match, so nothing was sent.";
		case 'wrong device':
			return 'A different device answered the token, so nothing was sent.';
		case 'expired':
			return 'The token ran out before anyone joined, so nothing was sent. Start a new swap to make another.';
		case 'used':
			return 'That token has been used already, so nothing was sent. Ask them to start a new swap.';
		default:
			return session.end_reason ? `The swap ended: ${session.end_reason}.` : 'The swap ended.';
	}
}

/** Received counts a whole checked file, filed counts one landed; `received` is null once ended. */
export function receivedWords(
	received: number | null | undefined,
	filed: number,
	wanted: number
): string {
	const of = `of ${wanted.toLocaleString()} files received`;
	if (received === null || received === undefined) return `${filed.toLocaleString()} ${of}`;
	return `${received.toLocaleString()} ${of}, ${filed.toLocaleString()} filed`;
}

function sentAndReceived(sent: number, received: number): string {
	const files = sent === 1 ? '1 file' : `${sent.toLocaleString()} files`;
	return `${files} sent and ${received.toLocaleString()} received`;
}

function endedBothWays(session: SwapSession): string {
	const both = sentAndReceived(session.sending?.files ?? 0, session.receiving?.files ?? 0);
	switch (session.end_reason) {
		case 'done':
			return `The exchange is finished: ${both}.`;
		case 'ended by you':
			return `You ended the exchange. ${both}.`;
		case 'ended by them':
			return `They ended the exchange. ${both}.`;
		case 'lost':
			return `The connection to them was lost. ${both}.`;
		default:
			return endedWords({ ...session, two_way: false });
	}
}

/**
 * One Swap folder in the chosen folder, by People and Sites; with the session's short id once there
 * is one.
 */
export function landingWords(
	folder: string,
	shortId?: string,
	noun: 'swap' | 'exchange' = 'swap'
): string {
	const parent = shortId ? `Swap-${shortId}` : `a new folder for this ${noun}`;
	return `Files land under ${parent} in ${folder}, in folders named for their people and Sites.`;
}

export type LeftOut = components['schemas']['LeftOut'];

/** One sentence a line, by name where one thing explains it; one file is "it", never "1 file". */
export function leftOutWords(named: readonly LeftOut[], other: number): string[] {
	const lines = named.map((one) => {
		const mark = one.mark === 'local' ? 'is kept local' : 'is kept out of swaps';
		if (one.kind === 'asset') return `${one.name} ${mark}, so it isn't offered.`;
		return `${one.name} ${mark}: ${filesSaid(one.files)} ${one.files === 1 ? "isn't" : "aren't"} offered.`;
	});
	if (other === 1) {
		const lead = named.length > 0 ? 'One more file' : 'One file';
		lines.push(
			`${lead} isn't offered, because it or something it's filed under is kept local or kept out of swaps.`
		);
	} else if (other > 1) {
		const lead = named.length > 0 ? `Another ${filesSaid(other)}` : filesSaid(other);
		lines.push(
			`${lead} aren't offered, because they or something they're filed under is kept local or kept out of swaps.`
		);
	}
	return lines;
}

/**
 * Why Start is off, the screen's own rule: a send-only swap of no files has nothing to send; an
 * exchange still receives; fingerprints go without files; before the weigh, as it was.
 */
export function startBlocked(
	weight: Pick<SwapWeight, 'files'> | null,
	chosen: readonly Chosen[],
	both: boolean
): string | null {
	if (weight === null || weight.files > 0 || both) return null;
	if (chosen.some((one) => one.kind === 'facial_fingerprints')) return null;
	return "None of what you picked can be sent, so there's nothing to start.";
}

/** The figure above Start, or what a swap of no files means for this kind. */
export function sendingWords(weight: SwapWeight, chosen: readonly Chosen[], both: boolean): string {
	if (weight.files > 0) return `You would send ${filesAndSize(weight.files, weight.bytes)}.`;
	const blocked = startBlocked(weight, chosen, both);
	if (blocked) return blocked;
	if (chosen.some((one) => one.kind === 'facial_fingerprints')) {
		return 'You would send facial fingerprints and no files.';
	}
	return 'You would send no files, and still receive what they send.';
}

export function filesAndSize(files: number, bytes: number): string {
	const counted = files === 1 ? '1 file' : `${files.toLocaleString()} files`;
	const weight = size(bytes);
	return weight ? `${counted}, ${weight}` : counted;
}

/** Grouped here too, so a copied id and a read-aloud one agree. */
export function inFours(id: string): string {
	const bare = id.replace(/[^A-Za-z0-9]/g, '').toUpperCase();
	return (bare.match(/.{1,4}/g) ?? []).join('-');
}
