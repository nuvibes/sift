// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * A swap with another Sift, as its screens ask the server and read the answers.
 *
 * Every state is the server's. A session is read from one route and nothing here decides what it
 * is: which step the screen is on is worked out from that read plus the one press this browser
 * alone knows about: whether the person in front of it has compared the code yet. That press is
 * deliberately not stored anywhere else: a guest who reloads is shown the code again, which is the
 * safe way for it to be lost.
 */

import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { filesSaid } from '$lib/entity/entity-counts';
import { size } from '$lib/library/facts';
import { NOT_ENOUGH_TO_SAY, exactly, sayWindow } from '$lib/shell/when';
import { COPY as SITES } from '$lib/settings-ui/Sites.search';

/*
 * One direction of a swap that sends and receives, from this side: offered, wanted, moved (sent or
 * received), and its own pace while it moves.
 */
export type SwapDirection = components['schemas']['SwapDirection'];

/* A session. A swap that sends and receives says so (`two_way`), whether this side has answered
   their offer (`answered`), and each direction; a swap one way leaves both directions null. */
export type SwapSession = components['schemas']['SwapSession'];
export type SwapStarted = components['schemas']['SwapStarted'];
export type SwapJoined = components['schemas']['SwapJoined'];
export type SwapTunnel = components['schemas']['SwapTunnel'];
export type SwapDevice = components['schemas']['SwapDevice'];
export type OfferScreen = components['schemas']['OfferScreen'];
export type PersonRow = components['schemas']['PersonRow'];
export type HeldFile = components['schemas']['HeldFile'];
export type Chosen = components['schemas']['Chosen'];

// The three POST paths below are written out in full rather than built from `at`: the
// reachability gate reads a route's path from the source, and a template it cannot follow
// reads as a route nothing calls.
const at = (id: string) => `/swap/sessions/${encodeURIComponent(id)}` as const;

/*
 * Start a swap. With `receiveInto`, an exchange: they offer too, and what is taken from them lands
 * in that folder.
 */
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

/** The setting that remembers the tunnel this library joins swaps through (the server's own key). */
export const GUEST_TUNNEL_KEY = 'swap.guest_tunnel';

/** Join through the tunnel chosen beside Join: never the downloads' default route. */
export function joinSwap(token: string, folderId: string, tunnelId: string): Promise<SwapJoined> {
	return api.post<SwapJoined>('/swap/join', {
		body: { token: tokenFrom(token), dest_folder_id: folderId, tunnel_id: tunnelId }
	});
}

/** How many files and how many bytes: what picks would offer, or what an answer would bring. */
export type SwapWeight = components['schemas']['SwapWeight'];

/*
 * What the picks would offer, before Start: the server's own read of them, as whoever pressed, with
 * Hidden shut, so the figure is what the offer will hold.
 */
export function weighPicks(chosen: Chosen[]): Promise<SwapWeight> {
	return api.post<SwapWeight>('/swap/weigh', { body: { chosen } });
}

/** What taking these would bring: the people skipped and any file unticked, weighed by the
 *  server's own rule for an answer, before Take these is pressed. */
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

/*
 * They match / They don't match. The guest of a swap that sends and receives says with its They
 * match what it sends (`offering`), which releases its offer as the host's They match releases
 * the host's.
 */
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

/** Take these: the people skipped, by their place in the offer, and any file unticked. */
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

/** Where a file, a person, a Site or a tag stands with swaps ("Do not swap"). */
export type SwapRefusal = components['schemas']['SwapRefusal'];

/** The kinds "Do not swap" can be put on: the five "Do not enrich" covers. */
export type RefusalSubject = 'asset' | 'person' | 'site' | 'tag' | 'folder';

/** Keep this out of every swap, or let it back in. */
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

/** How many face descriptions swaps brought for somebody already here, waiting to be added. */
export function heldFaces(personId: string): Promise<HeldFaces> {
	return api.get<HeldFaces>(`/swap/people/${encodeURIComponent(personId)}/held-faces`);
}

/** Add them to that person: what the offer on their page does when it is taken. */
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

/*
 * A pasted token as ONE string.
 *
 * It arrives however the other person sent it: wrapped across two lines by a chat window, with a
 * space where a hyphen was, with a trailing newline. The server reads it without spaces and
 * without hyphens, so every run of white space is taken out here, which also keeps a token that
 * was wrapped many times inside the length the route accepts.
 */
export function tokenFrom(pasted: string): string {
	return pasted.replace(/\s+/g, '');
}

/** The words for "Can host" on a tunnel; the last two are the tunnels pane's own. */
export function canHostWords(canHost: boolean | null | undefined): string {
	if (canHost === true) return 'Can host';
	if (canHost === false) return SITES.tunnels.hosting.cannot;
	return SITES.tunnels.hosting.untried;
}

/*
 * How long is left, from the session's OWN measured rate.
 *
 * The rate is what this session moved over its last ten seconds, in bits a second; the bytes left
 * are what was wanted less what has moved. Until the first ten seconds have been measured there is
 * no rate, and a guess from some other transfer would be a number with nothing behind it.
 *
 * What it says then is every estimate's word for the same state, `NOT_ENOUGH_TO_SAY`, reached by
 * handing `sayWindow` no bounds rather than by a sentence of this screen's own: Activity, the
 * sheets and the download row say "Not enough to say yet" while they are measuring, and a swap
 * saying something else for that one state would read as a different kind of wait.
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

/*
 * One estimate for a swap that sends and receives: the two directions move at once, each at its
 * own pace up its own side's upload, so the whole takes as long as the slower of them. A direction
 * moving with no pace yet is not enough to say; one not moving yet is waiting for an answer, and
 * the estimate is of what is moving.
 */
export function timeLeftBothWays(session: Pick<SwapSession, 'sending' | 'receiving'>): string {
	const moving = [session.sending, session.receiving].filter((one): one is SwapDirection =>
		Boolean(one?.moving)
	);
	if (moving.length === 0) return NOT_ENOUGH_TO_SAY;
	const lefts = moving.map((one) => secondsLeft(one.rate_bps, one.wanted_bytes, one.bytes));
	if (lefts.some((one) => one === null)) return NOT_ENOUGH_TO_SAY;
	return leftWords(Math.max(...(lefts as number[])));
}

/*
 * Which step a session's screen is on.
 *
 * `compared` is the one fact the server does not hold: whether the person here has pressed They
 * match. THE CODE COMES BEFORE THE OFFER on both sides. The host's press is what releases the offer
 * at all; the guest's offer can arrive before the guest has compared anything (the host pressed
 * first), and it is held back behind the code until they have: nobody chooses what to take from
 * a device they have not checked is the one they meant.
 */
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
			// Both ways, files can be moving one way while this side still has the other's offer
			// to answer. Moving at all means this side compared: its own offer went, or it took.
			return session.two_way && answering(session, took) ? 'offer' : 'progress';
		case 'cut_off':
			return 'cut-off';
		case 'waiting':
			return session.role === 'host' && session.token ? 'token' : 'connecting';
	}
	if (!session.code) return 'connecting';
	if (!compared) return 'code';
	// Both ways, each side answers the other's offer and then watches both directions, the one not
	// moving yet saying what it waits for (`SwapProgress`).
	if (session.two_way) return answering(session, took) ? 'offer' : 'progress';
	if (session.role === 'guest' && session.state === 'offered' && session.offer) {
		return took ? 'starting' : 'offer';
	}
	return 'waiting-for-them';
}

/** Whether this side has the other's offer in front of it, not yet answered. */
function answering(session: SwapSession, took: boolean): boolean {
	return Boolean(session.offer) && !session.answered && !took;
}

/** Whether the session is still running, so the screen keeps asking after it. */
export function isLive(session: SwapSession | null): boolean {
	return session !== null && !['done', 'ended', 'failed'].includes(session.state);
}

/*
 * What a session a tunnel cut off says: not ended, and how long it can be joined again. The host
 * waits for them to join again with the same token; the guest keeps dialling, and a Join with the
 * same token dials at once.
 */
export function cutOffWords(
	session: Pick<SwapSession, 'role' | 'rejoin_until'>,
	at: (when: number) => string = exactly
): string {
	const until = session.rejoin_until ? ` until ${at(session.rejoin_until)}` : '';
	return session.role === 'host'
		? `The connection to them was lost. They can rejoin with the same token${until}.`
		: `The connection to them was lost. Sift keeps trying to reach them${until}. Join again with the same token to try now.`;
}

/*
 * What an ended session says, in the reason's own words.
 *
 * The reasons are the session's (`done`, `ended by you`, `ended by them`, `lost`, `refused`,
 * `wrong device`, `expired`, `used`). A reason this does not know is said as it came, rather than
 * dressed as one of these.
 */
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

/*
 * What the receiver has, in the screen's two words: "4,293 of 5,000 files received, 3,100 filed".
 *
 * A file is received when its last piece is in and the whole file checks, and filed once the
 * landing has put it in the library, one file at a time after that: the sender counts the first,
 * so a receiver counting only the second would look slower than the sender for the length of the
 * queue. `received` is the session's own count while it runs (`received_files`), and null once it
 * has ended or from a Sift that does not say it: then the filed count is what there is.
 */
export function receivedWords(
	received: number | null | undefined,
	filed: number,
	wanted: number
): string {
	const of = `of ${wanted.toLocaleString()} files received`;
	if (received === null || received === undefined) return `${filed.toLocaleString()} ${of}`;
	return `${received.toLocaleString()} ${of}, ${filed.toLocaleString()} filed`;
}

/* "3 files sent and 1 received": a swap that sends and receives, from this side. */
function sentAndReceived(sent: number, received: number): string {
	const files = sent === 1 ? '1 file' : `${sent.toLocaleString()} files`;
	return `${files} sent and ${received.toLocaleString()} received`;
}

/*
 * An exchange (a swap that sends and receives), ended: what went each way, in the reason's words.
 * The reasons that send nothing say so as a swap one way does.
 */
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

/*
 * Where a swap's files land, said before they do: everything under one Swap folder in the chosen
 * folder, split by the People and Sites they were offered under. With the session's short id once
 * there is a session (`Swap-7K3QM2RD`), and without it on the join form, before there is one.
 */
export function landingWords(
	folder: string,
	shortId?: string,
	noun: 'swap' | 'exchange' = 'swap'
): string {
	// The folder's real name once there is one; before, the kind of swap the form is for.
	const parent = shortId ? `Swap-${shortId}` : `a new folder for this ${noun}`;
	return `Files land under ${parent} in ${folder}, in folders named for their people and Sites.`;
}

/* A pick that wears a mark on its own row, and how many of its files the mark keeps back: the
   weigh's answer (`SwapWeight.left_out`). */
export type LeftOut = components['schemas']['LeftOut'];

/*
 * What the picks leave out and why, one sentence a line, by name where one thing explains it:
 * "Ava Example is kept local: 1,200 files aren't offered." Then the files a mark on something they
 * are filed under keeps back. A single file is "it isn't offered", never "1 file". The two marks,
 * Kept local and Don't swap, said in a sentence: kept local, and kept out of swaps.
 */
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

/*
 * Why Start is off, or null where it is on, from the server's own weigh of the picks.
 *
 * The server starts a swap from any picks, even ones that offer no file, so the rule is this
 * screen's, and it follows what the server lets each kind of swap be:
 *   - a swap that only sends, whose picks offer no file, has nothing to send: Start is off, and
 *     the reason stands where the figure would;
 *   - an exchange that offers no file still receives what they send, which is a swap the server
 *     runs (their offer, its answer, their files): Start stays on;
 *   - facial fingerprints go with no file, so picks that hold them are never "nothing";
 *   - before the weigh has answered there is no figure to judge by, and Start stays as it was.
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

/*
 * The figure above Start: "You would send 38 files, 6 GB." Where the picks offer no file, what
 * that means for this kind of swap instead of "0 files, 0 B": an exchange still receives, a swap of
 * facial fingerprints sends them, and a swap that only sends has nothing to start (`startBlocked`).
 */
export function sendingWords(weight: SwapWeight, chosen: readonly Chosen[], both: boolean): string {
	if (weight.files > 0) return `You would send ${filesAndSize(weight.files, weight.bytes)}.`;
	const blocked = startBlocked(weight, chosen, both);
	if (blocked) return blocked;
	if (chosen.some((one) => one.kind === 'facial_fingerprints')) {
		return 'You would send facial fingerprints and no files.';
	}
	return 'You would send no files, and still receive what they send.';
}

/** "38 files, 6 GB": a count and what it adds up to. */
export function filesAndSize(files: number, bytes: number): string {
	const counted = files === 1 ? '1 file' : `${files.toLocaleString()} files`;
	const weight = size(bytes);
	return weight ? `${counted}, ${weight}` : counted;
}

/*
 * A device id as it is shown: in fours.
 *
 * The server already sends it grouped (`ABCD-EFGH-...`); anything else is grouped here so a
 * copied id and a read-aloud one agree.
 */
export function inFours(id: string): string {
	const bare = id.replace(/[^A-Za-z0-9]/g, '').toUpperCase();
	return (bare.match(/.{1,4}/g) ?? []).join('-');
}
