<script lang="ts" module>
	import type { Column } from '$lib/components/common/DataRows.svelte';

	/*
	 * THE QUEUE'S COLUMNS, declared once for the list and obeyed by every row (`DataRows`).
	 *
	 * Left to right: the tick, the Site's mark, the name, then the three facts a download manager
	 * reads down a column (its state, its size, its moment), then the one thing to do about it,
	 * standing against the row's actions. Every track is a length, so what a row holds never moves
	 * a column. Below `FACTS_UNDER_THE_NAME` the three facts and the fix go on a line under the
	 * name instead.
	 */
	const PICK: Column = { id: 'pick', width: '20px' };
	const MARK: Column = { id: 'mark', width: '28px' };
	const NAME: Column = { id: 'name', width: 'minmax(0, 1fr)' };
	/* The three every arrangement opens with. */
	const LEAD: readonly Column[] = [PICK, MARK, NAME];

	/*
	 * Where the detail under a row starts: the name's grid line, so the opened card stands under
	 * the words it is about and runs to the row's end. Placed by the row, because a cell in a
	 * subgrid takes one track unless it says otherwise, and the pick track is 20px wide.
	 */
	export const DETAIL_FROM = LEAD.indexOf(NAME) + 1;

	/** The columns the list declares, wide or narrow. */
	export function downloadColumns(narrow: boolean): readonly Column[] {
		if (narrow) return LEAD;
		return [
			...LEAD,
			{ id: 'status', width: '9rem' },
			{ id: 'size', width: '8rem', align: 'end' },
			{ id: 'when', width: '7.5rem', align: 'end' },
			{ id: 'fix', width: '10.5rem', align: 'end' }
		];
	}

	/** Two hover glyphs, the three dots and the arrow: their track where there is no hover. */
	export const DOWNLOAD_ACTIONS =
		'calc(2 * var(--control-height) + 2 * var(--control-height-sm) + 3 * var(--space-1))';

	/*
	 * The actions track on a phone: the three dots and the arrow.
	 *
	 * The two glyphs are the same verbs the three dots list, and on a phone there is no hover to
	 * reveal them with, so they would stand there on every row taking the width the name needs.
	 * Without them the name keeps about half the row on a phone instead of a third.
	 */
	export const PHONE_ACTIONS = 'calc(2 * var(--control-height-sm) + var(--space-1))';
</script>

<script lang="ts">
	import { counted } from '$lib/entity/entity-counts';
	/* One download, as a row of a download manager.
	 *
	 * Read left to right it is: is this one picked, which Site it came from, WHAT IT IS, and how
	 * far along. The name comes first because the name is what somebody is looking for: a row
	 * that led with the Site would read as a list of sites rather than a list of downloads.
	 *
	 * The state, the size and the moment are three columns the list declares, each the one place
	 * its fact is drawn, in tabular figures so a column of them does not jitter as they tick.
	 *
	 * The bar has a floor. A four hundred kilobyte image finishes in under a second, and a bar
	 * that flashes for one frame reads as noise rather than as information, so it is drawn only
	 * for something big enough or slow enough to be worth watching. A running download with no
	 * numbers at all gets the sweep instead: nothing has moved yet, and a bar at zero would say
	 * it had.
	 *
	 * And it is deliberately NOT a live region. `role="progressbar"` is not announced as it
	 * changes, so the instinct is to wrap it in one, which then announces every percent of
	 * every row and is unusable. Announcing is done once, above the whole list, at milestones.
	 *
	 * ## The verbs are declared once
	 *
	 * Every act this row offers is in `verbs` below, and the three surfaces (the glyph buttons
	 * on hover, the right-click menu and the three dots) all read that one list, so the buttons
	 * cannot offer something the menu does not. The one exception is the PRIMARY text button,
	 * which is the same verb drawn a second way rather than a second verb: it is picked out of
	 * the list by id.
	 */
	import { exactly, sayWhen, sayWindow } from '$lib/shell/when';
	import {
		AssetLink,
		Avatar,
		Badge,
		Button,
		Checkbox,
		ConfirmDialog,
		DataRow
	} from '$lib/components/common';
	import { linkMarks } from '$lib/entity/entity-picture';
	import { markMissing, markOf } from './marks.svelte';
	import type { BadgeState } from '$lib/components/common/Badge.svelte';
	import type { Verb } from '$lib/components/common/verbs';
	import Icon from '$lib/components/Icon.svelte';
	import type { IconName } from '$lib/design/icons';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import ProgressBar from '$lib/components/common/ProgressBar.svelte';
	import DownloadDetail, { detailOffers } from './DownloadDetail.svelte';
	import { copyText } from '$lib/shell/clipboard';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { goto } from '$app/navigation';
	import { canCancel, canPause, canResume, type DownloadItem } from './queue.svelte';

	interface Props {
		item: DownloadItem;
		now: number;
		/** Whether this row is ticked. */
		selected?: boolean;
		/**
		 * Ticked or unticked.
		 *
		 * The event is handed on as a third argument so a list can answer Shift-click the way every
		 * wall in Sift already does (Shift-click picks a range) rather than this row
		 * inventing a second meaning for the gesture. A caller that declares two parameters is
		 * unaffected, which is why it is an addition rather than a change.
		 */
		onselect?: (id: string, on: boolean, event?: MouseEvent) => void;
		/**
		 * Whether ANYTHING in the list is ticked.
		 *
		 * The tick is revealed by hover or focus, and once a selection exists every row's tick stays
		 * out. Otherwise the boxes a person is working through vanish as the pointer leaves them.
		 * A row cannot know this about the list, so the list says.
		 */
		anySelected?: boolean;
		/** Whether the detail under this row is open. Held by the list, so only one need be. */
		expanded?: boolean;
		onexpand?: (id: string) => void;
		/** Open the Cookies sheet on this Site. The page owns the sheet. */
		oncookies?: (site: string) => void;
		oncancel?: (id: string) => void;
		/** Hold it where it is, keeping what has been fetched. */
		onpause?: (id: string) => void;
		/** Let it go again, from where it stopped. */
		onresume?: (id: string) => void;
		onretry?: (id: string) => void;
		onfirst?: (id: string) => void;
		onanyway?: (id: string) => void;
		/** Take the row off the list. Never the file: this page does not delete anything. */
		onremove?: (id: string) => void;
		/** Show the file it produced, over this page. See `DownloadDetail` for the rule. */
		onopen?: (assetId: string) => void;
		/**
		 * Whether the window is under `FACTS_UNDER_THE_NAME`. The facts and the fix then go under
		 * the name rather than beside it, and the list declares the narrow columns to match: a
		 * decision about the arrangement the page makes once and hands down, never a width this
		 * row keeps a copy of.
		 */
		narrow?: boolean;
		/** Whether the window is a phone's (`NARROW_DOWNLOADS`): the row's glyphs are left to the
		 *  three dots, which list the same verbs. See `PHONE_ACTIONS`. */
		phone?: boolean;
	}

	let {
		item,
		now,
		selected = false,
		onselect,
		anySelected = false,
		expanded = false,
		onexpand,
		oncookies,
		oncancel,
		onpause,
		onresume,
		onretry,
		onfirst,
		onanyway,
		onremove,
		onopen,
		narrow = false,
		phone = false
	}: Props = $props();

	/* The failure sentence, chosen from `error_code` on the server so every screen says one
	 * thing. `item.error` is the fallback.
	 */
	const sentence = $derived(item.sentence ?? item.error ?? null);

	// The status the ledger and its job settle on, said the one way the whole app says it. Skipped is
	// grey and unalarming (the link was already in the library, nothing went wrong), and blocked is
	// amber and asks for cookies rather than reporting a failure.
	/* What the badge is told: the state, and the two things a screen may know better than the state
	   does. Named rather than written inline on the function, because the switch below returns it in
	   nine places and a widened field would have to be added to all nine. */
	interface Mark {
		state: BadgeState;
		label?: string;
		icon?: IconName;
		iconFilled?: boolean;
	}

	const badge = $derived(badgeOf(item.status));

	function badgeOf(status: string): Mark {
		switch (status) {
			/* A download is only ever blocked on COOKIES, so it says so and draws the waiting
			   glyph rather than the general stop a job would. The colour is still blocked's.

			   "Waiting for cookies", not "Waiting for login": Sift never signs in to anything
			   and holds no account (it is handed an export of a browser's cookies), so
			   "login" names a thing this surface does not have. */
			case 'blocked':
				return {
					state: 'blocked',
					label: 'Waiting for cookies',
					icon: 'chronic',
					iconFilled: true
				};
			/* Neither of these is really a cancellation, which is why each carries its own mark over
			   the grey that state gives it: one is a step past, and one is a repeat. */
			case 'skipped':
				return { state: 'canceled', label: 'Skipped', icon: 'skip_next' };
			case 'duplicate':
				return {
					state: 'canceled',
					label: 'Already in library',
					icon: 'toll',
					iconFilled: true
				};
			// Amber, not red. The download worked and the file was quarantined on the way in, so this
			// is not something to try again: the same bytes would arrive and be quarantined again.
			//
			// The WORD is `Badge`'s, which is why this case sits with the ones that pass straight
			// through. An override is for a screen that knows more than the state does, and this
			// screen knows nothing about the quarantine that the jobs list does not, so a word of
			// its own could only ever be the same fact spelled differently, which is how two screens
			// end up naming one thing two ways.
			/* Nothing is overridden for a held row: "Paused" is the word the Jobs list says for the
			   same state, and a second wording of it here would be the drift `Badge` exists to stop. */
			case 'paused':
			case 'quarantined':
			case 'canceled':
			case 'running':
			case 'queued':
			case 'done':
			case 'failed':
				return { state: status as BadgeState };
			default:
				return { state: 'queued' };
		}
	}

	/*
	 * What to call where this came from, best answer first.
	 *
	 * The ledger's own site is only written when a download SUCCEEDS, so a queued, running or
	 * failed row would have no name at all, while the address has said which site it was since
	 * the moment it was pasted. The server names the ones Sift recognizes.
	 *
	 * A site Sift has no record of still has a DOMAIN, and that is the answer for it: "Link" says
	 * nothing about a failed row, which is exactly the row somebody is looking at. It is only
	 * reached by a row with no readable address at all.
	 */
	const where = $derived(item.site ?? item.site_name ?? domainOf(item.url) ?? 'Link');

	/**
	 * The host, for an address the server could not name at all.
	 *
	 * Deliberately dumb, and deliberately last. The server names every address it can parse (from
	 * the catalog where there is a record, and from the domain where there is not), and that is the
	 * same name the tunnel routing lists the site under, so the two screens cannot come to call one
	 * site two things. A cleverer rule here would be a second copy of that one, disagreeing with it
	 * the first time either was edited.
	 */
	function domainOf(address: string | null | undefined): string | null {
		if (!address) return null;
		try {
			return new URL(address).hostname.replace(/^www\./, '') || null;
		} catch {
			return null;
		}
	}
	const whose = $derived(item.username ? `@${item.username}` : '');

	/*
	 * A download whose file has since been deleted.
	 *
	 * The row stays (it is history, and history of a file that is gone is still history), but
	 * nothing on it may pretend to lead anywhere: the site, the account and the name are struck
	 * through and are plain text, and no verb offers to open it, because there is no longer a file
	 * at the end of any of them. The ledger remembers what it produced, so the name survives.
	 */
	const settled = $derived(['done', 'duplicate'].includes(item.status));
	const gone = $derived(settled && !item.asset_id && Boolean(item.remembered_filename));

	/* What the address SAYS, which is not always what it points at.
	 *
	 * Some sites hand out a signed link: the file, plus an expiry and a signature regenerated
	 * every time somebody loads the page it is on. Eighty characters of hex that says nothing, and
	 * pushes the filename off the end of the row. The server works out the short form and sends it
	 * beside the real one; the link still points at the real one, because the host answers 404 to
	 * the short one. */
	const shownUrl = $derived(item.shown_url ?? item.url ?? '');

	/*
	 * THE NAME, and it is the first thing on the row.
	 *
	 * Best answer first: what the file is called, then what a gallery is going to become, then the
	 * address it was pasted from. A row that leads with its Site is a list of sites; the Site is on
	 * the second line, where it belongs beside the handle.
	 *
	 * "N files" rather than "N pictures", and that is a limit of the wire rather than a choice: a
	 * gallery reports files and nothing on `DownloadProgress` says what KIND they are, so calling
	 * them pictures would be the row guessing about a mixed album.
	 */
	const named = $derived.by(() => {
		const name = item.filename ?? item.remembered_filename;
		if (name) return name;
		const many = item.progress?.total_files ?? 0;
		if (many > 1) return `${counted(many)} files`;
		return shownUrl;
	});

	/*
	 * Cut in the MIDDLE, so the extension survives.
	 *
	 * CSS can only cut at an end. `text-overflow: ellipsis` takes the tail, which is exactly the
	 * part that says what kind of file this is, and a row of forty names all ending in the same
	 * cut is a column nobody can tell apart. The RTL trick that fakes a leading ellipsis reverses
	 * punctuation as well, so a name with a bracket in it comes out backwards.
	 *
	 * So the cut is made here, in text, and the whole name is in the detail under the row for
	 * anybody who needs all of it.
	 */
	const NAME_CEILING = 46;
	const NAME_TAIL = 14;

	function shorten(name: string): string {
		if (name.length <= NAME_CEILING) return name;
		return `${name.slice(0, NAME_CEILING - NAME_TAIL - 1)}\u2026${name.slice(-NAME_TAIL)}`;
	}

	const shownName = $derived(shorten(named));

	// Anything under ten seconds of work does not want a bar; anything under a megabyte will not be
	// on screen long enough to have one. Both floors, because either one alone lets through the case
	// the other catches: a huge file on a fast connection, and a tiny one on a slow one.
	const worthABar = $derived(
		item.progress != null &&
			((item.progress.total_bytes ?? 0) > 1_000_000 ||
				(item.progress.seconds_left ?? 0) > 10 ||
				(item.progress.total_files ?? 0) > 1)
	);

	const running = $derived(item.status === 'running');
	/** Held by somebody, with its bytes still on disk. Not moving, and not over either. */
	const held = $derived(item.status === 'paused');

	/* A bar only while it is running: a finished row's progress is its status, and a bar left under
	   one is a stale number that reads as authoritative. The sweep is for the silence before the
	   first byte (the page is being fetched, a challenge answered, the formats listed), where a
	   bar at zero would claim the transfer had started and stalled. */
	const barValue = $derived(
		item.progress?.total_bytes ? (item.progress.done_bytes / item.progress.total_bytes) * 100 : null
	);

	/* A held row keeps its bar and only its bar: no sweep, because nothing is moving, and nothing at
	   all where there is no figure to hold: an empty grey track would be a claim that nought was
	   fetched rather than that the size is unknown. */
	const showBar = $derived(
		(running && (worthABar || item.progress == null)) || (held && barValue !== null)
	);

	function size(bytes: number): string {
		if (bytes >= 1_000_000_000) return `${(bytes / 1_000_000_000).toFixed(1)} GB`;
		if (bytes >= 1_000_000) return `${(bytes / 1_000_000).toFixed(1)} MB`;
		if (bytes >= 1000) return `${Math.round(bytes / 1000)} KB`;
		return `${bytes} B`;
	}

	/** A size nobody is sure of, with the false precision taken off. */
	function roughly(bytes: number): string {
		if (bytes >= 1_000_000_000) return `${(bytes / 1_000_000_000).toFixed(1)} GB`;
		if (bytes >= 1_000_000) return `${Math.round(bytes / 1_000_000)} MB`;
		return size(bytes);
	}

	/** The one estimate formatter's window, from the transfer's own rate. */
	function howLong(seconds: number): string {
		return `${sayWindow(seconds, seconds)} left`;
	}

	/** How much, in words. A bare percentage tells nobody how much is left in real terms. */
	const amount = $derived.by(() => {
		const going = item.progress;
		if (!going) return '';
		if (going.total_files && going.total_files > 1) {
			return `${going.done_files} of ${counted(going.total_files)} files`;
		}
		if (going.total_bytes) {
			/* A video served in fragments has no declared size. The tool works one out from the
			   bitrate it has seen so far and revises it as it goes, so the "final size" moves
			   every few seconds, which reads as a fault. The number is honest; printing it as
			   though the site had stated it was not. Rounded to whole megabytes as well, because a
			   tenth of a megabyte on a guess is precision nobody has. */
			const total = going.total_is_estimated
				? `about ${roughly(going.total_bytes)}`
				: size(going.total_bytes);
			return `${size(going.done_bytes)} of ${total}`;
		}
		return size(going.done_bytes);
	});

	/**
	 * WHERE IN THE QUEUE a waiting row is, in the words somebody would use for it.
	 *
	 * The server counts the position. One ahead is named rather than counted: "next in line" is
	 * what a person would say, and "1 ahead" is the same fact in the machine's voice. Nothing at
	 * all for a row with no position, which is what an older server sends and what a row that is
	 * not waiting has.
	 */
	const place = $derived.by(() => {
		const at = item.position ?? null;
		if (at === null || at < 1) return '';
		return at === 1 ? 'next in line' : `${at - 1} ahead`;
	});

	/*
	 * THE SIZE COLUMN: how much has come while it runs, how much a pause KEPT (the whole question a
	 * pause raises, so "kept" rather than "done"), and how big it was once it has landed.
	 */
	const sizeSaid = $derived.by(() => {
		if (running) return amount;
		if (held) return amount ? `${amount} kept` : '';
		if (settled && item.size_bytes) return size(item.size_bytes);
		return '';
	});

	/*
	 * THE MOMENT COLUMN, and each state says the thing it leaves unanswered: a running row how
	 * long is left (or how fast, before it can say), a waiting one where in the queue, everything
	 * else when.
	 */
	const when = $derived.by(() => {
		const going = item.progress;
		if (running && going) {
			if (going.seconds_left) return howLong(going.seconds_left);
			return going.bytes_per_second ? `${size(going.bytes_per_second)}/s` : '';
		}
		if (item.status === 'queued') return place;
		if (held) return sayWhen(item.created_at, now);
		return sayWhen(item.finished_at ?? item.created_at, now);
	});

	/*
	 * THE MOMENT THAT COLUMN SAYS, for its hover: "10 minutes ago" is a live list's answer, and the
	 * exact moment is one gesture away on every other screen that says one (`$lib/shell/when`). Null where
	 * it says a speed or a place in the queue, read off the same branches as `when`.
	 */
	const whenAt = $derived.by((): number | null => {
		if (running && item.progress) return null;
		if (item.status === 'queued') return null;
		if (held) return item.created_at;
		return item.finished_at ?? item.created_at;
	});

	/*
	 * An album that landed without some of its files (out of the size settings, or gone from the
	 * Site), said on the second line from the counts the row stored when it finished. Nothing for a
	 * row that lost none.
	 */
	const leftOut = $derived.by(() => {
		const offered = item.files_offered ?? 0;
		const lost = item.files_left_out ?? 0;
		if (!settled || lost < 1 || offered < lost) return '';
		return `${counted(offered - lost)} of ${counted(offered)} files, ${counted(lost)} left out`;
	});

	/* The way it went out, on the second line: a tunnel by name, since which one is the part worth
	   reading. Recorded as it went, so a route changed later does not rewrite it. */
	const via = $derived.by(() => {
		if (!item.via) return null;
		if (item.via.toLowerCase() === 'direct') return null;
		return `through ${item.via}`;
	});

	/* The whole of the failure, small, under the sentence in the detail. Built from what the wire
	   actually carries (the code and the tier) rather than from an HTTP status nobody sends. */
	const rawFailure = $derived.by(() => {
		const parts: string[] = [];
		if (item.error_code) parts.push(item.error_code);
		const host = domainOf(item.url);
		if (host) parts.push(`from ${host}`);
		if (item.error_tier != null) parts.push(`tier ${item.error_tier}`);
		return parts.length > 0 ? parts.join(' \u2014 ') : null;
	});

	const failing = $derived(item.status === 'failed' || item.status === 'blocked');

	/* Stopped only while on its way, by the store's rule (`canCancel`), which the selection bar's
	   Cancel reads too, so the row and the bar cannot disagree. */
	const stoppable = $derived(canCancel(item));
	/* Off the list, never off the disk: this page does not delete files. Only for a row that has
	   stopped. Taking a running download off the list would leave it running with nothing on
	   screen saying so. */
	const removable = $derived(
		['done', 'failed', 'canceled', 'skipped', 'duplicate'].includes(item.status)
	);
	/* Open it, when there is something to open. A row whose file has since been deleted keeps the
	   name and offers nothing that leads anywhere. */
	const openable = $derived(Boolean(item.asset_id) && !gone);

	/** The file the name opens, or nothing: a row with no file, or whose file is gone, is text. */
	const door = $derived(openable && onopen ? item.asset_id : null);

	/* The pictures that may stand for the Site, best first (`linkMarks`); a host the pack lacks is
	   asked once a page (`markOf`), and with none `Avatar` draws the Site's letter. */
	const marks = $derived(item.url ? linkMarks(item.url, item.site) : []);
	const mark = $derived(markOf(marks[0]));

	let asking = $state(false);

	function copyLink() {
		void copyText(item.url ?? '').then((landed) => {
			if (landed) toasts.show('Copied', { tone: 'success' });
		});
	}

	/*
	 * Cancelling a download WITH BYTES ON DISK asks; cancelling one with none does not.
	 *
	 * The difference is whether anything is lost. A waiting download has fetched nothing, so
	 * stopping it costs a click to start again, and the page says so with a toast that undoes.
	 * A running one has bytes on disk that go with it, and so does a HELD one: pausing is the
	 * promise that what was fetched is kept, and cancelling is what breaks that promise. Asking on
	 * a running row and not on a paused one would make Cancel mean two things a press apart.
	 */
	const losesBytes = $derived(running || held);

	function cancel() {
		if (losesBytes) {
			asking = true;
			return;
		}
		oncancel?.(item.id);
	}

	const cancelConsequence = $derived(
		`${size(item.progress?.done_bytes ?? 0)} of ${named} is downloaded. ` +
			'Canceling drops it; Try again starts from nothing.'
	);

	/*
	 * EVERY act this row offers, declared once.
	 *
	 * The glyph buttons, the right-click menu and the three dots all read this, so none of them can
	 * offer something another does not (see the head of the file). A verb whose handler was not
	 * given is not built at all, which is how the page says "this list does not do that" rather
	 * than drawing a row that does nothing.
	 */
	const verbs = $derived.by<Verb[]>(() => {
		const built: Verb[] = [];

		// The Site the cookies are FOR is the one the address named: `site` is only filled in once
		// a download lands, and a row waiting for cookies never has one. Without this there would
		// be no Add cookies to press.
		if (item.status === 'blocked' && oncookies && (item.site_key ?? item.site)) {
			built.push({
				id: 'cookies',
				label: 'Add cookies',
				icon: 'cookie',
				group: 'change',
				run: () => oncookies?.(item.site_key ?? item.site ?? '')
			});
		}
		if (item.status === 'queued' && onfirst) {
			built.push({
				id: 'first',
				label: 'Move to the front',
				icon: 'arrow_upward',
				group: 'change',
				run: () => onfirst?.(item.id)
			});
		}
		/* Exactly one of these two can be built for any row: the states they answer to do not
		   overlap. Where they sit on the row is `ON_ROW`'s, below, not this list's. */
		if (canPause(item) && onpause) {
			built.push({
				id: 'pause',
				label: 'Pause',
				icon: 'pause',
				group: 'change',
				run: () => onpause?.(item.id)
			});
		}
		if (canResume(item) && onresume) {
			built.push({
				id: 'resume',
				label: 'Resume',
				icon: 'play_arrow',
				group: 'change',
				run: () => onresume?.(item.id)
			});
		}
		if (stoppable && oncancel) {
			built.push({ id: 'cancel', label: 'Cancel', icon: 'close', group: 'change', run: cancel });
		}
		if (['failed', 'canceled'].includes(item.status) && onretry) {
			built.push({
				id: 'retry',
				label: 'Try again',
				icon: 'sync',
				group: 'change',
				run: () => onretry?.(item.id)
			});
		}
		if (openable && onopen) {
			built.push({
				id: 'open',
				label: 'Open',
				icon: 'visibility',
				group: 'open',
				run: () => onopen?.(item.asset_id ?? '')
			});
		}
		if (item.status === 'quarantined') {
			/* Where the quarantined files are kept, which is the Organize board's own queue. The row
			   does not draw them itself: one screen holds that pile and this is a way into it. */
			built.push({
				id: 'quarantine',
				label: 'Open quarantine',
				icon: 'visibility',
				group: 'open',
				run: () => void goto('/organize/quarantine')
			});
		}
		if (['skipped', 'duplicate'].includes(item.status) && onanyway) {
			built.push({
				id: 'anyway',
				label: 'Download it anyway',
				icon: 'download',
				group: 'change',
				run: () => onanyway?.(item.id)
			});
		}
		if (item.url) {
			built.push({
				id: 'link',
				label: 'Copy link',
				icon: 'link',
				group: 'keep',
				singleOnly: true,
				run: copyLink
			});
		}
		if (removable && onremove) {
			/* Destructive in the menu's sense (it takes something away and there is no second copy
			   of a list), and deliberately not red-as-in-deletes-a-file: the words say "from the
			   list" because that is all it does. The page offers the undo. */
			built.push({
				id: 'remove',
				label: 'Remove from the list',
				icon: 'delete',
				destructive: true,
				run: () => onremove?.(item.id)
			});
		}
		return built;
	});

	/* The one or two a person reaches for, drawn as glyphs on hover, in ADDITION to the menu,
	   never instead of it, and they come off the same list. Two, because a row carrying five of
	   them is a row whose name has nowhere to go.

	   ORDER OF PREFERENCE, not the order the verbs were declared in: a live row has three
	   candidates and only two places. The pair a live row wears is hold-or-let-go on the left
	   and stop on the right, the same two in the same places whether it is running, waiting or
	   held, which is what makes them findable by shape rather than by reading. Move to the
	   front is what gives way; it is the rarer act, and the row says where in the queue it is in
	   words, which is what makes it worth pressing. */
	const ON_ROW = ['pause', 'resume', 'cancel', 'first', 'link'];
	const glyphs = $derived(
		ON_ROW.map((id) => verbs.find((verb) => verb.id === id))
			.filter((verb): verb is Verb => verb !== undefined)
			// Never the verb that already has words on it beside it: one act, one control.
			.filter((verb) => verb.id !== fix?.id)
			.slice(0, 2)
	);

	/*
	 * The one verb with words on it: the fix, where there is a fix.
	 *
	 * Picked out of the same list rather than written again, so it cannot say something the menu
	 * does not offer. One per row at most: two primary buttons is a row with no primary act.
	 */
	// Resume is the fix for a held row: the one act it is waiting on, in words, at rest, rather
	// than a paused row wearing nothing but its badge until the pointer finds it.
	/* Not Open: the NAME opens a finished download's file, so an Open button beside it would be
	   one act with two controls. Open stays in the menu and on right-click, where the keyboard
	   and the pointer both still find it by its word. */
	const FIX_ORDER = ['cookies', 'resume', 'retry', 'anyway', 'quarantine'];

	/* While the detail is open it says the failure and offers its fix, so the row does neither: it
	   keeps its facts, and the act is offered once. */
	const inDetail = $derived(
		expanded
			? detailOffers(item, sentence, {
					cookies: oncookies !== undefined,
					retry: onretry !== undefined,
					resume: onresume !== undefined
				})
			: new Set<string>()
	);

	const fix = $derived.by(() => {
		for (const id of FIX_ORDER) {
			/* A duplicate's file is already in the library and its name opens it, so fetching it a
			   second time is not the fix for it. That one is in the menu, as the design has it. */
			if (id === 'anyway' && item.status === 'duplicate') continue;
			const found = verbs.find((verb) => verb.id === id);
			if (found) return found;
		}
		return null;
	});

	/*
	 * Delete, while the keyboard is anywhere in the row: stop it, or take it off the list.
	 *
	 * Handed to `DataRow` as its `onkeys`, which hears a key from the row's own tab stop and from
	 * everything inside it: the tick, a glyph button, the Site link. On a child span it would
	 * hear only keys typed while that child was focused: Tab lands on the row first, and a key
	 * typed there never passes through a child of it.
	 *
	 * A key with Ctrl, Alt or the command key held is somebody else's (Ctrl+P is print), and is
	 * left alone rather than pausing a download and swallowing the shortcut.
	 */
	function typed(event: KeyboardEvent) {
		if (event.ctrlKey || event.metaKey || event.altKey) return;
		/* P holds it and lets it go again, which is one key because it is one question: the same
		   key a media player has meant for this since before any of us. It is the verb's own rule
		   that decides which way it goes, so a row that can do neither answers nothing at all
		   rather than a press that quietly does the wrong one. */
		if (event.key === 'p' || event.key === 'P') {
			if (canPause(item) && onpause) {
				event.preventDefault();
				onpause(item.id);
			} else if (canResume(item) && onresume) {
				event.preventDefault();
				onresume(item.id);
			}
			return;
		}
		if (event.key !== 'Delete') return;
		if (stoppable && oncancel) {
			event.preventDefault();
			cancel();
			return;
		}
		if (removable && onremove) {
			event.preventDefault();
			onremove(item.id);
		}
	}
</script>

<!-- THE CELLS, one per declared column (`downloadColumns`). The row positions none of them: it
     hands each to the list, which puts it in its track. -->
{#snippet cellPick()}
	<span class="tick" class:shown={selected || anySelected}>
		{#if onselect}
			<!-- Out of the way until it is wanted. A column of forty empty boxes is a form; the
			     tick arrives on hover or focus, and stays out for every row once anything at
			     all is picked, so the boxes somebody is working through do not vanish behind
			     the pointer. -->
			<Checkbox
				state={selected ? 'on' : 'off'}
				label={`Select ${named}`}
				onchange={(next, event) => onselect?.(item.id, next === 'on', event)}
			/>
		{/if}
	</span>
{/snippet}

{#snippet cellMark()}
	<!--
		The SITE's mark, always, never the creator's.

		A creator's picture belongs to the person and is shown wherever a person is; this column
		answers a different question, which is where a download came from, and it has to answer
		it the same way on every row. A wall of different faces is not a column of sites.

		The pack's logo for the address's HOST first: the host is known the moment a link is
		pasted, and a row still downloading would otherwise have no mark. Then the picture a
		download fetched for the Site, then its letter. All from Sift itself: an image at a
		remote address tells that site who is browsing.
	-->
	<span class="mark">
		<Avatar
			src={mark}
			instead={marks[1] ?? null}
			name={where}
			mark
			bare
			onfailed={() => markMissing(mark)}
		/>
	</span>
{/snippet}

{#snippet cellName()}
	<span class="text">
		<span class="what">
			{#if door}
				<!-- The NAME is the door to the file: pressed, it opens over this list, the
				     way every name of a file does in Sift. A middle click still opens a
				     tab, because it is a real address. -->
				<span class="named door"><AssetLink id={door}>{shownName}</AssetLink></span>
			{:else}
				<span class="named" class:gone>{shownName}</span>
			{/if}
		</span>

		{#if showBar}
			<!--
				While it is running, the bar stays on the row.

				Everything else about a download is reference and lives behind the chevron, but a
				transfer in flight is the one thing somebody is watching rather than looking up, and
				a bar that has to be opened to be seen is not a bar.

				A total nobody knows is a sweep rather than a bar at zero: the two mean different
				things and a bar stuck at nothing reads as a download that is not moving.
			-->
			<ProgressBar
				value={barValue}
				paused={held}
				label={held
					? `${named}: paused, ${amount} kept`
					: `${named}: ${amount || 'waiting for the Site'}`}
			/>
		{/if}

		<span class="under">
			{#if failing && sentence && !expanded}
				<!-- The sentence in place of the facts, because on a row that failed the facts are
				     not the question. Plain-language and already redacted on the way into the
				     ledger: no path. -->
				<span class="why">{sentence}</span>
			{:else}
				{#if item.site_id && !gone}
					<!-- The Site, as a way in: its own page holds everything fetched from it. Only
					     once a download has been filed under it, which is when there is a page. -->
					<a class="where" href={`/sites/${item.site_id}`}>{where}</a>
				{:else}
					<span class="where" class:gone>{where}</span>
				{/if}
				{#if whose}
					{#if item.person_id && !gone}
						<!-- Whose it is, on their own page. A download files what it fetched under a
						     person, and this is that person rather than a search for their name. -->
						<a class="whose" href={`/people/${item.person_id}`}>{whose}</a>
					{:else}
						<span class="whose" class:gone>{whose}</span>
					{/if}
				{/if}
				{#if via}
					<span class="whose">{via}</span>
				{/if}
				{#if leftOut}
					<span class="whose left-out">{leftOut}</span>
				{/if}
			{/if}
		</span>
	</span>
{/snippet}

<!-- The badge exactly as `/design` draws it: the state's own ground, glyph and word. -->
{#snippet cellStatus()}
	<span class="status"><Badge {...badge} /></span>
{/snippet}

{#snippet cellSize()}
	{#if sizeSaid}<span class="size">{sizeSaid}</span>{/if}
{/snippet}

{#snippet cellWhen()}
	<span class="when">
		{#if when && whenAt !== null}
			<Tooltip label={exactly(whenAt)}><span class="second">{when}</span></Tooltip>
		{:else if when}
			<span class="second">{when}</span>
		{/if}
	</span>
{/snippet}

<!-- The one thing to do about this row, in words, against the row's actions, and it does NOT hide
     until hover: a row that needs something from a person has to say so while nobody is touching
     it. -->
{#snippet cellFix()}
	<span class="fix">
		{#if fix && !inDetail.has(fix.id)}
			<Button
				size="small"
				tone="secondary"
				icon={fix.icon}
				iconFilled={fix.filled ?? false}
				onclick={() => fix?.run?.([item.id])}
			>
				{fix.label}
			</Button>
		{/if}
	</span>
{/snippet}

<!-- On a narrow window the name's cell carries the rest: the name, then the three facts and the
     fix on one line under it, in the same order the wide columns read. -->
{#snippet cellNameNarrow()}
	<span class="stacked">
		{@render cellName()}
		<span class="facts">
			{@render cellStatus()}
			{@render cellSize()}
			{@render cellWhen()}
			{@render cellFix()}
		</span>
	</span>
{/snippet}

<!-- The one or two glyphs a person reaches for, revealed on hover in the row's actions, in front
     of the three dots. Every glyph button names itself, and the name is the verb's own word. -->
{#snippet glyphActions()}
	<span class="controls">
		{#each glyphs as verb (verb.id)}
			<Tooltip label={verb.label}>
				<Button tone="ghost" onclick={() => verb.run?.([item.id])} aria-label={verb.label}>
					<Icon name={verb.icon} size={16} />
				</Button>
			</Tooltip>
		{/each}
	</span>
{/snippet}

<DataRow
	{verbs}
	ids={[item.id]}
	menuLabel={`More for ${named}`}
	onpress={onexpand ? () => onexpand?.(item.id) : undefined}
	expanded={onexpand ? expanded : undefined}
	ontoggle={onexpand ? () => onexpand?.(item.id) : undefined}
	toggleLabel={named}
	onkeys={typed}
	cells={narrow
		? { pick: cellPick, mark: cellMark, name: cellNameNarrow }
		: {
				pick: cellPick,
				mark: cellMark,
				name: cellName,
				status: cellStatus,
				size: cellSize,
				when: cellWhen,
				fix: cellFix
			}}
	actions={glyphs.length > 0 && !phone ? glyphActions : undefined}
>
	{#snippet expansion()}
		{#if expanded}
			<div class="opened" style:grid-column={`${DETAIL_FROM} / -1`}>
				<DownloadDetail
					{item}
					{now}
					{sentence}
					raw={rawFailure}
					{oncookies}
					{onretry}
					{onresume}
					{onopen}
					{gone}
				/>
			</div>
		{/if}
	{/snippet}
</DataRow>

<!-- Only on a running row, and only built once one is asked for: a dialog per row in a queue of
     five hundred is five hundred dialogs in the document. -->
{#if asking}
	<ConfirmDialog
		bind:open={asking}
		title="Cancel this download?"
		consequence={cancelConsequence}
		confirmLabel="Cancel download"
		cancelLabel="Keep downloading"
		onconfirm={() => oncancel?.(item.id)}
	/>
{/if}

<style>
	/* The detail may be narrower than its longest word, so a long address wraps at the row's
	   width rather than widening the row. */
	.opened {
		min-inline-size: 0;
	}

	/* A download whose file is gone. Struck through and never a link: there is nothing at the end
	   of it any more, and a row that still offers to open one is lying about its own history. */
	.gone {
		text-decoration: line-through;
		color: var(--sift-ink-3);
	}

	/* Revealed rather than removed, so nothing on the row moves sideways when it appears. A box
	   that shifted the name every time the pointer crossed the list would be worse than one that
	   was simply always there. The row it answers to is `DataRow`'s. */
	.tick {
		display: inline-flex;
		opacity: 0;
		transition: opacity var(--dur-instant) var(--ease);
	}

	.tick.shown,
	:global(.row:hover) .tick,
	:global(.row:focus-within) .tick {
		opacity: 1;
	}

	/* No pointer to hover with. The box is simply here. */
	@media (hover: none) {
		.tick {
			opacity: 1;
		}
	}

	/* The square the Site's mark stands in. `Avatar` fills whatever holds it. The logo is drawn
	   BARE (no ground and no corner taken off it, the way a person's links draw theirs), so this
	   clips nothing; the corner is handed down for the letter's tile alone, drawn where the Site
	   has no picture. It fills its column (`MARK` declares the one width) and is as tall as wide. */
	.mark {
		display: block;
		inline-size: 100%;
		aspect-ratio: 1;
		border-radius: var(--radius-sm);
	}

	.text {
		display: flex;
		flex-direction: column;
		gap: 2px;
		min-inline-size: 0;
	}

	/* The headline: the NAME, one line, and it is what gives way when there is not room. It has
	   already been cut in the middle, so what is left of it still ends in the extension. */
	.what {
		display: flex;
		align-items: center;
		min-inline-size: 0;
		color: var(--sift-ink);
		font: var(--text-body);
	}

	.named {
		overflow: hidden;
		font-weight: 600;
		text-overflow: ellipsis;
	}

	/* The name as the door wears the row's own ink, underlined under the pointer the way every
	   other name on this row is. The accent on every finished row of a list would make the list a
	   page of links; this is a name that also opens. */
	.door :global(.asset-link) {
		color: inherit;
	}

	.fix {
		display: flex;
		justify-content: flex-end;
	}

	/* The glyphs, in front of the three dots. `DataRow` reveals its actions on hover and focus,
	   and leaves them there with no pointer to hover with. */
	.controls {
		display: flex;
		align-items: center;
		gap: var(--space-1);
	}

	/* The name and, under it on a narrow window, the three facts and the fix on one line. */
	.stacked {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		min-inline-size: 0;
	}

	.facts {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-1) var(--space-3);
	}

	/* Under the name's own line the name may take two: an address has no spaces, so it breaks where
	   it must, and only a third line is cut. One line would cut a link to its scheme. */
	.stacked .named {
		display: -webkit-box;
		-webkit-box-orient: vertical;
		-webkit-line-clamp: 2;
		line-clamp: 2;
		white-space: normal;
		overflow-wrap: anywhere;
	}

	/* The fix follows the facts it answers, on their line. Pushed to the far end it would stand
	   alone in the room the row's hidden actions keep, far from the words it is about. */
	.facts .fix {
		justify-content: flex-start;
	}

	/* The second line: where it came from, whose it is, which way it went, or, on a row that
	   failed, the sentence instead, because on that row the facts are not the question. */
	.under {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		min-inline-size: 0;
		overflow: hidden;
		color: var(--sift-ink-2);
		font: var(--text-body-sm);
		white-space: nowrap;
		text-overflow: ellipsis;
	}

	/* Both names are links, and neither may look like one drawn by the browser.
	 *
	 * `color: inherit` rather than the accent, because a global anchor rule would give them the
	 * default link colours (the site blue until opened and purple afterwards), making two rows
	 * for the same site look like two different things. They wear the row's own ink and say they
	 * are links by underlining under the pointer, which is what every other name in Sift does.
	 */
	.where {
		color: inherit;
		text-decoration: none;
		/* The ink steps over --dur-instant. The underline arrives at once, which is right: a
		   mark either identifies the word under the pointer or it does not. */
		transition: color var(--dur-instant) var(--ease);
	}

	a.where:hover {
		color: var(--sift-ink);
		text-decoration: underline;
	}

	a.where:focus-visible,
	a.whose:focus-visible {
		outline: none;
		border-radius: var(--radius-sm);
		box-shadow: var(--focus-ring);
	}

	.whose {
		color: var(--sift-ink-3);
		text-decoration: none;
		transition: color var(--dur-instant) var(--ease);
	}

	a.whose:hover {
		color: var(--sift-ink);
		text-decoration: underline;
	}

	.why {
		overflow: hidden;
		color: var(--sift-bad-text);
		text-overflow: ellipsis;
	}

	/* The state, the size and the moment: one fact per column, in the data face, cut rather than
	   wrapped where a track is too narrow for it. Tabular figures stop a ticking one jittering. */
	.status {
		display: flex;
		min-inline-size: 0;
	}

	.size,
	.second {
		display: block;
		overflow: hidden;
		font: var(--text-data);
		font-variant-numeric: tabular-nums;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.size {
		color: var(--sift-ink);
	}

	.second {
		color: var(--sift-ink-3);
	}

	.when {
		display: flex;
		justify-content: flex-end;
		min-inline-size: 0;
	}

	/* A file the album lost is worth a glance: the caution ink, on the row's second line. */
	.left-out {
		color: var(--sift-warn);
	}
</style>
