<script lang="ts">
	/*
	 * ONE RECAP: "September, in 8 cards", as a story read a card at a time.
	 *
	 * A page with an address, so a recap can be opened again next month from the Recaps list, from
	 * a link, or from the browser's own history, not a dialog that is gone once it is closed. The
	 * cards are read in order, one on screen at a time, the way a year in review is: the time
	 * viewed, the top person with her portrait, the top five, the top Site and tag, the favourite
	 * time as a ring, Theater, what was organized, and last the closing card ("That was
	 * September.") with the way to the same period on Insights under it.
	 *
	 * ## Paging
	 *
	 * The bar of segments at the head says how far through the story the reader is; Earlier and
	 * Later (and the arrow keys) turn a card, and the next one slides in from the side it comes
	 * from, at the slow pace, its figure counting up as it lands: the one card in view is the one
	 * thing moving. Every card is still in the page for a screen reader, as a slide of a carousel,
	 * the ones not in view hidden until turned to.
	 *
	 * ## One card, one size
	 *
	 * Every card of a deck is the story's width by 16/9 of it, whatever it says, so Earlier, Save
	 * and Later stand still as the cards turn. On a window too short for that the width is taken
	 * once from the window, so the whole card and its controls are on screen together.
	 *
	 * ## A picture of a card, a set of them, the deck as a video
	 *
	 * Under the card, Save as picture paints the card in view again at 1080 wide (`share-card.ts`)
	 * and hands it to the door every screenshot takes. Offered only for a card that may be saved
	 * (`savable`). Under that, Save all as pictures does the same for every savable card still
	 * ticked, one file each, and a year's or a month's deck can be saved as a video
	 * (`deck-video.ts`). Each card is laid out for that on a stage nobody sees, one at a time,
	 * and painted from there. Square pictures lays each out as the board's square tile instead,
	 * the same card, for a picture 1080 square. The year's closing card offers Keep as
	 * Collections. Every card stands on the deck's own pictures (`groundOf`), on screen, in a
	 * picture and in the video alike.
	 *
	 * Leave out, before a picture is taken: the people and files the card in view names, any of
	 * them ticked to be taken out of this recap (`PUT .../left-out`, the whole list each time, so
	 * one unticked is put back). The deck is read again and every card naming one is drawn
	 * without it, on screen and in every picture after.
	 *
	 * ## What is decided here, and what is not
	 *
	 * Nothing about what is hidden. The server draws the recap for the reader's vault as it stands
	 * when it is asked: in the Show nothing mode a card that would name something hidden is not in
	 * the answer at all, in the placeholder mode it is a locked tile and the page carries the one
	 * line "Some of September is hidden. Unlock to include it." And the heading counts the cards
	 * actually sent. When the vault opens or shuts the recap is asked for again.
	 *
	 * Opening it ends its announcement, on Insights and on Browse's header at the same time: the
	 * server writes that when it draws the recap, and the shared list is told (`readRecap`).
	 *
	 * A recap is made to be shown a card at a time, and a card to be kept as a picture; what must
	 * not leave is kept back by `savable` (see `RecapCard`).
	 */
	import { tick, untrack } from 'svelte';

	import { page } from '$app/state';
	import { api, isMissing } from '$lib/api/client';
	import {
		BackButton,
		Button,
		Checkbox,
		Empty,
		Modal,
		Note,
		Problem,
		ProgressBar,
		Skeleton,
		Tooltip
	} from '$lib/components/common';
	import { thing } from '$lib/components/common/toast-pieces';
	import {
		keepAsCollections,
		readKeep,
		readSession,
		type KeepSheet,
		type SessionPath
	} from '$lib/components/insights/cards/kinds';
	import {
		encodeFilm,
		filmCard,
		filmName,
		filmOf,
		filmed,
		type Held
	} from '$lib/components/insights/deck-video';
	import { filesSaid } from '$lib/entity/entity-counts';
	import { session } from '$lib/shell/session.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import type { Crumb } from '$lib/components/common';
	import RecapCard, { savable } from '$lib/components/insights/RecapCard.svelte';
	import { groundOf } from '$lib/components/insights/cards/family';
	import { cardPicture, pictureName } from '$lib/components/insights/share-card';
	import { deliver } from '$lib/player/snapshot';
	import { INSIGHTS_WORDS } from '$lib/components/insights/words';
	import { arrive } from '$lib/shell/motion.svelte';
	import PageFrame from '$lib/components/shell/PageFrame.svelte';
	import PageHeader from '$lib/components/shell/PageHeader.svelte';
	import { reloadOnLibraryChange } from '$lib/library/changes.svelte';
	import { readRecap, type Recap } from '$lib/library/recaps.svelte';
	import { clock } from '$lib/shell/clock.svelte';

	/** The way to the same period on Insights, by the kind of period the recap is of. */
	const SEE_IT: Record<string, string> = {
		week: 'See this week in Insights',
		month: 'See this month in Insights',
		year: 'See this year in Insights'
	};

	const id = $derived(page.params.id ?? '');

	/* The card in view, and the side the last turn came from (1 forward, -1 back). */
	let at = $state(0);
	let turned = $state(1);
	let taking = $state(false);
	let recap = $state<Recap | null>(null);
	let missing = $state(false);
	let failed = $state(false);

	/** How far a card slides in from as it is turned to, in px: a card's width would be a lurch. */
	const TURN = 32;

	function turn(by: number): void {
		if (!recap) return;
		const next = Math.min(recap.cards.length - 1, Math.max(0, at + by));
		if (next === at) return;
		turned = by;
		at = next;
	}

	function onkeydown(event: KeyboardEvent): void {
		const target = event.target as HTMLElement | null;
		if (target?.closest('input, textarea, [contenteditable="true"]')) return;
		if (event.key === 'ArrowRight') turn(1);
		else if (event.key === 'ArrowLeft') turn(-1);
		else return;
		event.preventDefault();
	}

	const place = (index: number) => (recap ? `${index + 1} of ${recap.cards.length}` : '');

	/* The deck's own pictures, the ground every card of it stands on. */
	const ground = $derived(recap ? groundOf(recap.cards) : []);

	/* Pictures 1080 square rather than 1080 by 1920: each card laid out as the board's square tile. */
	let square = $state(false);
	const nameOf = (period: string, index: number) =>
		square
			? pictureName(period, index).replace(/\.png$/, '-square.png')
			: pictureName(period, index);

	let list = $state<HTMLElement | null>(null);
	let controls = $state<HTMLElement | null>(null);

	async function take(): Promise<void> {
		const card = recap?.cards[at];
		if (!recap || !card || !savable(card) || taking) return;
		taking = true;
		try {
			const drawn = square
				? await laidOut(at)
				: list?.querySelector<HTMLElement>('li:not([hidden]) .recap-card');
			const picture = drawn ? await cardPicture(drawn) : null;
			if (picture) await deliver(picture, nameOf(recap.period, at));
		} finally {
			staged = null;
			taking = false;
		}
	}

	/* THE STAGE: a card laid out where nobody sees it, to be painted as a picture or filmed. Within
	   the window, so its pictures load as they would on screen, and clipped to nothing. */
	let stage = $state<HTMLElement | null>(null);
	let staged = $state<number | null>(null);

	/* The longest a staged card waits for its pictures before it is painted without them. */
	const STAGE_WAIT_MS = 4000;

	async function laidOut(index: number): Promise<HTMLElement | null> {
		staged = index;
		await tick();
		await document.fonts?.ready;
		const card = stage?.querySelector<HTMLElement>('.recap-card') ?? null;
		/* A picture is put in once it has loaded (an `Avatar` waits on its own ground until then), a
		   beat after: painted before, it would be missing. A second at most. */
		for (let frame = 0; frame < 60 && card?.querySelector('.waiting'); frame += 1)
			await new Promise((next) => requestAnimationFrame(() => next(null)));
		const pictures = [...(card?.querySelectorAll('img') ?? [])];
		/* Nobody scrolls to the stage, so a picture waiting to be scrolled to would never load and
		   its painting would wait for ever: here every picture loads now. */
		for (const one of pictures) one.loading = 'eager';
		const settled = Promise.all(pictures.map((one) => one.decode?.().catch(() => undefined)));
		await Promise.race([settled, new Promise((late) => setTimeout(late, STAGE_WAIT_MS))]);
		await tick();
		return card;
	}

	/* The set: every card that may be saved, less the ones the reader unticked. */
	let untick = $state<Set<number>>(new Set());
	const savables = $derived(
		recap ? recap.cards.flatMap((card, index) => (savable(card) ? [index] : [])) : []
	);
	const picked = $derived(savables.filter((index) => !untick.has(index)));
	const leftOut = $derived(recap ? recap.cards.length - savables.length : 0);

	function pick(index: number, on: boolean): void {
		const next = new Set(untick);
		if (on) next.delete(index);
		else next.add(index);
		untick = next;
	}

	/* A browser stops a page's downloads after a burst of ten, so a set is saved a second apart. */
	const PACE_MS = 1100;
	let takingAll = $state<{ done: number; total: number } | null>(null);

	async function takeAll(): Promise<void> {
		if (!recap || taking) return;
		taking = true;
		takingAll = { done: 0, total: picked.length };
		try {
			for (const [n, index] of picked.entries()) {
				if (n > 0) await new Promise((wake) => setTimeout(wake, PACE_MS));
				const drawn = await laidOut(index);
				const picture = drawn ? await cardPicture(drawn) : null;
				// Untyped, so the clipboard (which holds one picture) refuses it and each lands
				// as a file of its own.
				if (picture) await deliver(new Blob([picture]), nameOf(recap.period, index));
				takingAll = { done: n + 1, total: picked.length };
			}
		} finally {
			staged = null;
			taking = false;
			takingAll = null;
		}
	}

	/* The video: frames drawn, then the server encoding them. */
	let filming = $state<{ done: number; total: number; encoding: boolean } | null>(null);

	async function film(): Promise<void> {
		if (!recap || filming) return;
		const page = getComputedStyle(document.body).backgroundColor;
		filming = { done: 0, total: savables.length, encoding: false };
		try {
			const frames: Held[] = [];
			for (const index of savables) {
				const drawn = await laidOut(index);
				const held = drawn ? await filmCard(drawn, recap.cards[index], page) : null;
				if (held) frames.push(...held);
				filming = { ...filming, done: filming.done + 1 };
			}
			staged = null;
			filming = { ...filming, encoding: true };
			const mp4 = await encodeFilm(recap.id, filmOf(frames));
			await deliver(mp4, filmName(recap.period));
		} catch {
			toasts.show("The video couldn't be saved", { tone: 'error' });
		} finally {
			staged = null;
			filming = null;
		}
	}

	/* LEAVE OUT: what the card in view names that may be taken out of the recap, and what is out. */
	type Named = { id: string; text: string };
	const LEAVABLE = new Set(['person', 'asset']);
	let takenOut = $state<string[]>([]);
	let leaving = $state<Named[] | null>(null);
	let leaveTicks = $state<Set<string>>(new Set());

	function namedOn(index: number): Named[] {
		const card = recap?.cards[index];
		if (!card || card.hidden) return [];
		const pieces = [...card.statement, ...(card.rows ?? []).map((row) => row.piece)];
		const found = new Map<string, Named>();
		for (const piece of pieces) {
			if (piece.id && piece.kind && LEAVABLE.has(piece.kind) && !piece.gone)
				found.set(piece.id, { id: piece.id, text: piece.text });
		}
		return [...found.values()];
	}

	function openLeave(): void {
		leaving = namedOn(at);
		leaveTicks = new Set(takenOut.filter((one) => leaving?.some((named) => named.id === one)));
	}

	function leaveTick(key: string, on: boolean): void {
		const next = new Set(leaveTicks);
		if (on) next.add(key);
		else next.delete(key);
		leaveTicks = next;
	}

	async function leave(): Promise<void> {
		if (!recap || !leaving) return;
		const shown = new Set(leaving.map((one) => one.id));
		const ids = [...takenOut.filter((one) => !shown.has(one)), ...leaveTicks];
		try {
			await api.put(`/insights/recaps/${encodeURIComponent(recap.id)}/left-out`, {
				body: { ids }
			});
			takenOut = ids;
			leaving = null;
			await read(id);
		} catch {
			toasts.show("This recap couldn't be drawn again", { tone: 'error' });
		}
	}

	/* The longest session's pages, for the card that says how one session went. */
	let sessionPath = $state<SessionPath | null>(null);

	/* Keep as Collections: the year's closing card, an admin's press. */
	const keeps = $derived(
		recap?.period.startsWith('year:') === true &&
			recap.cards[at]?.kind === 'closing' &&
			session.isAdmin
	);
	let sheet = $state<KeepSheet | null>(null);
	let keepTicks = $state<Set<string>>(new Set());
	let keeping = $state(false);

	async function openKeep(): Promise<void> {
		if (!recap) return;
		try {
			sheet = await readKeep(recap.id);
			keepTicks = new Set(
				sheet.lists.filter((one) => one.ticked && !one.kept).map((one) => one.key)
			);
		} catch {
			toasts.show("Keep as Collections couldn't be opened", { tone: 'error' });
		}
	}

	function keepTick(key: string, on: boolean): void {
		const next = new Set(keepTicks);
		if (on) next.add(key);
		else next.delete(key);
		keepTicks = next;
	}

	async function keep(): Promise<void> {
		if (!sheet || keeping) return;
		keeping = true;
		try {
			const kept = await keepAsCollections(sheet.lists.filter((one) => keepTicks.has(one.key)));
			sheet = null;
			const named = kept.flatMap((one, n) => [
				n ? ', ' : '',
				thing('collection', one.id, one.name)
			]);
			toasts.show(kept.length ? ['Sift created ', ...named] : 'Nothing new to keep', {
				tone: 'success'
			});
		} catch {
			toasts.show("The Collections couldn't be created", { tone: 'error' });
		} finally {
			keeping = false;
		}
	}

	/* The deck's width where the window is too short for a whole card at the story's width: what
	   is left under the card's top, less the controls, at 9:16. Measured when a deck opens and
	   when the window changes, never between two cards. */
	let fitted = $state<string | undefined>(undefined);

	function fit(): void {
		if (!list || !controls) return;
		const card = list.getBoundingClientRect();
		const room = window.innerHeight - card.top;
		/* The controls and the gap over them, and as much again under them. */
		const below = 2 * (controls.getBoundingClientRect().bottom - card.bottom);
		fitted = `${Math.floor(((room - below) * 9) / 16)}px`;
	}

	$effect(() => {
		if (recap && list && controls) untrack(fit);
	});

	/* Answers are numbered so a slow one for an address already left cannot land on the next. */
	let asked = 0;

	async function read(which: string): Promise<void> {
		const mine = ++asked;
		try {
			const got = await readRecap(which);
			if (mine !== asked) return;
			recap = got;
			takenOut = (got as Recap & { left_out?: string[] }).left_out ?? takenOut;
			at = Math.min(at, Math.max(0, got.cards.length - 1));
			sessionPath = got.cards.some((card) => card.kind === 'session' && !card.hidden)
				? await readSession(got.id).catch(() => null)
				: null;
			missing = false;
			failed = false;
		} catch (error) {
			if (mine !== asked) return;
			if (isMissing(error)) {
				recap = null;
				missing = true;
			} else {
				failed = true;
			}
		}
	}

	$effect(() => {
		const which = id;
		untrack(() => {
			recap = null;
			at = 0;
			missing = false;
			failed = false;
			void read(which);
		});
	});

	/* The vault opened or shut, or something was hidden or shared: which cards this reader is shown
	   has moved, and only the server knows what it moved to. */
	reloadOnLibraryChange(() => void read(id));

	/* The cards say a time of day on the reader's clock, so a change of clock asks again. */
	let clockSeen = untrack(() => clock.hours);
	$effect(() => {
		const now = clock.hours;
		if (now === clockSeen) return;
		clockSeen = now;
		untrack(() => void read(id));
	});

	/* The same period on Insights: a recap of a period says which, and its first day is inside it. */
	const kind = $derived(recap?.period.split(':')[0] ?? '');
	const onInsights = $derived(
		recap && recap.first_day && SEE_IT[kind]
			? {
					label: SEE_IT[kind],
					href: `/insights?period=${kind}&at=${encodeURIComponent(recap.first_day)}`
				}
			: null
	);

	const crumbs = $derived<Crumb[]>([
		{ label: 'Insights', href: '/insights' },
		{ label: 'Recaps', href: '/insights/recaps' },
		{ label: recap?.title ?? 'Recap' }
	]);
</script>

<svelte:head><title>{recap?.title ?? 'Recap'}</title></svelte:head>
<svelte:window onresize={fit} />

<PageFrame {crumbs}>
	{#snippet header()}
		<PageHeader title={recap?.heading ?? 'Recap'}>
			{#snippet lede()}{recap?.span ?? ''}{/snippet}
		</PageHeader>
	{/snippet}

	{#if missing}
		<Empty scope="page">There's no recap here.</Empty>
		<BackButton to="/insights" label="Insights" />
	{:else if failed}
		<Problem message="This recap couldn't be opened." />
	{:else if !recap}
		<Skeleton shape="block" />
	{:else}
		<div class="recap">
			{#if recap.hidden_line}
				<Note icon="visibility_off">{recap.hidden_line}</Note>
			{/if}

			<!-- The arrow keys turn a card while the story has the focus: the carousel's own keys, not
			     an app-wide shortcut. -->
			<!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
			<section
				class="story"
				aria-roledescription="carousel"
				aria-label={recap.heading}
				tabindex="0"
				style:--deck-width={fitted}
				{onkeydown}
			>
				<div class="progress" aria-hidden="true">
					{#each recap.cards as card, index (`${card.id}-${index}`)}
						<span class="segment" class:read={index <= at}></span>
					{/each}
				</div>
				<ol class="cards" bind:this={list}>
					{#each recap.cards as card, index (`${card.id}-${index}`)}
						<li
							role="group"
							aria-roledescription="slide"
							aria-label={place(index)}
							hidden={index !== at}
						>
							{#if index === at}
								<div class="turned" in:arrive={{ x: turned * TURN, pace: 'slow' }}>
									<RecapCard
										{card}
										heading={recap.title}
										place={place(index)}
										foot={recap.span}
										session={sessionPath}
										{ground}
									/>
								</div>
							{/if}
						</li>
					{/each}
				</ol>
				<div class="controls" bind:this={controls}>
					<Tooltip label={INSIGHTS_WORDS.earlier}>
						<Button
							icon="chevron_left"
							tone="ghost"
							aria-label={INSIGHTS_WORDS.earlier}
							disabled={at === 0}
							onclick={() => turn(-1)}
						/>
					</Tooltip>
					<Tooltip label="Remove people and files this card names from this recap">
						<Button tone="ghost" disabled={namedOn(at).length === 0 || taking} onclick={openLeave}
							>Leave out</Button
						>
					</Tooltip>
					<Tooltip label="A picture of this card, where your screenshots go">
						<Button
							icon="save"
							tone="ghost"
							disabled={!savable(recap.cards[at]) || taking}
							onclick={() => void take()}>Save as picture</Button
						>
					</Tooltip>
					<Tooltip label={INSIGHTS_WORDS.later}>
						<Button
							icon="chevron_right"
							tone="ghost"
							aria-label={INSIGHTS_WORDS.later}
							disabled={at === recap.cards.length - 1}
							onclick={() => turn(1)}
						/>
					</Tooltip>
				</div>
				<div class="deck-saves">
					<Checkbox
						state={savable(recap.cards[at]) && !untick.has(at) ? 'on' : 'off'}
						disabled={!savable(recap.cards[at])}
						label="This card in the set"
						onchange={(next) => pick(at, next === 'on')}
					/>
					<span class="pick" aria-hidden="true">This card in the set</span>
					<Checkbox
						state={square ? 'on' : 'off'}
						disabled={taking || filming !== null}
						label="Square pictures"
						onchange={(next) => (square = next === 'on')}
					/>
					<span class="pick" aria-hidden="true">Square pictures</span>
					<Button
						icon="photo_library"
						tone="ghost"
						disabled={picked.length === 0 || taking || filming !== null}
						onclick={() => void takeAll()}
						>{picked.length === savables.length
							? 'Save all as pictures'
							: `Save ${picked.length} as pictures`}</Button
					>
					{#if filmed(recap.period)}
						<Button
							icon="save"
							tone="ghost"
							disabled={savables.length === 0 || taking || filming !== null}
							onclick={() => void film()}>Save as video</Button
						>
					{/if}
					{#if keeps}
						<Button icon="bookmark_add" tone="ghost" onclick={() => void openKeep()}
							>Keep as Collections</Button
						>
					{/if}
				</div>
				{#if leftOut > 0}
					<p class="left-out">
						{savables.length} of {recap.cards.length}; {leftOut}
						{leftOut === 1 ? 'names' : 'name'} something hidden
					</p>
				{/if}
				{#if takingAll}
					<ProgressBar
						value={takingAll.done}
						max={takingAll.total}
						label={`Saving pictures: ${takingAll.done} of ${takingAll.total}`}
					/>
				{/if}
				{#if filming}
					<ProgressBar
						value={filming.encoding ? null : filming.done}
						max={filming.total}
						label={filming.encoding
							? 'Encoding the video'
							: `Drawing the video: ${filming.done} of ${filming.total} cards`}
					/>
				{/if}
			</section>

			<div
				class="stage"
				class:square={square && filming === null}
				bind:this={stage}
				aria-hidden="true"
				inert
			>
				{#if staged !== null && recap.cards[staged]}
					<RecapCard
						card={recap.cards[staged]}
						heading={recap.title}
						place={place(staged)}
						foot={recap.span}
						session={sessionPath}
						size={square && filming === null ? 'tile' : 'story'}
						shape="2x2"
						{ground}
						build={false}
					/>
				{/if}
			</div>

			<Modal
				open={sheet !== null}
				onOpenChange={(open) => {
					if (!open) sheet = null;
				}}
				title="Keep as Collections"
				description="Sift creates each one you tick as a Collection, named for the year."
			>
				{#snippet children()}
					<ul class="keep">
						{#each sheet?.lists ?? [] as one (one.key)}
							<li>
								{#if one.kept}
									<a href={`/collections/${one.kept}`}>{one.name}</a>
									<span class="kept">Already kept</span>
								{:else}
									<Checkbox
										state={keepTicks.has(one.key) ? 'on' : 'off'}
										label={one.name}
										onchange={(next) => keepTick(one.key, next === 'on')}
									/>
									<span>{one.name}</span>
									<span class="kept">{filesSaid(one.asset_ids.length)}</span>
								{/if}
							</li>
						{/each}
					</ul>
				{/snippet}
				{#snippet footer()}
					<Button disabled={keepTicks.size === 0 || keeping} onclick={() => void keep()}
						>Keep</Button
					>
				{/snippet}
			</Modal>

			<Modal
				open={leaving !== null}
				onOpenChange={(open) => {
					if (!open) leaving = null;
				}}
				title="Leave out"
				description="Sift draws this recap again without what you tick, in the deck and in its pictures."
			>
				{#snippet children()}
					<ul class="keep">
						{#each leaving ?? [] as one (one.id)}
							<li>
								<Checkbox
									state={leaveTicks.has(one.id) ? 'on' : 'off'}
									label={one.text}
									onchange={(next) => leaveTick(one.id, next === 'on')}
								/>
								<span>{one.text}</span>
							</li>
						{/each}
					</ul>
				{/snippet}
				{#snippet footer()}
					<Button icon="save" onclick={() => void leave()}>Save</Button>
				{/snippet}
			</Modal>

			{#if onInsights}
				<a class="see-it" href={onInsights.href}>{onInsights.label}</a>
			{:else}
				<BackButton to="/insights" label="Insights" />
			{/if}
		</div>
	{/if}
</PageFrame>

<style>
	.recap {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: var(--space-4);
		max-inline-size: var(--page-measure);
	}

	/* The story: its progress, the one card in view, and the controls under it, all one width,
	   so each card is that wide and 9:16 whatever it says. */
	.story {
		display: flex;
		flex-direction: column;
		gap: var(--space-4);
		inline-size: min(
			100%,
			var(--story-width),
			max(var(--story-width) * 2 / 3, var(--deck-width, var(--story-width)))
		);
	}

	.progress {
		display: flex;
		gap: var(--space-1);
	}

	/* A card's segment: faint until it has been read, then the accent's tint, over the fast pace. */
	.segment {
		flex: 1;
		block-size: var(--chart-mark-gap);
		border-radius: var(--radius-sm);
		background: var(--sift-surface-4);
		transition: background-color var(--dur-fast) var(--ease);
	}

	.segment.read {
		background: var(--sift-accent-tint-1);
	}

	.cards {
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.turned {
		display: grid;
	}

	.controls,
	.deck-saves {
		display: flex;
		align-items: center;
		justify-content: center;
		gap: var(--space-2);
	}

	.deck-saves {
		flex-wrap: wrap;
	}

	/* A tick's words beside it, as the sheets below set them. */
	.pick {
		margin-inline-end: var(--space-2);
		font: var(--text-label);
		color: var(--sift-ink-2);
	}

	.left-out {
		margin: 0;
		font: var(--text-label);
		color: var(--sift-ink-3);
		text-align: center;
	}

	/* Laid out at the story's width inside the window, so its pictures load, and drawn nowhere. */
	.stage {
		position: absolute;
		inset-block-start: 0;
		inset-inline-start: 0;
		inline-size: var(--story-width);
		clip-path: inset(50%);
		pointer-events: none;
	}

	/* A square tile: the card fills the stage both ways, a third wider than a story card so a
	   tile's type has the room a story's has. */
	.stage.square {
		inline-size: calc(var(--story-width) * 4 / 3);
		block-size: calc(var(--story-width) * 4 / 3);
	}

	.keep {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.keep li {
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}

	.kept {
		margin-inline-start: auto;
		font: var(--text-label);
		color: var(--sift-ink-3);
	}

	/* The way on, in the link face every quiet way on in Insights wears. */
	.see-it {
		inline-size: fit-content;
		border-radius: var(--radius-sm);
		font: var(--text-body);
		color: var(--sift-accent-text);
		text-decoration: underline;
		text-decoration-color: transparent;
		text-underline-offset: 3px;
		transition: text-decoration-color var(--dur-instant) var(--ease);
	}

	.see-it:hover,
	.see-it:focus-visible {
		text-decoration-color: currentColor;
	}
</style>
