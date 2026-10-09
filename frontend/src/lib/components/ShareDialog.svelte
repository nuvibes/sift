<script lang="ts">
	import { counted } from '$lib/entity/entity-counts';
	import { Button, SettingLink } from '$lib/components/common';
	import Scroller from '$lib/components/common/Scroller.svelte';
	/*
	 * Who can see this, and the two ways of changing it, for any kind of thing or selection. Not
	 * shared is no row; Shared hands it over; Restricted promises never. Nothing is written until
	 * Apply, and the server decides everything.
	 */
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Modal from '$lib/components/common/Modal.svelte';
	import { libraryChanges, whenChanged } from '$lib/library/changes.svelte';
	import { loadCounts } from '$lib/entity/related.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import {
		fetchUsers,
		fetchGrants,
		fetchSources,
		fetchVaultSources,
		beatenBy,
		breadthOf,
		canUnhide,
		hiddenLabel,
		includesSitesWithin,
		put,
		readingOf,
		sourceHref,
		sourceLabel,
		subjectOf,
		unhide,
		type Effect,
		type Grant,
		type GrantSource,
		type Reading,
		type ShareTarget,
		type ShareableUser,
		type ShareableType,
		type Standing,
		type VaultSource
	} from '$lib/library/sharing';
	import { HIDDEN_EXPLAINS, HIDDEN_VERB } from '$lib/library/sharing-marks';

	interface Props {
		open?: boolean;
		targets: ShareTarget[];
		/** Only on an Apply that wrote something; cancelling keeps the caller's selection. */
		onapplied?: () => void;
	}

	let { open = $bindable(false), targets, onapplied }: Props = $props();

	let users = $state<ShareableUser[]>([]);
	let recorded = $state<Grant[][]>([]);
	/* Every grant that REACHES the thing, asked only for one thing. */
	let sources = $state<GrantSource[]>([]);
	/* What is CONCEALING it; empty means nothing, or a shut vault that names nothing. */
	let concealing = $state<VaultSource[]>([]);
	let staged = $state<Record<string, Standing>>({});
	let loading = $state(false);
	let failed = $state(false);
	let saving = $state(false);
	let unhiding = $state<string | null>(null);
	let within = $state(0);

	/* Guests only: an admin is past every rule, and the server refuses such a grant. */
	const guests = $derived(users.filter((user) => user.role === 'guest'));
	const subject = $derived(subjectOf(targets));
	const changes = $derived(Object.keys(staged).length);

	const nothingToDecide = $derived(!loading && !failed && guests.length === 0);

	$effect(() => {
		if (!open || targets.length === 0) return;
		const opened = targets;
		void (async () => {
			loading = true;
			failed = false;
			staged = {};
			// Cleared on the way in, or the hidden block would describe the last thing.
			concealing = [];
			try {
				const [everybody, current, from, hiddenBy] = await Promise.all([
					fetchUsers(),
					Promise.all(opened.map((target) => fetchGrants(target))),
					opened.length === 1 ? fetchSources(opened[0]) : Promise.resolve([]),
					opened.length === 1 ? fetchVaultSources(opened[0]) : Promise.resolve([])
				]);
				users = everybody;
				recorded = current;
				sources = from;
				concealing = hiddenBy;
			} catch {
				failed = true;
			} finally {
				loading = false;
			}
		})();
	});

	/* The bell this panel rang itself, which it has already drawn. */
	let rang = -1;
	whenChanged(libraryChanges, () => {
		const opened = targets;
		if (!open || opened.length === 0 || loading || saving) return;
		if (libraryChanges.generation === rang) return;
		void Promise.all([
			fetchUsers(),
			Promise.all(opened.map((target) => fetchGrants(target))),
			opened.length === 1 ? fetchSources(opened[0]) : Promise.resolve([]),
			opened.length === 1 ? fetchVaultSources(opened[0]) : Promise.resolve([])
		])
			.then(([everybody, current, from, hiddenBy]) => {
				if (targets !== opened || !open) return;
				const same = (held: unknown, read: unknown) =>
					JSON.stringify(held) === JSON.stringify(read);
				if (!same(users, everybody)) users = everybody;
				if (!same(recorded, current)) recorded = current;
				if (!same(sources, from)) sources = from;
				if (!same(concealing, hiddenBy)) concealing = hiddenBy;
			})
			.catch(() => {
				// The panel as drawn stays.
			});
	});

	/*
	 * The Sites within ONE site (`sites_within`, the tab's own number); its own effect, so it
	 * cannot fail the panel.
	 */
	$effect(() => {
		const only = targets.length === 1 && targets[0].type === 'site' ? targets[0].id : null;
		if (!open || !only) {
			within = 0;
			return;
		}
		let current = true;
		void loadCounts('site', only).then((counts) => {
			if (current) within = counts.sites_within ?? 0;
		});
		return () => {
			current = false;
		};
	});

	/** What is recorded ON the thing, which is what the two buttons change. */
	function localReading(user: ShareableUser): Reading {
		return staged[user.id] ?? readingOf(recorded, user.id);
	}

	/** The word beside the name: the outcome, counting everything above; the staged choice wins. */
	function reading(user: ShareableUser): Reading {
		const chosen = staged[user.id];
		if (chosen === 'shared' || chosen === 'restricted') return chosen;
		/* Taking the local decision off leaves whatever reaches it from elsewhere. */
		if (chosen === 'private') return elsewhere(user);
		const decisive = sources.find((source) => source.subject_user_id === user.id && source.decides);
		if (decisive) return decisive.effect === 'restrict' ? 'restricted' : 'shared';
		return readingOf(recorded, user.id);
	}

	function wordToEffect(word: Reading): Effect | null {
		if (word === 'shared') return 'share';
		if (word === 'restricted') return 'restrict';
		return null;
	}

	/** The resolver's ladder less its first rung, as a preview. */
	function elsewhere(user: ShareableUser): Reading {
		const reaching = sources.filter((source) => source.subject_user_id === user.id && !source.here);
		if (reaching.some((source) => source.effect === 'restrict')) return 'restricted';
		if (reaching.length > 0) return 'shared';
		return 'private';
	}

	/** Restricts first; every grant that reaches the file is listed. */
	function reasons(user: ShareableUser): {
		effect: Effect;
		verb: string;
		lead: string;
		name: string | null;
		type: ShareableType;
		id: string | null;
		decides: boolean;
		why: string;
	}[] {
		const kind = targets[0]?.type ?? 'item';
		const chosen = staged[user.id];
		const decisive = wordToEffect(reading(user));
		return (
			sources
				.filter((source) => source.subject_user_id === user.id)
				/* A staged decision replaces the line describing the current one. */
				.filter((source) => !(chosen !== undefined && source.here))
				// In force first, then narrowest-first: the resolver's order.
				.sort((a, b) => Number(b.decides) - Number(a.decides) || breadthOf(a) - breadthOf(b))
				.map((source) => {
					const decides = chosen === undefined ? source.decides : source.effect === decisive;
					const words = sourceLabel(source, kind);
					return {
						effect: source.effect,
						verb: source.effect === 'restrict' ? 'Restricted' : 'Shared',
						lead: words.lead,
						name: words.name,
						type: source.source_type,
						id: source.source_id,
						decides,
						why: decides ? '' : beatenBy(source)
					};
				})
		);
	}

	/**
	 * Three states on two buttons: pressing the lit one turns it off; both together is never
	 * offered.
	 */
	function choose(user: ShareableUser, word: Standing) {
		// Against what is recorded HERE, not the resolved answer.
		const desired: Standing = localReading(user) === word ? 'private' : word;
		const stored = readingOf(recorded, user.id);
		// Choosing back what is recorded is not a change.
		const next = { ...staged };
		if (desired === stored) delete next[user.id];
		else next[user.id] = desired;
		staged = next;
	}

	/** Out of the vault from the line naming it, immediately: it is not a grant to stage. */
	async function takeOut(source: VaultSource) {
		unhiding = source.source_id;
		try {
			await unhide(source);
			concealing = concealing.filter((each) => each.source_id !== source.source_id);
			libraryChanges.changed();
			rang = libraryChanges.generation;
		} catch {
			toasts.show("That couldn't be unhidden", { tone: 'error' });
		} finally {
			unhiding = null;
		}
	}

	function cancel() {
		staged = {};
		open = false;
	}

	async function apply() {
		if (changes === 0) {
			open = false;
			return;
		}
		const changed = changes;
		saving = true;
		try {
			for (const [subjectUserId, desired] of Object.entries(staged)) {
				// One at a time, so a failure is not "some of it, in no order".
				for (const [index, target] of targets.entries()) {
					await put(target, subjectUserId, desired, recorded[index] ?? []);
				}
			}
			// Tiles' badges and who sees what both move.
			libraryChanges.changed();
			staged = {};
			open = false;
			// Said out loud: nothing on this screen moves.
			toasts.show(
				changed === 1 ? 'Sharing changed' : `Sharing changed for ${counted(changed)} users`,
				{
					tone: 'success'
				}
			);
			onapplied?.();
		} catch {
			toasts.show("That couldn't be saved", { tone: 'error' });
		} finally {
			saving = false;
		}
	}
</script>

<!-- A link where the thing has a page (`sourceHref`), closing the panel on the way. -->
{#snippet named(type: ShareableType, id: string | null, label: string)}
	{@const href = sourceHref(type, id)}
	{#if href}
		<a class="named" {href} onclick={() => (open = false)}>{label}</a>
	{:else}
		<span class="named">{label}</span>
	{/if}
{/snippet}

<Modal
	bind:open
	onOpenChange={(next) => !next && (staged = {})}
	title="Sharing"
	description={subject}
	sheetClass="share-sheet"
	scrolls={false}
>
	<!--
	What is hiding this from YOU, above the rows because it outranks them; solid where it is on this
	very thing.
	-->
	{#if concealing.length > 0}
		<ul class="hidden-by">
			{#each concealing as source, at (`${at}:${source.source_type}:${source.source_id ?? ''}`)}
				{@const words = hiddenLabel(source, targets[0]?.type ?? 'item')}
				<li>
					<Icon name="visibility_off" size={16} filled={source.here} />
					<span class="said">
						<span class="verb hidden-verb">{HIDDEN_VERB}</span>
						{words.lead}
						{#if words.name}
							{@render named(source.source_type, source.source_id, words.name)}
						{/if}
					</span>
					{#if canUnhide(source)}
						<!-- On each line: unhiding the folder is not unhiding the file. -->
						<Button
							size="small"
							icon="visibility"
							disabled={unhiding !== null}
							onclick={() => void takeOut(source)}
						>
							Unhide
						</Button>
					{/if}
				</li>
			{/each}
			<li class="explains">{HIDDEN_EXPLAINS}</li>
		</ul>
	{/if}

	<!-- What else a Site's decision covers: the Sites within it. -->
	{#if includesSitesWithin(within)}
		<p class="reaches">
			<Icon name="hub" size={16} />
			<span>{includesSitesWithin(within)}</span>
		</p>
	{/if}

	{#if loading}
		<p class="note">Loading&hellip;</p>
	{:else if failed}
		<p class="note">That couldn't be loaded.</p>
	{:else if guests.length === 0}
		<!-- No guests is the ordinary state: said as a fact. -->
		<p class="note">
			There are no guests yet. Add one in
			<SettingLink section="users">Users</SettingLink>, and it will appear here.
		</p>
	{:else}
		<div class="rows-box">
			<Scroller>
				<ul class="rows">
					{#each guests as user (user.id)}
						{@const word = reading(user)}
						{@const local = localReading(user)}
						{@const shared = local === 'shared'}
						{@const restricted = local === 'restricted'}
						{@const reachedShare = !shared && word === 'shared'}
						{@const reachedRestrict = !restricted && word === 'restricted'}
						<!-- Recorded here and BEATEN by something that outranks it. -->
						{@const beatenShare = shared && word !== 'shared'}
						{@const beatenRestrict = restricted && word !== 'restricted'}
						<!-- The word is the OUTCOME; the buttons are what is set HERE. -->
						<li class="row">
							<span class="who">
								<span class="name">{user.username}</span>
								<span class="standing" data-standing={word}>
									{word === 'shared'
										? 'Shared'
										: word === 'restricted'
											? 'Restricted'
											: word === 'mixed'
												? 'Mixed'
												: 'Not shared'}
								</span>

								<!-- POSITION in the key: neither words nor names are unique. -->
								{#each reasons(user) as reason, at (`${at}:${reason.verb}${reason.lead}${reason.name ?? ''}`)}
									{#if reason.decides}
										<span class="reason">
											<!--
											Weight on the verb and the name, never a whole red line.
											-->
											<span class="verb" data-effect={reason.effect}>{reason.verb}</span>
											{reason.lead}
											{#if reason.name}
												{@render named(reason.type, reason.id, reason.name)}
											{/if}
										</span>
									{:else}
										<Tooltip label={reason.why} placement="bottom">
											<span class="reason beaten">
												<span class="verb">{reason.verb}</span>
												{reason.lead}
												{#if reason.name}
													{@render named(reason.type, reason.id, reason.name)}
												{/if}
											</span>
										</Tooltip>
									{/if}
								{/each}
							</span>

							<span class="choices">
								<Button
									size="small"
									class="choice {reachedShare ? 'reached' : ''} {beatenShare ? 'beaten' : ''}"
									icon="group"
									pressed={shared && !beatenShare}
									disabled={saving}
									onclick={() => choose(user, 'shared')}
								>
									Share
								</Button>
								<Button
									size="small"
									class="choice restrict {reachedRestrict ? 'reached' : ''} {beatenRestrict
										? 'beaten'
										: ''}"
									icon="block"
									pressed={restricted && !beatenRestrict}
									disabled={saving}
									onclick={() => choose(user, 'restricted')}
								>
									Restrict
								</Button>
							</span>
						</li>
					{/each}
				</ul>
			</Scroller>
		</div>

		<p class="note">
			A guest set to Restricted never sees this, even if you later share a folder or tag that
			includes it.
		</p>
		<p class="note">
			To share one file from a restricted folder, take the restriction off the folder first.
		</p>
	{/if}

	<div class="buttons">
		<!-- ONE BUTTON when there is nothing to decide. -->
		{#if nothingToDecide}
			<Button tone="primary" onclick={cancel}>Dismiss</Button>
		{:else}
			<Button onclick={cancel}>Cancel</Button>
			<Button tone="primary" disabled={saving || loading} onclick={() => void apply()}>
				Apply{changes > 0 ? ` (${changes})` : ''}
			</Button>
		{/if}
	</div>
</Modal>

<style>
	:global(.share-sheet) {
		/* The width only; `.sheet` clamps it (app.css). */
		--sheet-inline: 460px;
	}

	/* The cap on the box that SCROLLS, anchored to `.rows-box`; on the `<ul>` it would spill. */
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

	.who {
		display: flex;
		flex-direction: column;
		gap: 2px;
		min-inline-size: 0;
	}

	.name {
		font: var(--text-label);
		color: var(--sift-ink);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.reason {
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	/* Beaten: no green "Shared" on a line doing nothing. */
	.reason.beaten,
	.reason.beaten .verb,
	.reason.beaten .named {
		color: var(--sift-ink-3);
	}

	/* What is hiding this, a fact rather than a control, above the users. */
	.hidden-by {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		margin: 0 0 var(--space-4);
		padding: var(--space-3);
		list-style: none;
		border-radius: var(--radius-md);
		background: var(--sift-surface-3);
	}

	.hidden-by li {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}

	.hidden-by .explains {
		color: var(--sift-ink-3);
		padding-block-start: var(--space-1);
		padding-inline-start: calc(16px + var(--space-2));
	}

	.hidden-verb {
		color: var(--sift-ink);
	}

	/* The thing pointed at carries the weight, not the verb, which never varies. */
	.named {
		font-weight: 600;
		color: var(--sift-ink);
	}

	a.named {
		text-decoration: none;
		border-radius: var(--radius-sm);
		transition: color var(--dur-instant) var(--ease);
	}

	a.named:hover {
		color: var(--sift-ink);
		text-decoration: underline;
	}

	a.named:focus-visible {
		outline: none;
		box-shadow: var(--focus-ring);
	}

	/* A quiet way out, pushed over by the sentence growing. */
	.said {
		display: inline-flex;
		align-items: baseline;
		gap: 0.25em;
		flex-wrap: wrap;
		min-width: 0;
		margin-inline-end: auto;
	}

	.verb[data-effect='share'] {
		color: var(--sift-ok);
	}

	.verb[data-effect='restrict'] {
		color: var(--sift-bad-text);
	}

	.standing {
		display: flex;
		align-items: baseline;
		gap: var(--space-2);
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	.standing[data-standing='shared'] {
		color: var(--sift-ok);
	}

	.standing[data-standing='restricted'] {
		color: var(--sift-bad-text);
	}

	.choices {
		display: flex;
		gap: var(--space-1);
		flex: none;
	}

	/* One width for both, so the row does not jump; `:global` for the shared button. */
	.choices :global(.choice) {
		min-inline-size: var(--choice-width);
	}

	/* In force from elsewhere: a tint, since this button does not undo it. */
	.choices :global(.choice.reached) {
		border-color: var(--sift-accent);
		background: var(--sift-accent-bg);
		color: var(--sift-accent-text);
	}

	.choices :global(.choice.restrict.reached) {
		border-color: var(--sift-bad-text);
		background: var(--sift-bad-bg);
		color: var(--sift-bad-text);
	}

	.choices :global(.choice[aria-pressed='true']) {
		border-color: transparent;
		background: var(--sift-accent);
		color: var(--primary-foreground);
	}

	/* The label token for this fill: 3.29:1. */
	.choices :global(.choice.restrict[aria-pressed='true']) {
		background: var(--sift-bad);
		color: var(--destructive-foreground);
	}

	.choices :global(.choice.beaten) {
		background: none;
		border-color: var(--sift-line);
		color: var(--sift-ink-3);
	}

	.note {
		margin: 0 0 var(--space-4);
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	.reaches {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		margin: 0 0 var(--space-3);
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}

	:global(.share-sheet .confirm:disabled) {
		opacity: 0.5;
		cursor: default;
	}
</style>
