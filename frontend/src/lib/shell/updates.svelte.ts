/* What the server knows about newer versions, as the Updates screen asks for it.
 *
 * THE SERVER NEVER APPLIES AN UPDATE, and there is no endpoint here that would: a server that
 * answers on the network does not get to replace its own program.
 *
 * The desktop application updates ITSELF, on a click, having proved the release is Sift's and newer
 * than the copy running (see `bridge.applyUpdate`). It still installs nothing silently; the
 * installer opens and the person agrees to it.
 *
 * A failed check is not an error to show. Sift works with no outbound network at all, so "could not
 * reach the release page" is an ordinary state and the screen simply says nothing is known.
 */

import { api } from '$lib/api/client';
import { bridge } from '$lib/bridge';
import { isNewer } from '$lib/shell/version-order';
import { settingChanges } from '$lib/library/changes.svelte';
import { sayAgo } from '$lib/shell/when';

export interface UpdateState {
	current_version: string;
	latest_version: string | null;
	update_available: boolean;
	/** The release notes, as published: Markdown, drawn through `notesBlocks` and never as HTML. */
	notes: string;
	/** The release's own page, https only, or empty. The one address the notes may link to. */
	release_page: string;
	/** Unix time of the last attempt, or 0 if none has happened. */
	last_checked: number;
	dismissed: boolean;
}

export class Updates {
	state = $state<UpdateState | null>(null);
	loaded = $state(false);
	/* A guest never sees this screen, and a signed-out browser has bigger problems. Anything else
	   that goes wrong is the check failing, which is not worth a message: the screen shows what it
	   knows, which is nothing. */
	unavailable = $state(false);
	/** What version THIS copy of the application is where it is not the library's own (client mode);
	 *  null in a browser and on the computer running Sift (`bridge.shellVersion`). */
	here = $state<string | null>(null);

	async load(): Promise<void> {
		void this.readHere();
		try {
			this.state = await api.get<UpdateState>('/update/check');
			this.loaded = true;
			this.unavailable = false;
		} catch {
			this.unavailable = true;
		}
	}

	async readHere(): Promise<void> {
		this.here = await bridge.shellVersion();
	}

	/** Whether THIS copy is behind the newest release, which the server's own verdict never says. */
	get behindHere(): boolean {
		const latest = this.state?.latest_version;
		return this.here !== null && !!latest && isNewer(latest, this.here);
	}

	/** Whether a newer Sift waits for the library's computer or for this copy. */
	get waiting(): boolean {
		return this.state?.update_available === true || this.behindHere;
	}

	/** Whether to put the banner in front of somebody. */
	get shouldNotify(): boolean {
		return this.state !== null && this.waiting && !this.state.dismissed;
	}

	/** Stop the banner for the version currently offered. The next release brings it back. */
	async dismiss(): Promise<void> {
		const version = this.state?.latest_version;
		if (!version) return;
		try {
			await api.post('/update/dismiss', { body: { version } });
			this.state = { ...this.state!, dismissed: true };
		} catch {
			// Dismissing is a convenience. If it did not save, the notice comes back, which is the
			// safe direction for it to fail in.
		}
	}
}

/** The one shared instance, so the banner and the settings section agree about what is available. */
export const updates = new Updates();

/* A release the server's check found, or one dismissed in another window, is said on the settings
   bell. The answer is the session's (the banner and `Settings > Updates` both draw it), so one
   lasting listener; a session that never asked asks nothing. */
settingChanges.subscribe(() => {
	if (updates.loaded) void updates.load();
});

/**
 * When the last check happened, in words. Empty when none has.
 *
 * The ladder of thresholds is `sayWhen`, because the Scheduled tasks screen needs the same one read
 * forwards ("in 3 hours" as well as "3 hours ago"), and two copies of one ladder disagree the
 * first time either is tuned. What stays here is what is this screen's alone: zero means no check
 * has ever happened, and that is a blank rather than a moment.
 *
 * Clamped to now: a check whose recorded moment is a second in the future is a clock that moved,
 * not a check that has not happened yet.
 */
export function describeLastChecked(at: number, now: number = Date.now()): string {
	if (at === 0) return '';
	const seconds = now / 1000;
	/* A check is past: a recorded moment a second in the future is a clock that moved, not a check
	   that has not happened yet: `sayAgo`, the one rule for every past moment. */
	return sayAgo(at, seconds);
}

/* --- The release notes ------------------------------------------------------------------------
 *
 * Published as Markdown and drawn as a small, fixed subset of it: headings, paragraphs, lists, bold
 * and code. The result is a tree of plain strings the screen puts into text nodes, so nothing in a
 * release body can become markup: an HTML tag in the notes is shown as the characters it is. A
 * link is drawn as a link only when it points at the release's own page; any other is its words.
 */

/** A run of text inside a block. */
export type NotesInline =
	{ kind: 'text' | 'strong' | 'code'; text: string } | { kind: 'link'; text: string; href: string };

/** One block of the notes, in the order they are drawn. */
type NotesBlock =
	| { kind: 'heading'; level: 1 | 2 | 3; inline: NotesInline[] }
	| { kind: 'paragraph'; inline: NotesInline[] }
	| { kind: 'list'; ordered: boolean; items: NotesInline[][] }
	| { kind: 'code'; text: string };

const BULLET = /^\s{0,3}(?:[-*+]|(\d{1,9})[.)])\s+(.*)$/;
const HEADING = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const FENCE = /^\s{0,3}(```|~~~)/;
const RULE = /^\s{0,3}(?:[-*_]\s*){3,}$/;

/** Read release notes into blocks. `page` is the one address a link may keep. */
export function notesBlocks(markdown: string, page: string): NotesBlock[] {
	const blocks: NotesBlock[] = [];
	const lines = markdown.replace(/\r\n?/g, '\n').split('\n');
	let paragraph: string[] = [];
	let list: { ordered: boolean; items: string[] } | null = null;

	const flush = () => {
		if (paragraph.length > 0) {
			blocks.push({ kind: 'paragraph', inline: inlineRuns(paragraph.join(' '), page) });
			paragraph = [];
		}
		if (list !== null) {
			const { ordered, items } = list;
			blocks.push({ kind: 'list', ordered, items: items.map((item) => inlineRuns(item, page)) });
			list = null;
		}
	};

	for (let at = 0; at < lines.length; at += 1) {
		const line = lines[at] as string;
		const fence = FENCE.exec(line);
		if (fence !== null) {
			flush();
			const code: string[] = [];
			for (
				at += 1;
				at < lines.length && !(lines[at] as string).trimStart().startsWith(fence[1] as string);
				at += 1
			) {
				code.push(lines[at] as string);
			}
			blocks.push({ kind: 'code', text: code.join('\n') });
			continue;
		}
		if (line.trim() === '' || RULE.test(line)) {
			flush();
			continue;
		}
		const heading = HEADING.exec(line);
		if (heading !== null) {
			flush();
			const level = Math.min((heading[1] as string).length, 3) as 1 | 2 | 3;
			blocks.push({ kind: 'heading', level, inline: inlineRuns(heading[2] as string, page) });
			continue;
		}
		const bullet = BULLET.exec(line);
		if (bullet !== null) {
			const ordered = bullet[1] !== undefined;
			if (paragraph.length > 0 || (list !== null && list.ordered !== ordered)) flush();
			list ??= { ordered, items: [] };
			list.items.push(bullet[2] as string);
			continue;
		}
		if (list !== null && /^\s/.test(line)) {
			/* An indented line carries the item above it on. */
			const last = list.items.length - 1;
			list.items[last] = `${list.items[last]} ${line.trim()}`;
			continue;
		}
		if (list !== null) flush();
		paragraph.push(line.trim());
	}
	flush();
	return blocks;
}

/* A link written the Markdown way. Read over a bounded window, so text full of brackets costs at most
   that much per bracket rather than the whole remaining text. */
const LINK = /^!?\[([^\]\n]*)\]\(\s*<?([^)\s>]*)>?(?:\s+"[^"\n]*")?\s*\)/;
const LINK_WINDOW = 2048;

/**
 * The runs of one line of text: bold, code, a link to the release page, and plain text.
 *
 * One pass. A marker with no closer is remembered as having none, so text full of lone `**` costs
 * one search in all rather than one per marker.
 */
export function inlineRuns(text: string, page: string): NotesInline[] {
	const runs: NotesInline[] = [];
	let plain = '';
	const keep = (run: NotesInline) => {
		if (plain !== '') runs.push({ kind: 'text', text: plain });
		plain = '';
		runs.push(run);
	};
	const unclosed = new Set<string>();
	const closing = (marker: string, from: number): number => {
		if (unclosed.has(marker)) return -1;
		const found = text.indexOf(marker, from);
		if (found === -1) unclosed.add(marker);
		return found;
	};

	let at = 0;
	while (at < text.length) {
		const ch = text[at] as string;
		if (ch === '`') {
			let ticks = 1;
			while (text[at + ticks] === '`') ticks += 1;
			const marker = '`'.repeat(ticks);
			const end = closing(marker, at + ticks);
			if (end > at + ticks) {
				keep({ kind: 'code', text: text.slice(at + ticks, end).trim() });
				at = end + ticks;
			} else {
				plain += marker;
				at += ticks;
			}
			continue;
		}
		if ((ch === '*' || ch === '_') && text[at + 1] === ch) {
			const marker = ch + ch;
			const end = closing(marker, at + 2);
			if (end > at + 2 && (text[at + 2] as string).trim() !== '') {
				keep({ kind: 'strong', text: text.slice(at + 2, end) });
				at = end + 2;
			} else {
				plain += marker;
				at += 2;
			}
			continue;
		}
		if (ch === '[' || (ch === '!' && text[at + 1] === '[')) {
			const link = LINK.exec(text.slice(at, at + LINK_WINDOW));
			if (link !== null) {
				const words = link[1] as string;
				const href = link[2] as string;
				if (ch !== '!' && page !== '' && href === page)
					keep({ kind: 'link', text: words || href, href });
				else plain += words;
				at += link[0].length;
				continue;
			}
		}
		plain += ch;
		at += 1;
	}
	if (plain !== '') runs.push({ kind: 'text', text: plain });
	return runs;
}
