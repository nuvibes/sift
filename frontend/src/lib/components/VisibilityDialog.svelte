<script lang="ts">
	import { counted } from '$lib/entity/entity-counts';
	/*
	 * Who, other than you, can see this, and through what.
	 *
	 * The sibling of `ShareDialog`, deliberately a separate panel. Sharing is where a decision is
	 * made, so every row there is a control. This is a report about the users on this device, and
	 * nothing in that part changes anything; the two switches at its foot are about the world
	 * outside it (stash-box lookups and swaps), where the report and the decision are one row. It
	 * answers what the sharing panel cannot, "can anybody else actually get to it", which
	 * differs whenever the reason lives elsewhere. A file inside a shared folder, carrying a shared
	 * tag, released by a label under a shared network has three ways in and nothing written on it.
	 *
	 * Every word of it is the server's: the verdict, whether each line is the one in force, and
	 * which user the report is about are decided there and drawn here. A panel checked before
	 * handing a library to a second person is the last place for a second opinion about
	 * permissions.
	 *
	 * Hiding is yours and is reported at the top, never per user: what you hid is withheld from you
	 * and nobody else, so it does not answer "who can see this". Each row reports permission.
	 *
	 * One panel for every kind of thing, as with sharing: a file, a folder, a person, a tag, a
	 * collection, a site and a Photo Set are one question asked about different objects.
	 */
	import { Button, Empty, LabelledRow, Note, SectionHeading, Switch } from '$lib/components/common';
	import { setKeptLocal } from '$lib/entity/enrichment.svelte';
	import { setKeptFromSwaps, type RefusalSubject } from '$lib/components/swap/swap';
	import { sayAgo } from '$lib/shell/when';
	import { libraryChanges, whenChanged } from '$lib/library/changes.svelte';
	import Modal from '$lib/components/common/Modal.svelte';
	import Scroller from '$lib/components/common/Scroller.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import {
		fetchReach,
		fetchReachThrough,
		reachBeatenBy,
		reachBreadth,
		reachLabel,
		reasonWords,
		sourceHref,
		subjectOf,
		type ReachUser,
		type ReachReport,
		type ReachThrough,
		type ReachThroughFiles,
		type ShareTarget,
		type ShareableType
	} from '$lib/library/sharing';

	interface Props {
		open?: boolean;
		/**
		 * What the report is about. Null is what "not open on anything" is.
		 *
		 * ONE thing, unlike the sharing panel, and it is not an omission. A decision can sensibly be
		 * made about forty files in one go (it is the same decision made forty times) but forty
		 * files have forty different answers to "who can see this and how", and a panel that showed
		 * one of them, or the intersection of them, would be answering a question nobody asked. The
		 * verb is `singleOnly` for the same reason.
		 */
		target: ShareTarget | null;
	}

	let { open = $bindable(false), target }: Props = $props();

	let report = $state<ReachReport | null>(null);
	let loading = $state(false);
	let failed = $state(false);

	/*
	 * WHY a user can see the thing, per user, where the report itself cannot say.
	 *
	 * Keyed by user and filled in as each answer lands, because it is one read per user and
	 * only for the users that need it. See `unexplained` below. A row with nothing here draws
	 * the honest sentence in the meantime rather than a blank space that fills a moment later.
	 */
	let reasons = $state<Record<string, ReachThroughFiles>>({});

	const subject = $derived(target ? subjectOf([target]) : '');

	/*
	 * OUTSIDE THIS DEVICE: the two ways something about this thing can leave it, a stash-box
	 * lookup and a swap with another Sift, each with its switch ("Don't enrich", "Don't swap")
	 * and where it stands. The server's words for both (`ReachReport.outside`), read through the
	 * one refusal the lookups and the swap themselves read, so this cannot disagree with them.
	 * Absent for a kind neither is ever told about.
	 */
	const outside = $derived(report?.outside ?? null);
	const refusalKind = $derived<RefusalSubject | null>(
		target?.type === 'item'
			? 'asset'
			: target?.type === 'person' ||
				  target?.type === 'site' ||
				  target?.type === 'tag' ||
				  target?.type === 'folder'
				? target.type
				: null
	);
	let changing = $state(false);

	const enrichWords = $derived.by(() => {
		if (!outside) return '';
		if (outside.enrich_refused && !outside.enrich_refused_here) {
			return refusalKind === 'folder'
				? "Kept local by a folder it's inside, so no stash-box is asked about its files."
				: "Kept local by something it's filed under, so no stash-box is asked about it.";
		}
		if (refusalKind === 'folder') {
			return outside.enrich_refused
				? 'No stash-box is asked about any file inside it.'
				: 'A stash-box can be asked about the files inside it.';
		}
		if (outside.enrich_refused) return 'No stash-box is asked about it.';
		if (outside.enriched_at) {
			const by = outside.enriched_by ?? 'A stash-box';
			return `${by} last filled it in ${sayAgo(outside.enriched_at, Date.now() / 1000)}.`;
		}
		return 'No stash-box has filled anything in about it yet.';
	});

	const swapWords = $derived.by(() => {
		if (!outside) return '';
		if (outside.swap_refused_here && refusalKind === 'folder') {
			return 'No file inside is offered in a swap, or said to be here.';
		}
		if (outside.swap_refused_here) return 'Never offered in a swap, and never said to be here.';
		if (outside.swap_refused && outside.enrich_refused) {
			return "Kept out of swaps too, because Don't enrich keeps everything about it on this device.";
		}
		if (outside.swap_refused && refusalKind === 'folder') {
			return "Kept out of swaps by a folder it's inside.";
		}
		if (outside.swap_refused) return "Kept out of swaps by something it's filed under.";
		return 'Can be offered when you start a swap.';
	});

	/** Flip one of the two switches, then read the report again: it is the server's to say. */
	async function change(which: 'enrich' | 'swap', on: boolean): Promise<void> {
		const asked = target;
		const kind = refusalKind;
		if (!asked?.id || !kind) return;
		changing = true;
		try {
			if (which === 'enrich') await setKeptLocal(kind, asked.id, on);
			else await setKeptFromSwaps(kind, asked.id, on);
			const fresh = await fetchReach(asked);
			if (target === asked) report = fresh;
		} catch {
			// The switch reads the report, which did not move: it springs back by itself.
		} finally {
			changing = false;
		}
	}
	const kind = $derived<ShareableType>(target?.type ?? 'item');

	$effect(() => {
		if (!open || !target) return;
		const asked = target;
		let current = true;
		void (async () => {
			loading = true;
			failed = false;
			// Cleared on the way in rather than replaced on the way out, so a slow read cannot leave
			// the previous subject's answer on screen under this subject's heading, which is the one
			// mistake this panel could make that somebody would act on.
			report = null;
			reasons = {};
			try {
				const found = await fetchReach(asked);
				if (!current) return;
				report = found;
				// Drawn the moment the report lands, before the explanations are asked for. They are
				// a read per user that needs one, and holding the whole panel behind them would
				// make the common case (a file, where every yes already has its chain) wait for
				// nothing.
				loading = false;
				await explain(asked, found);
			} catch {
				if (current) failed = true;
			} finally {
				if (current) loading = false;
			}
		})();
		return () => {
			current = false;
		};
	});

	/**
	 * Ask WHY, for the users whose yes the report has nothing to say about.
	 *
	 * One read per such user, and there is usually at most one. Not folded into the report
	 * above and not asked for everybody: it reads a page of the entity's files and works out what
	 * let each of them through, which is a real read rather than a reshaping of the first one, and
	 * asking it for a user whose yes is already explained would be paying for an answer that
	 * is on screen.
	 *
	 * A failure is silent, and deliberately: the sentence it would have replaced is true and
	 * already drawn. An error message in its place would be a panel reporting on itself in the
	 * middle of a report about permissions, which is exactly where somebody should not have to
	 * work out which of the two they are reading.
	 */
	/* A change elsewhere while open: the report is asked again and replaces the drawn one in place. */
	whenChanged(libraryChanges, () => {
		const asked = target;
		if (!open || !asked || loading) return;
		void fetchReach(asked)
			.then(async (found) => {
				if (target !== asked || !open) return;
				if (JSON.stringify(found) === JSON.stringify(report)) return;
				report = found;
				await explain(asked, found);
			})
			.catch(() => {
				// The report as drawn stays.
			});
	});

	async function explain(asked: ShareTarget, found: ReachReport): Promise<void> {
		const owed = found.users.filter((user) => unexplained(user));
		await Promise.all(
			owed.map(async (user) => {
				try {
					const through = await fetchReachThrough(asked, user.id);
					if (target === asked && through.reasons.length > 0) {
						reasons = { ...reasons, [user.id]: through };
					}
				} catch {
					// Left to the sentence. See above.
				}
			})
		);
	}

	/**
	 * The lines under one name, in the order the resolver reads them.
	 *
	 * What is in force first and then the rest narrowest-first, exactly as the sharing panel orders
	 * its reasons, so the block can be read downwards as the reason for the word above it. The
	 * losers are kept rather than filtered out: a share underneath a restrict is the answer to what
	 * happens when the restrict comes off, which is the next question every time.
	 */
	function lines(user: ReachUser): ReachThrough[] {
		return [...user.through].sort(
			(a, b) => Number(b.decides) - Number(a.decides) || reachBreadth(a) - reachBreadth(b)
		);
	}

	/**
	 * What the word beside a name says.
	 *
	 * A disabled user is its own answer rather than a plain no. It keeps everything it was ever
	 * granted and can make no request to use any of it, so "Cannot see" alone would read as a
	 * decision somebody made about this thing, and the day the user is switched back on, that
	 * reading is wrong without anything here having changed.
	 */
	function standing(user: ReachUser): string {
		if (user.disabled) return 'User blocked';
		return user.sees ? 'Can see' : "Can't see";
	}

	/**
	 * A yes this report has nothing to say about, which is a real state and not a missing answer.
	 *
	 * Only an entity reaches it. A guest's verdict row for a file exists because a grant reached
	 * that file, and every such grant is in the chain the server sends, so a file with a yes always
	 * has a line. An entity is visible on a different rule: it is on the wall because one of its
	 * files can be reached, and what let that file through was said about something else. On a
	 * library shared by folder, every person, site and tag comes back `sees: true` with an empty
	 * chain.
	 */
	function unexplained(user: ReachUser): boolean {
		// An ADMIN is not one of these and the row above draws them: their yes is the role, which is
		// an explanation and not a missing one. Asked about anyway, the second read would walk a
		// page of files to find the grants behind a reach that rests on no grant at all.
		return user.sees && !user.disabled && user.role !== 'admin' && user.through.length === 0;
	}
</script>

<!--
	The thing a line points at, and a way to it where there is one.

	A link rather than a word wherever the thing has a page, and it closes the panel on the way: a
	report that sends you to the Site it just named, over a sheet you then have to dismiss, is a
	report that makes you do the last step by hand. A folder and a library get the plain word: they
	live in the tree inside Settings, which is a panel and not an address. See `sourceHref`.
-->
{#snippet named(type: ShareableType, id: string | null, label: string)}
	{@const href = sourceHref(type, id)}
	{#if href}
		<a class="named" {href} onclick={() => (open = false)}>{label}</a>
	{:else}
		<span class="named">{label}</span>
	{/if}
{/snippet}

<Modal bind:open title="Visibility" description={subject} sheetClass="reach-sheet" scrolls={false}>
	<!--
		What YOU have hidden, above the rows because it is a different rule and it outranks them on
		your own screen. It is not a share pointed the other way: it withholds the thing from you and
		from nobody else, so a row underneath saying somebody can see it is telling the truth about
		them while this is telling the truth about you.

		Absent while your Hidden is shut, and that is not a claim that nothing is hidden: the names
		of what conceals a thing are the concealment, so the server answers the way the rest of the
		application does.
	-->
	{#if report?.concealed}
		<div class="own-view">
			<Note icon="visibility_off">
				{report.hidden
					? "You have hidden this. It's off your own screens and nobody else's."
					: "Something above this is hiding it from you. It's off your own screens and nobody else's."}
			</Note>
		</div>
	{/if}

	{#if loading}
		<p class="note">Loading&hellip;</p>
	{:else if failed}
		<p class="note">That couldn't be loaded.</p>
	{:else if report && report.users.length === 0}
		<!-- Not an error, and the ordinary state of a Sift with one person on it. The report is about
		     everybody BUT you, so with nobody else there is genuinely nothing to report, said as the
		     fact it is rather than drawn as an empty list somebody has to interpret. -->
		<Empty icon="group" title="Nobody else" scope="block">
			You are the only user here, so nothing about this can be seen by anybody else.
		</Empty>
	{:else if report}
		<div class="rows-box">
			<Scroller>
				<ul class="rows">
					{#each report.users as user (user.id)}
						<li class="row">
							<span class="who">
								<span class="name">{user.name}</span>
								<span class="standing" data-sees={user.sees}>{standing(user)}</span>

								<!-- An admin is past every access rule, so the yes is the ROLE and not a decision.
								     Said in words rather than left as a bare "Can see" with nothing under it, which
								     reads as a share somebody made and would send them looking for one to revoke. -->
								{#if user.role === 'admin'}
									<span class="reason">Every file, as an administrator</span>
								{:else if unexplained(user)}
									<!--
										A yes with no decision behind it, which happens on an entity
										and cannot on a file.

										A person, a tag, a site, a collection or a Photo Set is on a
										guest's wall because a file under it is reachable, and that
										file's reason (the folder it sits in, another tag it
										carries) was never said about the thing this panel is open
										on. So the server has a true yes and no grant row to name,
										and this panel must never draw "Can see" over a blank.

										This sentence is the fallback. Naming which files and what
										let them through is a second read with its own ceiling (see
										`explain`); this is drawn while that answer is in flight,
										for a user the second read could explain nothing about, and
										when it cannot be made at all. It is true in every case: it
										is exactly what the first read asked.
									-->
									{#if reasons[user.id]}
										{@const through = reasons[user.id]}
										{#each through.reasons as reason, at (`${at}:${reason.kind}${reason.id ?? ''}`)}
											{@const words = reasonWords(reason, through.files)}
											<span class="reason">
												<!-- One word in colour, exactly as a decision's line above: the verb
												     there says what was done, and here it says that the reach is
												     second-hand: nothing was decided about the thing on screen. -->
												<span class="verb" data-how="shared">Through a file</span>
												{words.lead}
												{#if words.name}
													{@render named(reason.kind, reason.id, words.name)}
												{/if}{words.tail ?? ''}
											</span>
										{/each}
										{#if !through.complete}
											<!-- The ceiling, said rather than hidden. A count out of a page reads as a
											     count out of everything unless the panel says which it is. The same ink
											     as the reasons above it: it is a fact about the read and not a quieter
											     kind of answer, and `--sift-ink-4` is decoration rather than text. -->
											<span class="reason">
												Read from the first {counted(through.files)} files of this one
											</span>
										{/if}
									{:else}
										<span class="reason">Through a file of this one they can already reach</span>
									{/if}
								{/if}

								<!-- POSITION is part of the key. Two lines of one kind can carry the same words and
								     the same name: two collections can share a name, and two grants made over
								     everything have no name at all. -->
								{#each lines(user) as line, at (`${at}:${line.how}${line.kind}${line.name ?? ''}`)}
									{@const words = reachLabel(line, kind)}
									{#if line.decides}
										<span class="reason">
											<!-- Two words carry the weight and the sentence between them does not: the
											     VERB, which is what was done, and the NAME, which is what it was done
											     on. A whole line in colour reads as an error; one word in it reads as
											     a fact. -->
											<span class="verb" data-how={line.how}>
												{line.how === 'restricted' ? 'Restricted' : 'Shared'}
											</span>
											{words.lead}
											{#if words.name}
												{@render named(line.kind, line.id, words.name)}
											{/if}
										</span>
									{:else}
										<!-- A decision that lost. Faded, and it says why on hover: it is worth showing,
										     because it is what happens when the thing beating it is lifted, and it must
										     not read as though it is in force. -->
										<Tooltip label={reachBeatenBy(line)} placement="bottom">
											<span class="reason beaten">
												<span class="verb">
													{line.how === 'restricted' ? 'Restricted' : 'Shared'}
												</span>
												{words.lead}
												{#if words.name}
													{@render named(line.kind, line.id, words.name)}
												{/if}
											</span>
										</Tooltip>
									{/if}
								{/each}
							</span>
						</li>
					{/each}
				</ul>
			</Scroller>
		</div>

		<p class="note">
			This is what each user can reach, however the reach was arranged. Change it in Sharing.
		</p>
	{/if}

	{#if outside}
		<SectionHeading band>Outside this device</SectionHeading>
		<div class="outside">
			<LabelledRow label="Don't enrich" help={enrichWords}>
				<Switch
					label="Don't enrich"
					checked={outside.enrich_refused_here}
					disabled={changing}
					onCheckedChange={(on) => void change('enrich', on)}
				/>
			</LabelledRow>
			<LabelledRow label="Don't swap" help={swapWords}>
				<Switch
					label="Don't swap"
					checked={outside.swap_refused_here}
					disabled={changing}
					onCheckedChange={(on) => void change('swap', on)}
				/>
			</LabelledRow>
		</div>
	{/if}

	<div class="buttons">
		<!-- One way out, and it says what it does. The only writes here are the two switches, which
		     take effect as they are flipped, so a Cancel would offer to undo nothing. -->
		<Button tone="primary" onclick={() => (open = false)}>Done</Button>
	</div>
</Modal>

<style>
	/*
	 * The same width as the sharing sheet, because it is the same list of users read a different
	 * way.
	 */
	:global(.reach-sheet) {
		/* The width only. `.sheet` clamps it to the window. See app.css. */
		--sheet-inline: 460px;
	}

	/* The cap goes on the box that SCROLLS, reached through a class of this file's own, never as a
	   bare `:global(.scroll-root)`, which would be a rule about every scrolling box in the
	   application the moment anything loaded this stylesheet. See `ShareDialog`, where the same
	   ceiling sits for the same reason. */
	.rows-box :global(.scroll-root) {
		max-block-size: 40vh;
	}

	.rows {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		margin: 0 0 var(--space-4);
		padding: 0;
		list-style: none;
	}

	.row {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-3);
		padding: var(--space-2) var(--space-1);
	}

	/* The name and the lines under it, stacked tight: they are one block about one user, so the
	   gap between them is smaller than the gap between the users themselves. `--space-1` rather
	   than the 2px the sharing panel writes by hand: the two are a pixel apart on screen and only
	   one of them is a number this design system can move. */
	.who {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		min-inline-size: 0;
	}

	.name {
		font: var(--text-label);
		color: var(--sift-ink);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	/* The answer, in the ink the sharing panel gives the same two facts: a yes is the thing worth
	   noticing on a report somebody opened to find one, and a no is quiet. */
	.standing {
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	.standing[data-sees='true'] {
		color: var(--sift-ok);
	}

	/* The explanation under the answer, quieter than it. */
	.reason {
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	.verb {
		color: var(--sift-ink-2);
	}

	.verb[data-how='shared'],
	.verb[data-how='inherited'] {
		color: var(--sift-ok);
	}

	.verb[data-how='restricted'] {
		color: var(--sift-bad-text);
	}

	/* Beaten: the whole line steps back and the verb loses its colour with it. A coloured "Shared" on
	   a line that is doing nothing is the one thing this panel must not draw. */
	.reason.beaten,
	.reason.beaten .verb,
	.reason.beaten .named {
		color: var(--sift-ink-3);
	}

	/* The name is the thing being pointed at, so it wears the ink the subject at the top of the
	   panel wears, and it is a way to that thing where there is one. */
	.named {
		color: var(--sift-ink);
	}

	a.named:hover {
		text-decoration: underline;
	}

	/* What YOU have hidden, said as the SHARED aside rather than as a box of this panel's own.
	 *
	 * The sharing panel draws the same fact inside a surface, which is right there and would be wrong
	 * here: its block is a LIST of everything concealing the thing, each line carrying its own Unhide
	 * button, so the box is what holds a group of rows together. This is one sentence with nothing to
	 * press, which is exactly what `Note` is: a standing fact about a screen with a symbol in front
	 * of it. Copying the container would have been copying the part that was about the list.
	 *
	 * The wrapper carries only the space under it, so the aside sits off the rows rather than running
	 * into them. */
	.own-view {
		margin-block-end: var(--space-4);
	}

	/* Both rows hold a switch, so the control column is the switch's own width: the settings
	   width (15rem) would leave the words a third of this narrow sheet, three lines for one sentence. */
	.outside {
		--settings-control-col: auto;
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		margin-block-end: var(--space-4);
	}

	.note {
		margin: 0 0 var(--space-3);
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	/* `.buttons` is the shared `.sheet .buttons` rule in app.css. */
</style>
