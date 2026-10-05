<script lang="ts">
	/* NOT ON THE GALLERY: this drives whatever the desk offers over the live socket, and everything
	   it is made of (the buttons, the segmented tabs, the sheets, the strip) is on the gallery already. */
	/*
	 * The Remote: the phone driving what is already open at the desk, a player or a Theater wall.
	 *
	 * Drawn by `/remote` under its own title; it draws no frame or title of its own.
	 *
	 * ## One card per screen, in the screen's own regions
	 *
	 * An open player or wall at the desk offers itself to the signed-in user (`$lib/remote/offer`),
	 * and this lists those screens and drives one. The card leads with what plays: the file's name,
	 * the scrubber, the transport and the sound, the presses a thumb reaches for first, and under
	 * them the rest, over More controls:
	 *
	 * - A PLAYER (the popout, the corner player, a still): the sound with Mini player and Full
	 *   screen at its end as the bar has them, the heart and the O counter, then which screen, then
	 *   the drawer's seven presses in the drawer's own order.
	 * - A WALL: the chosen cell as a player (its scrubber, transport and sound), then which screen,
	 *   the wall's bar (Play everything, Silence everything, Layouts, Saved Layouts) and the cells as
	 *   a strip with the one being talked to lit (and Every cell under them), then the cell's drawer
	 *   in the drawer's order. Under Every cell the scrubber is dimmed with its reason: the cells'
	 *   files are each their own length, so one position means nothing for all of them.
	 *
	 * The drawer is folded under its name (`Fold`), shut until opened, so the card is the bar, the
	 * cells and the transport, and the drawer's presses are a press away.
	 *
	 * The same glyphs and words as the desk (`./copy`), so a press is called one thing on both.
	 *
	 * ## What is drawn, and what is dimmed
	 *
	 * The transport is every bar's (`Transport`): all five, each dimmed with its reason where the
	 * screen cannot act. The sound draws only what the screen answers. The DRAWERS draw every press
	 * in its place, dimmed where the screen cannot make it now, so a row never rearranges under a
	 * thumb. Full screen, Screenshot and Stats for nerds are always dimmed: they need a press made
	 * at the computer.
	 *
	 * A toggle sends the state it wants rather than "flip it" (both ends act on the same player),
	 * and a choice (a size, a layout, a preset, the repeat) sends its place in the list the screen
	 * reported, which the server holds to that list.
	 *
	 * ## The switch between screens
	 *
	 * With more than one screen the card's head is a chooser of exactly the screens there are now
	 * (never a choice with nothing behind it), and the one picked is the one driven. With one, the
	 * head names it.
	 *
	 * ## A browser that is not offering
	 *
	 * A browser tab offers itself only when its own switch is on, and one holding a player or a wall
	 * with the switch off says only that it exists (`$lib/remote/offer`). While the server counts
	 * one, this says where the switch is, in one line, so a missing laptop explains itself.
	 *
	 * ## Every control is a finger's width
	 *
	 * `--control-height` is the touch target on this screen, so every button is 44px on every width,
	 * and each slider stands in a 44px band.
	 */
	import { onMount, type Snippet } from 'svelte';
	import {
		Button,
		ChoiceCard,
		ContextMenuGroup,
		ContextMenuItem,
		Drawer,
		Empty,
		Fold,
		Heart,
		MenuButton,
		Panel,
		Problem,
		SectionHeading,
		Select,
		Skeleton,
		Slider,
		Tooltip
	} from '$lib/components/common';
	import LayoutGlyph from '$lib/components/theater/LayoutGlyph.svelte';
	import Transport from '$lib/components/player/Transport.svelte';
	import type { IconName } from '$lib/design/icons';
	import { thumbUrl } from '$lib/entity/art';
	import { lengthClock, playheadClock } from '$lib/shell/duration';
	import { LOOP_MODES } from '$lib/player/loop-modes';
	import { SKIP_SECONDS } from '$lib/player/skip';
	import { LAST_SECONDS } from '$lib/player/snapshot';
	import { LAYOUTS } from '$lib/theater/layouts';
	import type { RemoteAction } from '$lib/shell/shortcuts';
	import {
		COPY,
		LOOP_STEPS,
		TIMER_SECONDS,
		cellLabel,
		clipWords,
		counterName,
		timerWords,
		whatPlays
	} from './copy';
	import { remoteList } from './screens.svelte';
	import type { ScreenOut } from './wire';

	onMount(() => remoteList.watch());

	/** How long a dragged value is shown in place of the screen's own, while the screen catches up. */
	const HOLD_MS = 1_500;

	/** A thumb's drag sends at most this often; the release always sends. See `tuning.py`'s rate. */
	const DRAG_EVERY_MS = 250;

	const screens = $derived(remoteList.live);
	const screen = $derived(remoteList.current);
	const offers = $derived(new Set<RemoteAction>(screen?.supports ?? []));
	const wall = $derived(screen?.surface === 'theater');

	let layoutsOpen = $state(false);

	/* A value the thumb set, shown until the screen reports it back (or HOLD_MS passes). Keyed by the
	   screen, so picking another screen never shows the last one's drag. */
	let heldPosition = $state<{ screen: string; value: number; at: number } | null>(null);
	let heldVolume = $state<{ screen: string; value: number; at: number } | null>(null);
	let lastDrag = 0;

	function held(
		hold: { screen: string; value: number; at: number } | null,
		one: ScreenOut
	): number | null {
		if (hold === null || hold.screen !== one.screen) return null;
		return remoteList.at - hold.at < HOLD_MS ? hold.value : null;
	}

	const position = $derived(
		screen === null ? 0 : (held(heldPosition, screen) ?? remoteList.positionOf(screen))
	);
	const volume = $derived(screen === null ? 0 : (held(heldVolume, screen) ?? screen.volume));

	/* The one verb each shared control sends, on the surface this card is drawing. A wall's scrubber
	   and sound are the cell's; the wall's own hold and silence are the bar's two. */
	const seekVerb = $derived<RemoteAction>(wall ? 'theater.seekTo' : 'player.seekTo');
	const volumeVerb = $derived<RemoteAction>(wall ? 'theater.volumeTo' : 'player.volumeTo');
	const muteVerb = $derived<RemoteAction>(wall ? 'theater.mute' : 'player.mute');
	const backVerb = $derived<RemoteAction>(wall ? 'theater.back' : 'player.back');
	const forwardVerb = $derived<RemoteAction>(wall ? 'theater.forward' : 'player.forward');
	const previousVerb = $derived<RemoteAction>(wall ? 'theater.previous' : 'player.previous');
	const nextVerb = $derived<RemoteAction>(wall ? 'theater.next' : 'player.next');
	const playVerb = $derived<RemoteAction>(wall ? 'theater.pause' : 'player.playPause');
	const cornerVerb = $derived<RemoteAction>(wall ? 'theater.corner' : 'player.corner');
	const repeatVerb = $derived<RemoteAction>(wall ? 'theater.repeat' : 'player.repeat');
	const shuffleVerb = $derived<RemoteAction>(wall ? 'theater.shuffle' : 'player.shuffle');
	/* A wall from before a cell could be held from here: its only hold is the wall's. */
	const wallHoldOnly = $derived(wall && !offers.has(playVerb) && offers.has('theater.pauseAll'));

	/* Whether what the scrubber belongs to is playing, and heard: on a wall, the cell under its own
	   hold and the wall's (the drawer's play control reads both), and silent under either mute. */
	const going = $derived(
		screen === null ? false : wall ? screen.playing && !(screen.cell_held ?? false) : screen.playing
	);
	const silent = $derived(
		screen === null ? false : wall ? screen.muted || (screen.cell_muted ?? false) : screen.muted
	);

	/* Whether the scrubber may move: never on a wall under Every cell, whose cells are each their
	   own length (the desk's scrubber stays with the one cell it draws; from here that would move
	   one cell while the card says every cell). */
	const scrubbable = $derived(!(wall && (screen?.every_cell ?? false)));

	/* The drawer as it stands, read from the report; a screen from before these were reported says
	   nothing, and each reads as its resting state. */
	const repeat = $derived(screen?.repeat ?? 'loop_all');
	const shuffled = $derived(screen?.shuffle ?? false);
	const marks = $derived(Math.min(2, Math.max(0, screen?.loop_marks ?? 0)));
	const qualities = $derived(screen?.qualities ?? []);
	const cellFiles = $derived(screen?.cell_files ?? []);
	const presets = $derived(screen?.presets ?? []);

	function send(action: RemoteAction, value: number | null = null) {
		if (screen === null) return;
		void remoteList.send(screen.screen, action, value);
	}

	function seek(value: number, final: boolean) {
		if (screen === null) return;
		heldPosition = { screen: screen.screen, value, at: remoteList.at };
		if (final) send(seekVerb, value);
	}

	function level(value: number, final: boolean) {
		if (screen === null) return;
		heldVolume = { screen: screen.screen, value, at: remoteList.at };
		const now = performance.now();
		if (!final && now - lastDrag < DRAG_EVERY_MS) return;
		lastDrag = now;
		send(volumeVerb, value);
	}

	/** What the chooser says a screen is: its name, and a wall says it is one. */
	function titleOf(one: ScreenOut): string {
		return one.surface === 'theater' ? `${one.label}, ${COPY.wall}` : one.label;
	}

	function pick(which: string) {
		remoteList.pick(which);
	}

	/** The next answer of the repeat press, sent as its place in the one order both ends read. */
	function nextRepeat(): number {
		return (LOOP_MODES.indexOf(repeat) + 1) % LOOP_MODES.length;
	}
</script>

<!-- One press in a drawer: the glyph over its word, dimmed with its reason where it cannot act.
     `pressed` lights a toggle that is on, as the desk's drawer does. -->
{#snippet verb(
	icon: IconName,
	word: string,
	action: RemoteAction | null,
	onpress: () => void,
	options: { lit?: boolean; filled?: boolean; why?: string } = {}
)}
	{@const able = action !== null && offers.has(action)}
	<Tooltip label={able ? word : (options.why ?? COPY.notHere)} stretch>
		<Button
			tone="ghost"
			{icon}
			iconSize={20}
			iconFilled={options.filled ?? false}
			pressed={able ? (options.lit ?? undefined) : undefined}
			disabled={!able}
			onclick={onpress}
		>
			{word}
		</Button>
	</Tooltip>
{/snippet}

<!-- A drawer's press that opens a list (a size, a length of clip, a timer): the list is a sheet at a
     phone's width, as every menu there is. -->
{#snippet chooser(
	icon: IconName,
	word: string,
	action: RemoteAction,
	options: { why?: string; lit?: boolean },
	rows: Snippet
)}
	{#if offers.has(action) && !options.why}
		<MenuButton label={word}>
			{#snippet trigger({ props })}
				<Button {...props} tone="ghost" {icon} iconSize={20} pressed={options.lit}>{word}</Button>
			{/snippet}
			{@render rows()}
		</MenuButton>
	{:else}
		{@render verb(icon, word, null, () => {}, { why: options.why })}
	{/if}
{/snippet}

<div class="remote">
	{#if !remoteList.read && remoteList.problem === null}
		<Skeleton lines={3} />
	{:else if screen === null}
		{#if remoteList.problem !== null}<Problem message={remoteList.problem} />{/if}
		<!-- A browser holding its screens back is WHY there is nothing to control, so its sentence
		     is the empty state's own, in the same centred column (DESIGN 9.6), not a line under it. -->
		<Empty scope="page" icon="settings_remote" title={COPY.emptyTitle}
			>{#if remoteList.notOffering > 0}<span class="holding-back">{COPY.notOffering}</span
				>{:else}{COPY.empty}{/if}</Empty
		>
	{:else}
		<section aria-label={screen.label}>
			<Panel>
				<SectionHeading>
					{#if wall}
						{screen.every_cell ? COPY.everyCell : cellLabel(screen.focused ?? 0)}:
					{/if}
					{whatPlays(screen, remoteList.names) ?? (going ? COPY.playing : COPY.paused)}
				</SectionHeading>

				{#if offers.has(seekVerb) && screen.length !== null}
					<!-- Dimmed under Every cell, never taken away: the row keeps its place under a thumb,
					     and the line under it says why in place of one cell's times. -->
					<Tooltip label={scrubbable ? COPY.position : COPY.positionOfEvery} stretch>
						<div class="band grow">
							<Slider
								class="track"
								label={COPY.position}
								value={position}
								min={0}
								max={screen.length}
								step={1}
								disabled={!scrubbable}
								valueText={`${playheadClock(position, screen.length)} of ${lengthClock(screen.length)}`}
								oninput={(value) => seek(value, false)}
								onchange={(value) => seek(value, true)}
							/>
						</div>
					</Tooltip>
				{/if}
				{#if screen.length !== null && !scrubbable}
					<p class="times why">{COPY.positionOfEvery}</p>
				{:else if screen.length !== null}
					<p class="times">
						<span>{playheadClock(position, screen.length)}</span>
						<span>{lengthClock(screen.length)}</span>
					</p>
				{/if}

				<!-- Five seconds either way, the keys' arrows at the desk, over the transport. -->
				{#if offers.has(backVerb) || offers.has(forwardVerb)}
					<div class="row skips">
						{#if offers.has(backVerb)}
							<Tooltip label={COPY.back}>
								<Button
									tone="ghost"
									icon="replay_5"
									iconSize={28}
									aria-label={COPY.back}
									onclick={() => send(backVerb, SKIP_SECONDS)}
								/>
							</Tooltip>
						{/if}
						{#if offers.has(forwardVerb)}
							<Tooltip label={COPY.forward}>
								<Button
									tone="ghost"
									icon="forward_5"
									iconSize={28}
									aria-label={COPY.forward}
									onclick={() => send(forwardVerb, SKIP_SECONDS)}
								/>
							</Tooltip>
						{/if}
					</div>
				{/if}

				<!-- THE TRANSPORT, every bar's own. A wall's play press is the cell's. -->
				<div class="row transport">
					<Transport
						playing={wallHoldOnly ? screen.playing : going}
						playable={offers.has(playVerb) || wallHoldOnly}
						playWhy={COPY.notHere}
						onplay={() =>
							wallHoldOnly
								? send('theater.pauseAll', screen.playing ? 1 : 0)
								: send(playVerb, wall ? (going ? 1 : 0) : going ? 0 : 1)}
						onback={offers.has(previousVerb) ? () => send(previousVerb) : undefined}
						onforward={offers.has(nextVerb) ? () => send(nextVerb) : undefined}
						repeat={{
							mode: repeat,
							onpress: () => send(repeatVerb, nextRepeat()),
							why: offers.has(repeatVerb) ? undefined : COPY.notHere
						}}
						shuffle={{
							on: shuffled,
							onpress: () => send(shuffleVerb, shuffled ? 0 : 1),
							why: offers.has(shuffleVerb) ? undefined : COPY.notHere
						}}
						keyboard={null}
					/>
				</div>

				<!-- THE SOUND, with the bar's two ways out at its end: the Mini player, and Full screen,
				     which needs a press at the desk and is dimmed here saying so. -->
				<div class="row">
					{#if offers.has(muteVerb)}
						<Tooltip label={silent ? COPY.unmute : COPY.mute}>
							<Button
								tone="ghost"
								icon={silent || volume === 0 ? 'volume_off' : 'volume_up'}
								aria-label={silent ? COPY.unmute : COPY.mute}
								onclick={() => send(muteVerb, silent ? 0 : 1)}
							/>
						</Tooltip>
					{/if}
					{#if offers.has(volumeVerb)}
						<div class="band grow">
							<Slider
								class="track"
								label={COPY.volume}
								value={volume}
								min={0}
								max={100}
								step={1}
								valueText={`${volume}%`}
								oninput={(value) => level(value, false)}
								onchange={(value) => level(value, true)}
							/>
						</div>
					{:else}
						<span class="grow"></span>
					{/if}
					{#if offers.has(cornerVerb)}
						<Tooltip label={COPY.corner}>
							<Button
								tone="ghost"
								icon="picture_in_picture"
								aria-label={COPY.corner}
								onclick={() => send(cornerVerb)}
							/>
						</Tooltip>
					{/if}
					<Tooltip label={COPY.fillAtTheDesk}>
						<Button tone="ghost" icon="fullscreen" aria-label={COPY.fill} disabled />
					</Tooltip>
				</div>

				{#if !wall && (offers.has('player.favorite') || offers.has('player.count'))}
					<!-- The file's own marks, as the viewer's bar has them: the heart, then the O counter. -->
					<div class="row marks">
						{#if offers.has('player.favorite')}
							<Heart
								favorite={screen.favorite ?? false}
								size={20}
								onchange={(on) => send('player.favorite', on ? 1 : 0)}
							/>
						{/if}
						{#if offers.has('player.count')}
							<Tooltip label={COPY.oneMore}>
								<Button
									tone="ghost"
									icon="water_drop"
									iconSize={20}
									iconFilled={(screen.count ?? 0) > 0}
									aria-label={counterName(screen.count ?? 0)}
									onclick={() => send('player.count')}
								>
									{screen.count ?? 0}
								</Button>
							</Tooltip>
						{/if}
					</div>
				{/if}

				<!-- WHICH SCREEN, under the transport and the sound and over More controls: what a
				     thumb reaches for first is what plays, and the screen it plays on is chosen
				     less often. A chooser of exactly the screens there are now, or with one its
				     name alone: a choice with no screen behind it is never drawn. -->
				<div class="head">
					{#if screens.length > 1}
						<Select
							label={COPY.screens}
							value={screen.screen}
							options={screens.map((one) => ({ value: one.screen, label: titleOf(one) }))}
							onValueChange={pick}
						/>
					{:else}
						<p class="label">{titleOf(screen)}</p>
					{/if}
				</div>
				{#if remoteList.notOffering > 0}
					<p class="holding-back">{COPY.notOffering}</p>
				{/if}

				{#if wall}
					<!-- THE WALL'S BAR: its hold, its silence, and the two lists it chooses from. -->
					<div class="verbs" role="group" aria-label={COPY.wall}>
						{@render verb(
							screen.playing ? 'autostop' : 'autoplay',
							screen.playing ? COPY.pauseAll : COPY.playAll,
							'theater.pauseAll',
							() => send('theater.pauseAll', screen.playing ? 1 : 0),
							{ lit: screen.playing }
						)}
						{@render verb(
							screen.muted ? 'volume_off' : 'volume_up',
							screen.muted ? COPY.soundOn : COPY.silenceAll,
							'theater.muteAll',
							() => send('theater.muteAll', screen.muted ? 0 : 1),
							{ lit: screen.muted }
						)}
						{@render verb('view_array', COPY.layouts, 'theater.layout', () => (layoutsOpen = true))}
						{#snippet presetRows()}
							<ContextMenuGroup>
								{#if presets.length === 0}
									<ContextMenuItem label={COPY.noPresets} disabled onselect={() => {}} />
								{/if}
								{#each presets as name, at (at)}
									<ContextMenuItem label={name} onselect={() => send('theater.preset', at)} />
								{/each}
							</ContextMenuGroup>
						{/snippet}
						{@render chooser('table_view', COPY.presets, 'theater.preset', {}, presetRows)}
					</div>

					<!-- THE CELLS, as a strip: Every cell first, then each cell with what it shows. -->
					{#if screen.cells > 0}
						<div class="cells" role="group" aria-label={COPY.cells}>
							{#each Array.from({ length: screen.cells }, (_, index) => index) as index (index)}
								{@const shows = cellFiles[index]}
								<ChoiceCard
									role="button"
									name={cellLabel(index)}
									chosen={!screen.every_cell && screen.focused === index}
									onchoose={() => send('theater.cell', index)}
								>
									{#snippet preview()}
										{#if shows?.file}
											<img class="thumb" src={thumbUrl({ id: shows.file })} alt="" />
										{:else if shows?.hidden}
											<img class="thumb" src={thumbUrl({ id: '', concealed: true })} alt="" />
										{:else}
											<span class="thumb"></span>
										{/if}
									{/snippet}
								</ChoiceCard>
							{/each}
							<!-- Every cell at once, the selection the desk's cell chooser and its backtick
							     make: a row of its own under the cells, as "or all of them". -->
							{#if offers.has('theater.everyCell') && screen.cells > 1}
								<span class="every">
									<ChoiceCard
										role="button"
										dense
										name={COPY.everyCell}
										chosen={screen.every_cell}
										onchoose={() => send('theater.everyCell')}
									/>
								</span>
							{/if}
						</div>
					{/if}
				{/if}

				<!-- THE DRAWER, every press in the desk drawer's own order, dimmed where it cannot act,
				     folded under its name until opened. -->
				<Fold section summary={COPY.drawer} open={false} remember="sift.remote.drawer">
					<div class="verbs" role="group" aria-label={COPY.drawer}>
						{#snippet qualityRows()}
							<ContextMenuGroup>
								{#each qualities as name, at (at)}
									<ContextMenuItem
										label={name}
										checked={screen?.quality === at}
										onselect={() => send(wall ? 'theater.quality' : 'player.quality', at)}
									/>
								{/each}
							</ContextMenuGroup>
						{/snippet}
						{#snippet clipRows()}
							<ContextMenuGroup>
								{#each LAST_SECONDS as seconds (seconds)}
									<ContextMenuItem
										label={clipWords(seconds)}
										onselect={() => send('player.clip', seconds)}
									/>
								{/each}
							</ContextMenuGroup>
						{/snippet}
						{#snippet timerRows()}
							<ContextMenuGroup>
								{#each TIMER_SECONDS as seconds (seconds)}
									<ContextMenuItem
										label={timerWords(seconds)}
										checked={(screen?.timer ?? 0) === seconds}
										onselect={() => send('theater.timer', seconds)}
									/>
								{/each}
							</ContextMenuGroup>
						{/snippet}
						{#if wall}
							{@render verb('all_inclusive', LOOP_STEPS[marks], 'theater.loop', () =>
								send('theater.loop')
							)}
							{@render verb(
								'bookmark_add',
								COPY.saveLoop,
								marks === 2 ? 'theater.saveLoop' : null,
								() => send('theater.saveLoop'),
								{ why: COPY.markBoth }
							)}
							{@render verb('casino', COPY.randomize, 'theater.random', () =>
								send('theater.random')
							)}
							{@render chooser(
								'video_settings',
								COPY.quality,
								'theater.quality',
								{ why: qualities.length > 1 ? undefined : COPY.oneSize },
								qualityRows
							)}
							{@render verb('cognition_2', COPY.stats, null, () => {}, {
								why: COPY.statsAtTheDesk
							})}
							{@render verb('hearing', COPY.solo, 'theater.solo', () => send('theater.solo'))}
							{@render chooser(
								'timer',
								COPY.timer,
								'theater.timer',
								{ lit: (screen.timer ?? 0) > 0 },
								timerRows
							)}
						{:else}
							{@render chooser('content_cut', COPY.clip, 'player.clip', {}, clipRows)}
							{@render verb('screenshot_region', COPY.screenshot, null, () => {}, {
								why: COPY.screenshotAtTheDesk
							})}
							{@render chooser(
								'video_settings',
								COPY.quality,
								'player.quality',
								{ why: qualities.length > 1 ? undefined : COPY.oneSize },
								qualityRows
							)}
							{@render verb('casino', COPY.randomize, 'player.random', () => send('player.random'))}
							{@render verb('all_inclusive', LOOP_STEPS[marks], 'player.loop', () =>
								send('player.loop')
							)}
							{@render verb(
								'bookmark_add',
								COPY.saveLoop,
								marks === 2 ? 'player.saveLoop' : null,
								() => send('player.saveLoop'),
								{ why: COPY.markBoth }
							)}
							{@render verb('cognition_2', COPY.stats, null, () => {}, {
								why: COPY.statsAtTheDesk
							})}
						{/if}
					</div>
				</Fold>

				<!-- One line, where the last press or read went wrong: the server's own sentence for a
				     refusal, or that the screen never said it acted. -->
				{#if remoteList.problem !== null}
					<Problem message={remoteList.problem} />
				{/if}
			</Panel>
		</section>

		<!-- The layouts, each drawn as its own shape, as the desk's Layouts menu draws them: a name
		     alone does not say what a wall will look like. A sheet from the foot of the screen. -->
		{#if wall}
			<Drawer side="bottom" label={COPY.layouts} bind:open={layoutsOpen}>
				<div class="layouts">
					{#each LAYOUTS as one, at (one.id)}
						<ChoiceCard
							role="button"
							name={one.label}
							chosen={screen.layout === at}
							onchoose={() => {
								send('theater.layout', at);
								layoutsOpen = false;
							}}
						>
							{#snippet preview()}
								<span class="shape"><LayoutGlyph shape={one} /></span>
							{/snippet}
						</ChoiceCard>
					{/each}
				</div>
			</Drawer>
		{/if}
	{/if}
</div>

<style>
	/* Every button on this screen is a finger's width, on every width: the page is a remote, and a
	   remote is held in one hand. `Button` sizes itself from this token. */
	.remote {
		--control-height: var(--touch-target);
		display: flex;
		flex-direction: column;
		gap: var(--space-4);
	}

	/* The card's head: the chooser, or with one screen its name, quieter than what it plays. */
	.head {
		display: flex;
		align-items: center;
		min-block-size: var(--touch-target);
	}

	.label {
		margin: 0;
		font: var(--text-label);
		color: var(--sift-ink-2);
	}

	/* Where a browser's switch is, while one is holding a player back: said, not warned. */
	p.holding-back {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}

	/* A slider's own track is thin; the band around it is what a thumb lands in. */
	.band {
		display: flex;
		align-items: center;
		min-block-size: var(--touch-target);
	}

	.grow {
		flex: 1;
		min-inline-size: 0;
	}

	/* The input itself is the band's height, so the whole band takes the thumb, not a 16px strip. */
	.band :global(.track) {
		inline-size: 100%;
		block-size: var(--touch-target);
	}

	.times {
		display: flex;
		justify-content: space-between;
		margin: 0;
		font: var(--text-data);
		color: var(--sift-ink-2);
	}

	/* Why the scrubber is dimmed, in words, where its times would be. */
	.times.why {
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	/* A layout's picture in the ink the desk's Layouts menu draws it in: the card is a button, and
	   a button's own text colour is the browser's, which drew the blocks black on the dark card. */
	.shape {
		display: inline-flex;
		color: var(--sift-ink-2);
	}

	.row {
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}

	.transport,
	.skips {
		justify-content: center;
	}

	.marks {
		justify-content: center;
	}

	/* The heart is a finger's width here too: its own box is one glyph tall, sized for a card's
	   corner, and on a remote it is a press. */
	.marks :global(.heart) {
		min-inline-size: var(--touch-target);
		min-block-size: var(--touch-target);
	}

	/*
	 * A BAR OR A DRAWER: equal columns, the glyph over the word, each a finger's height.
	 *
	 * The shape a phone's selection bar has (DESIGN 8.8's two-line bar), for the same reason: a row
	 * of bare glyphs asks a thumb to guess, and there is no pointer here to raise a tooltip. Four
	 * across fits the narrowest phone with every word whole; a drawer of nine is three rows, and
	 * each press keeps its place whether or not it can act.
	 */
	.verbs {
		display: grid;
		grid-template-columns: repeat(4, minmax(0, 1fr));
		gap: var(--space-1);
	}

	.verbs > :global(*) {
		display: flex;
		min-inline-size: 0;
	}

	.verbs :global(button.btn) {
		flex: 1 1 0;
		flex-direction: column;
		justify-content: flex-start;
		gap: var(--space-1);
		min-inline-size: 0;
		inline-size: 100%;
		block-size: auto;
		min-block-size: var(--touch-target);
		padding: var(--space-2) var(--space-1);
		white-space: normal;
		text-align: center;
		font: var(--text-body-sm);
	}

	/* The word wraps under its glyph, between words, rather than being cut: "Set the loop end" is two
	   lines, not one clipped. */
	.verbs :global(.btn .label) {
		white-space: normal;
	}

	/* The strip of cells: four across, the one being talked to lit; a picture of what each shows. */
	.cells,
	.layouts {
		display: grid;
		grid-template-columns: repeat(4, minmax(0, 1fr));
		gap: var(--space-2);
	}

	.every {
		display: grid;
		grid-column: 1 / -1;
	}

	/* A finger's height on every width, as every press on this screen is; the card floors itself
	   only at a phone's. */
	.every :global(.card) {
		min-block-size: var(--touch-target);
	}

	.thumb {
		display: block;
		inline-size: 100%;
		aspect-ratio: 16 / 9;
		border-radius: var(--radius-sm);
		background: var(--sift-surface-3);
		object-fit: cover;
	}
</style>
