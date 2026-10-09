<script lang="ts">
	import { counted } from '$lib/entity/entity-counts';
	/*
	 * Who, other than you, can see this, and through what: a report, beside `ShareDialog` where
	 * decisions are made, with the two switches about the world outside this device. Every word is
	 * the server's. What you hid is said at the top, never per user.
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
		/** ONE thing: forty files have forty answers (`singleOnly`). */
		target: ShareTarget | null;
	}

	let { open = $bindable(false), target }: Props = $props();

	let report = $state<ReachReport | null>(null);
	let loading = $state(false);
	let failed = $state(false);

	/* WHY a user can see the thing where the report cannot say, filled per user as answers land. */
	let reasons = $state<Record<string, ReachThroughFiles>>({});

	const subject = $derived(target ? subjectOf([target]) : '');

	/* OUTSIDE THIS DEVICE: stash-box lookups and swaps, from `ReachReport.outside`. */
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

	/** Then the report is read again: it is the server's to say. */
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
			// Cleared on the way in, so no old answer sits under this heading.
			report = null;
			reasons = {};
			try {
				const found = await fetchReach(asked);
				if (!current) return;
				report = found;
				// Drawn before the explanations are asked for.
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
					// Left to the sentence, which is true.
				}
			})
		);
	}

	/** In force first, then narrowest-first, as the sharing panel orders them; losers kept. */
	function lines(user: ReachUser): ReachThrough[] {
		return [...user.through].sort(
			(a, b) => Number(b.decides) - Number(a.decides) || reachBreadth(a) - reachBreadth(b)
		);
	}

	/** A disabled user keeps its grants and can use none, so it is its own answer. */
	function standing(user: ReachUser): string {
		if (user.disabled) return 'User blocked';
		return user.sees ? 'Can see' : "Can't see";
	}

	/** A yes with no chain: an entity seen because one of its files is reachable. */
	function unexplained(user: ReachUser): boolean {
		// An ADMIN's yes is the role, drawn above.
		return user.sees && !user.disabled && user.role !== 'admin' && user.through.length === 0;
	}
</script>

<!-- A link where the thing has a page, closing the panel on the way (`sourceHref`). -->
{#snippet named(type: ShareableType, id: string | null, label: string)}
	{@const href = sourceHref(type, id)}
	{#if href}
		<a class="named" {href} onclick={() => (open = false)}>{label}</a>
	{:else}
		<span class="named">{label}</span>
	{/if}
{/snippet}

<Modal bind:open title="Visibility" description={subject} sheetClass="reach-sheet" scrolls={false}>
	<!-- What YOU hid, above the rows; absent while Hidden is shut, which names nothing. -->
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
		<!-- Nobody else on this Sift: said as a fact. -->
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

								<!-- An admin's yes is the ROLE, said in words. -->
								{#if user.role === 'admin'}
									<span class="reason">Every file, as an administrator</span>
								{:else if unexplained(user)}
									<!--
									A yes with no decision behind it (an entity): this true fallback
									shows while `explain` reads which files.
									-->
									{#if reasons[user.id]}
										{@const through = reasons[user.id]}
										{#each through.reasons as reason, at (`${at}:${reason.kind}${reason.id ?? ''}`)}
											{@const words = reasonWords(reason, through.files)}
											<span class="reason">
												<span class="verb" data-how="shared">Through a file</span>
												{words.lead}
												{#if words.name}
													{@render named(reason.kind, reason.id, words.name)}
												{/if}{words.tail ?? ''}
											</span>
										{/each}
										{#if !through.complete}
											<!--
											The ceiling, said: a count out of a page is not a count
											out of everything.
											-->
											<span class="reason">
												Read from the first {counted(through.files)} files of this one
											</span>
										{/if}
									{:else}
										<span class="reason">Through a file of this one they can already reach</span>
									{/if}
								{/if}

								<!-- POSITION in the key: words and names repeat. -->
								{#each lines(user) as line, at (`${at}:${line.how}${line.kind}${line.name ?? ''}`)}
									{@const words = reachLabel(line, kind)}
									{#if line.decides}
										<span class="reason">
											<span class="verb" data-how={line.how}>
												{line.how === 'restricted' ? 'Restricted' : 'Shared'}
											</span>
											{words.lead}
											{#if words.name}
												{@render named(line.kind, line.id, words.name)}
											{/if}
										</span>
									{:else}
										<!-- A decision that lost: faded, with why on hover. -->
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
		<!-- One way out: the switches apply as they are flipped. -->
		<Button tone="primary" onclick={() => (open = false)}>Done</Button>
	</div>
</Modal>

<style>
	:global(.reach-sheet) {
		/* The width only; `.sheet` clamps it (app.css). */
		--sheet-inline: 460px;
	}

	/* The cap on the box that SCROLLS, through this file's own class, as in `ShareDialog`. */
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

	/* One block per user. */
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

	.standing {
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	.standing[data-sees='true'] {
		color: var(--sift-ok);
	}

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

	/* Beaten: no coloured "Shared" on a line doing nothing. */
	.reason.beaten,
	.reason.beaten .verb,
	.reason.beaten .named {
		color: var(--sift-ink-3);
	}

	.named {
		color: var(--sift-ink);
	}

	a.named:hover {
		text-decoration: underline;
	}

	/* `Note`, one sentence with nothing to press. */
	.own-view {
		margin-block-end: var(--space-4);
	}

	/* The control column is the switch's width. */
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

	/* `.buttons` is the shared rule in app.css. */
</style>
