<script lang="ts">
	import { counted } from '$lib/entity/entity-counts';
	import { Button, SettingLink } from '$lib/components/common';
	import Scroller from '$lib/components/common/Scroller.svelte';
	/*
	 * Who can see this, and the two ways of changing the answer.
	 *
	 * One panel for every kind of thing that can be shared (a file, a folder, a person, a tag, a
	 * collection, a site), because they are one decision about different objects. It opens on a
	 * selection as readily as on one thing: a decision about forty files is the same decision forty
	 * times.
	 *
	 * The three words matter more than the controls. Not shared is where everything starts and
	 * means no row: this user cannot reach it here, but a share made elsewhere still might. Shared
	 * hands it over. Restricted is a promise: never this, whatever else gets shared later. A switch
	 * cannot express the difference between "I never shared this" and "I said never", and that
	 * difference is the whole design, so each guest has two buttons, Share and Restrict, and neither
	 * lit is the third answer.
	 *
	 * Nothing is written until Apply: a press moves a word on the screen and Cancel throws the lot
	 * away, so a misread row is never already a fact, and a selection costs one press and one
	 * Apply.
	 *
	 * Nothing here is a control in the security sense. Every button posts to a server that decides
	 * for itself, re-reads the answer on the guest's next request, and would refuse the same
	 * request made with curl. A wrong row here draws the wrong word for a moment.
	 */
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Modal from '$lib/components/common/Modal.svelte';
	import { libraryChanges } from '$lib/library/changes.svelte';
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
		/** What the panel is acting on. Empty is what "not open on anything" is. */
		targets: ShareTarget[];
		/**
		 * Something was actually written.
		 *
		 * Only on Apply, and only when there was something to apply: a screen that opened this on a
		 * selection uses it to let the selection go, and cancelling out of the panel is not a reason
		 * to lose what was picked.
		 */
		onapplied?: () => void;
	}

	let { open = $bindable(false), targets, onapplied }: Props = $props();

	let users = $state<ShareableUser[]>([]);
	/** What is recorded right now, one list per thing the panel is open on, in the same order. */
	let recorded = $state<Grant[][]>([]);
	/*
	 * Every grant that REACHES the thing, and what each was made on.
	 *
	 * Different from `recorded`, which is what was written on the thing itself, and the difference
	 * is the question people actually have when they see a mark they did not make. Asked only when
	 * the panel is open on one thing: across a selection the answer would be a different list per
	 * file and there is nowhere sensible to draw that.
	 */
	let sources = $state<GrantSource[]>([]);
	/*
	 * And what is CONCEALING it, which is the other rule entirely.
	 *
	 * Empty is the ordinary answer twice over: nothing is hidden, or the vault is shut and the
	 * server will not name what it is keeping back. The panel cannot tell those apart and does not
	 * need to: with nothing to say it says nothing, which is right either way.
	 */
	let concealing = $state<VaultSource[]>([]);
	/** What somebody has chosen and not yet applied. Absent means "leave this row alone". */
	let staged = $state<Record<string, Standing>>({});
	let loading = $state(false);
	let failed = $state(false);
	let saving = $state(false);
	/** Which source is being taken out of the vault, so the row cannot be pressed twice. */
	let unhiding = $state<string | null>(null);
	/** Labels under the site this is open on, when it is open on exactly one site. */
	let within = $state(0);

	/* Guests only. An admin is already past every access rule, so a grant made to one does nothing
	   whatsoever, and the server refuses to store it for exactly that reason. Offering the row
	   would be offering a control that cannot work. */
	const guests = $derived(users.filter((user) => user.role === 'guest'));
	const subject = $derived(subjectOf(targets));
	const changes = $derived(Object.keys(staged).length);

	/* Whether this panel can be acted on at all. Nothing can be staged without a guest user to
	   stage it against, so with none there is only a sentence on screen and one way out of it. */
	const nothingToDecide = $derived(!loading && !failed && guests.length === 0);

	$effect(() => {
		if (!open || targets.length === 0) return;
		const opened = targets;
		void (async () => {
			loading = true;
			failed = false;
			staged = {};
			// Cleared on the way in rather than only replaced on the way out. The rows below are not
			// drawn while this is loading or after it fails, but the hidden block is outside that,
			// so left standing it would describe whatever the panel was open on last time.
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

	/*
	 * How many Sites a SITE this panel is open on holds within it, so it can say what the decision
	 * reaches.
	 *
	 * Its own effect and not part of the load above, because it is not part of the panel's own
	 * question and must not be able to fail it: a network whose count could not be fetched should
	 * still show the sharing rows. Only for ONE site: across a selection the honest answer is a
	 * different number for every row, and there is nowhere to draw that.
	 *
	 * The counts route rather than a listing of children, because it is the request the tab strip
	 * already makes and `sites_within` is the very number the Sites tab on a Site's page draws
	 * (`sites` is the Sites a set of files came from, null for a Site, and read here it would draw
	 * no sentence at all). Asking any other way would be a second population that could disagree
	 * with the tab.
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

	/**
	 * What is recorded ON the things the panel is open on, which is what the two buttons are about.
	 *
	 * Not the same question as the word beside the name. A file inside a shared folder is reachable
	 * and has nothing written on it, so its Share button is dark: pressing it writes a share here,
	 * and pressing a lit one takes that share away. A button lit by a decision made somewhere else
	 * would do nothing when pressed.
	 */
	function localReading(user: ShareableUser): Reading {
		return staged[user.id] ?? readingOf(recorded, user.id);
	}

	/**
	 * The word beside the name: what this user can actually do, counting everything above, so a
	 * file shared by its folder does not say "Not shared" over a line saying which folder shared
	 * it.
	 *
	 * The staged choice wins while there is one, because that is what Apply is about to make true.
	 * Across a selection there are no sources to read, so it falls back to the local answer, the
	 * only one there.
	 */
	function reading(user: ShareableUser): Reading {
		const chosen = staged[user.id];
		if (chosen === 'shared' || chosen === 'restricted') return chosen;
		/*
		 * Taking the local decision off does not make it Not shared.
		 *
		 * A grant on the thing itself is final, so choosing one of the two words IS the answer. But
		 * choosing neither only removes what was written here, and whatever reaches the file from
		 * the folder it sits in, or a tag it carries, is still there and takes over the moment this
		 * is applied. A panel saying "Not shared" whose row comes back after Apply saying Shared
		 * would be describing a state that never existed.
		 */
		if (chosen === 'private') return elsewhere(user);
		const decisive = sources.find((source) => source.subject_user_id === user.id && source.decides);
		if (decisive) return decisive.effect === 'restrict' ? 'restricted' : 'shared';
		return readingOf(recorded, user.id);
	}

	/** The two words as the effect that produced them. Anything else decides nothing. */
	function wordToEffect(word: Reading): Effect | null {
		if (word === 'shared') return 'share';
		if (word === 'restricted') return 'restrict';
		return null;
	}

	/**
	 * What would still reach this user with nothing written on the thing itself.
	 *
	 * The resolver's ladder with its first rung removed: any restrict, then any share, then nothing.
	 * It is a preview and it is allowed to be one: the panel re-reads from the server the next
	 * time it opens, and the server is what decides.
	 */
	function elsewhere(user: ShareableUser): Reading {
		const reaching = sources.filter((source) => source.subject_user_id === user.id && !source.here);
		if (reaching.some((source) => source.effect === 'restrict')) return 'restricted';
		if (reaching.length > 0) return 'shared';
		return 'private';
	}

	/**
	 * Where this user's answer comes from, in words, one line per grant.
	 *
	 * Restricts first, because a restrict is what beats things: somebody looking at a file they
	 * thought they had shared wants that line at the top. Everything that reaches the file is listed
	 * rather than only the decisive one: the share underneath a restrict is the answer to what
	 * happens when the restrict comes off, which is the next question every time.
	 */
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
				/*
				 * A decision staged on the thing itself is about to replace what is written there,
				 * so the line describing the current one goes: listing a share being removed, in
				 * the colour of something in force, would describe a state nobody asked for.
				 */
				.filter((source) => !(chosen !== undefined && source.here))
				// What is in force first, then the rest narrowest-first: the order the resolver reads
				// them in, so the list can be read downwards as the reason for the answer.
				.sort((a, b) => Number(b.decides) - Number(a.decides) || breadthOf(a) - breadthOf(b))
				.map((source) => {
					// With something staged, what decides is what the row is about to say: the server's
					// answer describes the grants as they are now, not as Apply will leave them.
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
	 * Press one of the two buttons.
	 *
	 * Three states, two buttons, mutually exclusive: Shared, Restricted, or neither, which is
	 * Not shared. Pressing the one already on turns it off; pressing the other moves straight
	 * across. A mixed row is not "already on" either way, so the first press makes the whole
	 * selection agree.
	 *
	 * Both at once is not offered: a restrict beats every share, so the share underneath would do
	 * nothing, look like it did, and quietly come into force the day the restrict was lifted.
	 *
	 * Two buttons rather than a menu: two buttons show both answers at once, where a menu hides the
	 * one not chosen.
	 */
	function choose(user: ShareableUser, word: Standing) {
		// Against what is recorded HERE, not against the resolved answer. Pressing Share on a file
		// that is reachable through its folder has to write a share on the file, not try to undo one
		// that was never there.
		const desired: Standing = localReading(user) === word ? 'private' : word;
		const stored = readingOf(recorded, user.id);
		// Choosing back what is already recorded is not a change. Dropping it keeps the count
		// honest, so Apply is offered when there is something to apply and not otherwise.
		const next = { ...staged };
		if (desired === stored) delete next[user.id];
		else next[user.id] = desired;
		staged = next;
	}

	/**
	 * Take one of the named things back out of the vault, from the line that named it.
	 *
	 * The line above it is the reason this is worth having: it says which folder, person or tag is
	 * doing the hiding, and without it the only way to act on that is to go and find the thing.
	 *
	 * Nothing else in this panel is written before Apply, and this deliberately is: it is not a
	 * grant, there is nothing to weigh it against, and staging it would leave the block describing
	 * a state that is not true yet beside rows that are.
	 */
	async function takeOut(source: VaultSource) {
		unhiding = source.source_id;
		try {
			await unhide(source);
			concealing = concealing.filter((each) => each.source_id !== source.source_id);
			// Whatever came back out is on somebody's screen again now, and if this was the last
			// thing hiding the file, the file is too.
			libraryChanges.changed();
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
		// Read before the staging is cleared, because it is what the sentence afterwards counts.
		const changed = changes;
		saving = true;
		try {
			for (const [subjectUserId, desired] of Object.entries(staged)) {
				// One at a time rather than all at once. These are small writes and there are at most a
				// few users times a selection of them; firing them together would make the failure
				// case "some of it happened, in no particular order" for no gain anybody can see.
				for (const [index, target] of targets.entries()) {
					await put(target, subjectUserId, desired, recorded[index] ?? []);
				}
			}
			// Every screen made of tiles re-reads. Two things move: the badge in the corner of a tile,
			// and, for whoever it was about, whether the thing is on their screen at all. Without
			// this the change would show at the next reload, and a file could leave the grid while
			// staying on Recently viewed.
			libraryChanges.changed();
			staged = {};
			open = false;
			// Said out loud, like every other verb that finishes. Sharing is the one action here
			// whose whole effect is on somebody else's screen: nothing on this one moves, so with no
			// sentence the panel would simply close and the only way to be sure would be to open it again.
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

<!--
	The thing a line is pointing at, and a way to it where there is one.

	A link rather than a word wherever the thing has a page: reading "Shared by the person Reya Solberg"
	and then having to go and find her is a step the sentence already knows how to save. It closes
	the panel on the way, because a dialog left standing over the screen it just sent you to is a
	dialog you have to dismiss before you can look at what you asked for.

	A folder and a library get the plain word. They live in the tree inside Settings, which is a panel
	over whatever you were on rather than an address. See `sourceHref`, which is where that is
	decided once for everywhere.
-->
{#snippet named(type: ShareableType, id: string | null, label: string)}
	{@const href = sourceHref(type, id)}
	{#if href}
		<a class="named" {href} onclick={() => (open = false)}>{label}</a>
	{:else}
		<span class="named">{label}</span>
	{/if}
{/snippet}

<!-- A filename is not a sentence and can be wider than this sheet: `Modal` breaks it wherever it has
     to, on every sheet, and shows all of it, uncapped (see the rule there). -->
<Modal
	bind:open
	onOpenChange={(next) => !next && (staged = {})}
	title="Sharing"
	description={subject}
	sheetClass="share-sheet"
	scrolls={false}
>
	<!--
							What is hiding this, above the sharing rows because it outranks them.

							Hidden is not a share pointed the other way and it is not Restricted either: it
							withholds the thing from YOU, and no share you hold overrides it. So a panel
							showing "Shared with Reya" over a file you cannot see is telling the truth
							about a decision that is still in force for Reya and doing nothing for you,
							and this is the line that says so. What is listed here is what you hid; to keep
							something from Reya, restrict it.

							The same fill rule as the marks: solid where the switch is on this very thing
							(change it here) and hollow where it is on something above, which is where to go.
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
						<!-- On the line that names it, because each line is a different thing:
											     unhiding the folder is not unhiding the file, and one button at the
											     foot of the block would have to guess which was meant. -->
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

	<!--
		What ELSE this decision covers, above the rows because it changes what every one of them
		means. A site can be part of another site, and the files are filed under the Sites within rather
		than under the network, so a share made here hands over everything underneath and the heading
		names only the network.
	-->
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
		<!-- Not an error, and said as the fact it is. An install with no guests on it is
							     the ordinary state of a Sift, and the person reading this needs to know where
							     guests come from rather than that something went wrong. -->
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
						<!-- Reachable, but not from here. The button is dark, because pressing it would
						     write something new: it wears a tint instead, so the row does not look
						     untouched when it plainly is not. -->
						{@const reachedShare = !shared && word === 'shared'}
						{@const reachedRestrict = !restricted && word === 'restricted'}
						<!-- Recorded on this thing and BEATEN by something that outranks it (a restrict on
						     a person, tag, site or collection outranks a grant on the item itself). Drawn
						     the way the reason lines draw the same fact: quiet, not claiming to be in force. -->
						{@const beatenShare = shared && word !== 'shared'}
						{@const beatenRestrict = restricted && word !== 'restricted'}
						<!-- The word beside the name is the OUTCOME; the choice is what is set HERE. A
						     restrict on a person, tag, site or collection outranks a grant on the item
						     itself, so the two can differ, and the reason lines say why. -->
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

								<!-- Where the answer came from. Absent when nothing reaches this user, which
												     is what Private means, and absent across a selection where the honest answer
												     would be a different list for every file. -->
								<!-- POSITION is part of the key. `lead` is the same words for every source of one kind
									     and a name is not unique: two collections can both be called the same thing,
									     and two grants of one kind can both have no name at all. -->
								{#each reasons(user) as reason, at (`${at}:${reason.verb}${reason.lead}${reason.name ?? ''}`)}
									<!-- A grant that lost is faded and says why on hover. It is worth
													     showing (it is what happens when the thing beating it is
													     lifted) and it must not read as though it is in force. -->
									{#if reason.decides}
										<span class="reason">
											<!-- Two words carry weight and the sentence between them does not:
															     the VERB, which is what happened, and the NAME, which is what
															     it happened on. A whole line in red reads as an error; one
															     word in it reads as a fact. The name wears the same ink as the
															     filename at the top of the panel, because it is the same kind
															     of thing: a thing being pointed at. -->
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

							<!-- Each button says the act; whether it is in force is its fill, and the word
							     under the name says the outcome in words. A button's words are the act it
							     does, so the state lives in the fill and in the word beside the name. -->
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

		<!-- Under the rows: what Restrict promises, and
		     how one file of a restricted folder is opened. -->
		<p class="note">
			A guest set to Restricted never sees this, even if you later share a folder or tag that
			includes it.
		</p>
		<p class="note">
			To share one file from a restricted folder, take the restriction off the folder first.
		</p>
	{/if}

	<div class="buttons">
		<!--
			ONE BUTTON WHEN THERE IS NOTHING TO DECIDE, and it is not tidying.

			Every staged change in this panel is a decision about a guest user. With no guest
			users on the install there is nothing that can be staged, so Cancel and Apply would be two
			buttons doing exactly the same nothing, and the primary one, the one the eye goes to,
			would promise an action that could not happen. A panel that is only telling you something has
			one way out and it says what it does.
		-->
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
	/* Wider than a confirm, because it holds a list rather than a sentence. */
	:global(.share-sheet) {
		/* The width only. `.sheet` clamps it to the window. See app.css. */
		--sheet-inline: 460px;
	}

	/*
	 * The cap is on the box that SCROLLS, reached through a class of this file's own.
	 *
	 * Not on the `<ul>` INSIDE the scroller: a ceiling with no overflow does not scroll, it spills,
	 * and with enough guest users the list paints out of the sheet and over whatever is under it,
	 * which is indistinguishable from a dialog whose buttons do not work.
	 *
	 * Anchored to `.rows-box` rather than written as a bare `:global(.scroll-root)`, because that
	 * would be a rule about every scrolling box in the application, and it would take effect the
	 * moment anything loaded this file's stylesheet.
	 */
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

	/* Where the answer came from: "Restricted the folder Holiday", "Shared the tag beach".
	   Quieter than the word above it, because it is the explanation rather than the answer. */
	.reason {
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	/* Beaten: the whole line steps back to the ink the explanation under the rows is written in, and
	   the verb loses its colour with it. A green "Shared" on a line that is doing nothing is the one
	   thing this panel must not draw. */
	.reason.beaten,
	.reason.beaten .verb,
	.reason.beaten .named {
		color: var(--sift-ink-3);
	}

	/*
	 * What is hiding this, drawn as a block above the users rather than as another row.
	 *
	 * Above them because it outranks them on YOUR screen: what you hide is gone for you whatever
	 * is shared, so a share
	 * underneath is a decision that is not currently doing anything. Its own box, in the panel's
	 * quieter surface, so it reads as a fact about the thing rather than as a control: there is
	 * nothing here to press, and the way to undo it is on whatever the line names.
	 */
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

	/* The sentence under the list, in the same ink as the explanations under the rows. Indented to
	   the words above it rather than to the glyph, so the block reads as lines of text with one
	   icon column beside them instead of a second column starting underneath the first. */
	.hidden-by .explains {
		color: var(--sift-ink-3);
		padding-block-start: var(--space-1);
		padding-inline-start: calc(16px + var(--space-2));
	}

	.hidden-verb {
		color: var(--sift-ink);
	}

	/*
	 * The thing being pointed at, and it is the part that carries the weight.
	 *
	 * A line reads "Shared by the person Reya Solberg" and has three parts, of which only two are the
	 * answer: what happened, and what it happened ON. The words joining them stay quiet. Weight on
	 * the VERB would be the wrong half: every line in the block starts with the same handful
	 * of words, so bolding them emphasises the part that never varies and leaves the one specific
	 * fact (which person, which folder) reading as an aside. The verb keeps its colour, which is
	 * carrying a different signal entirely and is the one thing here that must not move.
	 */
	.named {
		font-weight: 600;
		color: var(--sift-ink);
	}

	/* A link, and drawn as one only when the pointer is on it: a line of prose with a permanent
	   underline through the middle of it reads as decoration rather than as a sentence. */
	a.named {
		text-decoration: none;
		border-radius: var(--radius-sm);
		/* The change steps over --dur-instant rather than happening between frames. */
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

	/* The way out, on the line that names what is doing the hiding. Quiet: it sits inside a block
	   that is explaining something, and a loud button there would read as the point of the block.

	   The button is pushed over by the SENTENCE growing, not by spreading the line out: with
	   `space-between` on the row the glyph, the words and the button would be three things each
	   shoved into their own corner, the icon on its own far from the sentence it belongs to. */
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

	/* The current answer in words, beside the choice that changes it. Two people reading the same
	   row (one who knows what the choice means and one who does not) get the same answer. */
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

	/*
	 * The two answers a row offers. `:global` because the classes are handed to the shared button
	 * and land on an element compiled in its file. The button brings the shape, ground, disabled
	 * fade and focus ring; what is left here is about this row: one width for both, so the row does
	 * not jump when a button lights, and the states this control has that a plain button does
	 * not.
	 */
	.choices :global(.choice) {
		min-inline-size: var(--choice-width);
	}

	/* In force, but from somewhere else. A tint rather than the full colour: the state is real and
	   the button is not what holds it, so it must not look like the thing to press to undo it. */
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

	/* Set HERE, on this row. The full colour, because this is the button that undoes it. */
	.choices :global(.choice[aria-pressed='true']) {
		border-color: transparent;
		background: var(--sift-accent);
		color: var(--primary-foreground);
	}

	/* A restrict in force is worth noticing from across the room: it is the one that is a promise.
	   The label token for THIS fill: white on the red is 3.29:1. */
	.choices :global(.choice.restrict[aria-pressed='true']) {
		background: var(--sift-bad);
		color: var(--destructive-foreground);
	}

	/* Recorded here and overruled: the quiet the reason lines take for the same fact. */
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

	/* Ordinary ink rather than the quiet grey the notes wear: this is not an aside about the panel,
	   it is what the decision about to be made actually covers. */
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
