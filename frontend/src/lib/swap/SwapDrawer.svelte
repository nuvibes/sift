<script lang="ts">
	/* NOT ON THE GALLERY: it starts a real swap. The gallery draws the `Drawer` it stands in, with
	   picks in it (`drawer`). */
	/*
	 * Swap mode's drawer: what has been picked, and the press that starts the swap from it.
	 *
	 * Beside the page rather than over it (`Drawer`'s `beside`), because the page is where the
	 * picking happens: every press on a tile or a card while the mode is on adds to this list or
	 * takes out of it. It is open exactly while the mode is on, and the mode is left from here or
	 * from the same control in the Add button's panel that entered it.
	 *
	 * The picks are grouped by kind, in the order the Start a swap screen lists its pickers, and
	 * each can be taken out here as well as by pressing it again on the wall.
	 *
	 * Each pick is drawn as the chip the player's Enrichment rows draw for the same thing: the
	 * entity's own picture, a link to its page, and the card that opens on hover, from the same
	 * pieces (`EntityPreview`, `entityPicture`, `pageOf`). A person picked here and a person on a
	 * file are one thing, so they look like one thing, and pressing either opens them. A file has no
	 * page of its own: its chip opens the file, at the address every file opens at.
	 *
	 * Starting the swap is Send, Receive or Exchange, like the Swap page's three doors: Send is the
	 * picks, the tunnel that hosts and Start; Receive is the join, a pasted token, a folder and the
	 * tunnel this side dials through; Exchange is Send with a folder for what they send back, or,
	 * when they got the token, the join with the picks kept for what this side sends them
	 * (`keepExchangePicks`). Each is complete where it is, so nobody has to leave the drawer for the
	 * page to do the other half.
	 */
	import { goto } from '$app/navigation';
	import { Button, Chip, ChipRow, Drawer, SectionHeading, Tabs } from '$lib/components/common';
	import { phoneWidth } from '$lib/components/common/phone-width.svelte';
	import EntityPreview, { entityPicture } from '$lib/components/EntityPreview.svelte';
	import JoinSwap from '$lib/components/swap/JoinSwap.svelte';
	import SwapLaunch from '$lib/components/swap/SwapLaunch.svelte';
	import { pageOf, type EntityKind } from '$lib/entity/related.svelte';
	import type { SwapStarted } from '$lib/components/swap/swap';
	import type { IconName } from '$lib/design/icons';
	import { reloadOnLibraryChange } from '$lib/library/changes.svelte';
	import { askNames, nameNow } from '$lib/entity/names-now.svelte';
	import { untrack } from 'svelte';
	import { swapMode, type SwapKind } from './mode.svelte';
	import { keepExchangePicks } from './exchange';

	// Who the mode belongs to and when it ends are `SwapModeKeeper`'s, mounted outside the shell
	// this drawer stands in, so a sign-out or a lock that takes the shell down still reaches them.

	/** Each kind's heading and glyph, in the order they are listed. */
	const KINDS: readonly { kind: SwapKind; heading: string; icon: IconName }[] = [
		{ kind: 'person', heading: 'People', icon: 'person' },
		{ kind: 'site', heading: 'Sites', icon: 'public' },
		{ kind: 'tag', heading: 'Tags', icon: 'shoppingmode' },
		{ kind: 'collection', heading: 'Collections', icon: 'box' },
		{ kind: 'photo_set', heading: 'Photo Sets', icon: 'photo_library' },
		/* The songs picked, under the Music page's own word and glyph. */
		{ kind: 'song', heading: 'Music', icon: 'music_note_2' },
		{ kind: 'asset', heading: 'Files', icon: 'article' }
	];

	/* The kinds with a page that a swap can carry: every one, a song among them. */
	type SwappedEntity = Extract<EntityKind, SwapKind>;

	const ENTITIES: ReadonlySet<string> = new Set<SwappedEntity>([
		'person',
		'site',
		'tag',
		'collection',
		'photo_set',
		'song'
	]);

	/** The kind as an entity with a page and a picture, or null for a file (or any pick that is
	 *  not one): those are drawn as a plain chip, a file's opening the file. */
	function entityOf(kind: SwapKind): SwappedEntity | null {
		return ENTITIES.has(kind) ? (kind as SwappedEntity) : null;
	}

	/* What each picked thing is called NOW. A pick keeps its id and the name it had when it was
	   pressed, and that copy lives as long as the tab: a Site renamed on its own page would go on
	   reading its old name here for the rest of the session. So the names are asked for whenever
	   the picks change and whenever the library does, and the copy is drawn only until the first
	   answer lands (and for a thing the answer does not name: gone, or never this account's). */
	function askForNames(): void {
		for (const kind of ENTITIES) {
			const ids = swapMode.picks.filter((pick) => pick.kind === kind).map((pick) => pick.id);
			if (ids.length > 0) void askNames(kind as SwappedEntity, ids);
		}
	}
	$effect(() => {
		if (swapMode.picks.length === 0) return;
		untrack(askForNames);
	});
	reloadOnLibraryChange(askForNames);

	function calledNow(kind: SwappedEntity, pick: { id: string; name: string }): string {
		return nameNow(kind, pick.id) ?? pick.name;
	}

	const groups = $derived(
		KINDS.map((one) => ({
			...one,
			picks: swapMode.picks.filter((pick) => pick.kind === one.kind)
		})).filter((one) => one.picks.length > 0)
	);

	function started(session: SwapStarted): void {
		swapMode.leave();
		void goto(`/swap?${new URLSearchParams({ session: session.session_id })}`);
	}

	/** Send the picks, receive what somebody else sends, or both: the Swap page's three doors. */
	const DIRECTIONS = [
		{ id: 'send', label: 'Send' },
		{ id: 'receive', label: 'Receive' },
		{ id: 'both', label: 'Exchange' }
	];
	let direction = $state('send');
	/* What the picks offer, as the server is sent them: one array per change of the picks, not one
	   per read, so the weigh's wait for the picks to settle sees one change for one pick. */
	const picked = $derived(swapMode.chosen());
	/* Under Exchange: pasting the token they got, rather than getting one. */
	let pasting = $state(false);

	/* The doors are drawn once the mode has been entered, and kept after, so the drawer closes
	   with them still in it. Mounted with the drawer shut, on every page of every account, they
	   would ask the tunnels and the download folders at each load: work nobody asked for, and for
	   a guest refusals in the console, since swapping is an admin's. */
	let entered = $state(false);
	$effect(() => {
		if (swapMode.on) entered = true;
	});

	function joined(sessionId: string): void {
		swapMode.leave();
		void goto(`/swap?${new URLSearchParams({ session: sessionId })}`);
	}

	/* Joined under Exchange: the picks are what this side sends them, so they are kept for the
	   session before leaving the mode takes them away. */
	function joinedExchange(sessionId: string): void {
		keepExchangePicks(
			sessionId,
			swapMode.picks.map(({ kind, id, name }) => ({ kind, id, name }))
		);
		swapMode.leave();
		void goto(`/swap?${new URLSearchParams({ session: sessionId, exchange: '1' })}`);
	}
</script>

<!--
	At a phone's width the drawer rises from the foot rather than standing at the right. Beside the
	page at the right it would take nearly the whole width and leave the wall a column too narrow
	for a file to be pressed. From the foot it leaves the wall
	its whole width and the top half of the screen, with the page still live to pick from. The half is
	this drawer's own height (`--drawer-height`, set on the box around it), because a bottom sheet
	elsewhere is most of the screen and this one has to leave room to pick.
-->
<div class="swap-drawer">
	<Drawer open={swapMode.on} label="Swap" beside side={phoneWidth.yes ? 'bottom' : 'right'}>
		<!-- One group per kind and one for starting the swap, a gap between groups: a heading
		     owns only the space under it, so without this each heading would stand on the
		     chips above it. -->
		<div class="groups">
			{#if swapMode.picks.length === 0}
				<p class="lede">
					Press files, People, Sites, tags, Collections, Photo Sets and songs to add them to the
					swap. Press one again to remove it.
				</p>
			{:else}
				{#each groups as group (group.kind)}
					<section>
						<SectionHeading>{group.heading}</SectionHeading>
						<ChipRow>
							{#each group.picks as pick (pick.id)}
								{@const kind = entityOf(pick.kind)}
								{#if kind === null}
									<span class="hovered">
										<Chip
											tone="quiet"
											icon={group.icon}
											href={pick.kind === 'asset' ? `/asset/${pick.id}` : undefined}
											onremove={() => swapMode.drop(pick.kind, pick.id)}
											removeLabel="Remove {pick.name}">{pick.name}</Chip
										>
									</span>
								{:else}
									{@const called = calledNow(kind, pick)}
									<EntityPreview {kind} id={pick.id} name={called}>
										{#snippet children({ props })}
											<span {...props} class="hovered">
												<Chip
													tone="quiet"
													href={pageOf(kind, pick.id)}
													picture={entityPicture(kind, pick.id, called)}
													onremove={() => swapMode.drop(kind, pick.id)}
													removeLabel="Remove {called}">{called}</Chip
												>
											</span>
										{/snippet}
									</EntityPreview>
								{/if}
							{/each}
						</ChipRow>
					</section>
				{/each}
			{/if}

			{#if entered}
				<section>
					<SectionHeading
						>{direction === 'both' ? 'Start the exchange' : 'Start the swap'}</SectionHeading
					>
					<Tabs
						look="segmented"
						size="panel"
						tabs={DIRECTIONS}
						bind:value={direction}
						label="Send or receive"
					>
						{#snippet pane(which)}
							<!-- Only the pane on show is drawn. `Tabs` draws every pane in its travelling
							     row, so each one's form would be mounted at the same time: one pick weighed by the
							     Send and the Exchange panes both, and the tunnels read once per pane. -->
							{#if which !== direction}
								<!-- Not on show: nothing asked for. -->
							{:else if which === 'receive'}
								<JoinSwap framed={false} onjoined={joined} />
							{:else if which === 'both' && pasting}
								<p class="lede">
									Paste the token they sent you. What you picked is what you send them, once you've
									compared the code.
								</p>
								<JoinSwap framed={false} exchange onjoined={joinedExchange} />
								<div class="instead">
									<Button size="small" tone="quiet" onclick={() => (pasting = false)}
										>Start the exchange instead</Button
									>
								</div>
							{:else}
								<SwapLaunch chosen={picked} onstarted={started} both={which === 'both'} />
								{#if which === 'both'}
									<div class="instead">
										<Button size="small" tone="quiet" onclick={() => (pasting = true)}
											>Join their exchange instead</Button
										>
									</div>
								{/if}
							{/if}
						{/snippet}
					</Tabs>
				</section>
			{/if}
		</div>

		{#snippet footer()}
			<!-- Says both things it does: the picks go with the mode. -->
			<Button tone="quiet" onclick={() => swapMode.leave()}>Deselect all and leave swap mode</Button
			>
		{/snippet}
	</Drawer>
</div>

<style>
	/* A box for the drawer's height to be set on, and nothing else: it draws no box of its own. */
	.swap-drawer {
		display: contents;
	}

	@media (max-width: 767px) {
		.swap-drawer {
			--drawer-height: 50%;
		}
	}

	.groups {
		display: flex;
		flex-direction: column;
		gap: var(--space-6);
	}

	/* A column, as the drawer's own content box is: a chooser in a group stretches to the
	   drawer's width. */
	.groups section {
		display: flex;
		flex-direction: column;
	}

	/* The hover card's trigger, wrapping the chip, held to the width the player's Enrichment rows
	   hold the same chip to, so a long name is cut the same way in both. */
	.hovered {
		display: flex;
		max-inline-size: 16ch;
	}

	.lede {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}

	/* Exchange's other half, offered under the half on show: a press, at the right. */
	.instead {
		display: flex;
		justify-content: flex-end;
		margin-block-start: var(--space-3);
	}
</style>
