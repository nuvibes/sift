<script lang="ts">
	/*
	 * One cell's bar, which IS the player's bar; what is a cell's own goes in the drawer. Mute,
	 * solo and volume are pressed, never restored: an unmute without a gesture pauses the element.
	 */
	import Icon from '$lib/components/Icon.svelte';
	import BarPanel from '$lib/components/common/BarPanel.svelte';
	import { Button, NumberInput } from '$lib/components/common';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Separator from '$lib/components/common/Separator.svelte';
	import PlayerBar from '$lib/components/player/PlayerBar.svelte';
	import ScreenshotButton from '$lib/components/player/ScreenshotButton.svelte';
	import ClipButton from '$lib/components/player/ClipButton.svelte';
	import { getStage } from '$lib/components/player/stage.svelte';
	import { mini } from '$lib/player/mini.svelte';
	import { stage } from '$lib/components/shell/stage.svelte';
	import { toCorner } from '$lib/theater/corner';
	import { spriteUrl } from '$lib/entity/art';
	import { usable } from '$lib/player/trickplay';
	import { aims as marks, aimsAtChosen } from '$lib/theater/aim';
	import { loopModeIcon, loopModeLabel, loopRepeats, nextLoopMode } from '$lib/player/loop-modes';
	import { clipTheStretch } from '$lib/edit/edit.svelte';
	import { SKIP_SECONDS } from '$lib/player/skip';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { saveSettings } from '$lib/settings-ui/settings';
	import type { Quality } from '$lib/player/playback';
	import type { Snippet } from 'svelte';
	import type { Cell } from '$lib/theater/cell.svelte';
	import { MEDIA_KINDS } from '$lib/theater/cell.svelte';
	import { MEDIA_KIND_LABELS, type Wall } from '$lib/theater/wall.svelte';
	import { pressShuffle } from '$lib/theater/orders';
	import { ACTS, keyOf } from '$lib/player/acts';

	interface Props {
		wall: Wall;
		cell: Cell;
		index: number;
		position: number;
		duration: number;
		onseek: (seconds: number) => void;
		/*
		 * What is open under this bar is the cell's (`Cell.factsOpen`): three components ask it.
		 */
		/** Whether this bar spans a filled wall, where the two ways out join the row. */
		filled?: boolean;
		picker?: Snippet<[number]>;
	}

	let { wall, cell, index, position, duration, onseek, filled = false, picker }: Props = $props();

	/*
	 * `hold` clears the frame's idle clock and `wake` restarts it; stopping the event does neither.
	 */
	const frame = getStage();
	const pictured = $derived(cell.shot?.() ?? null);

	const number = $derived(index + 1);
	const silent = $derived(wall.masterMuted || cell.muted);
	const held = $derived(wall.paused || cell.paused);

	/* The cell drawn here, or every addressed cell for a relative move (`Wall.addressed`). */
	const acting = $derived(wall.addressed);
	const actingAt = $derived(wall.addressedAt);
	const goesBack = $derived(acting.some((one) => one.hasBack));

	/*
	 * What this bar is about to act on, washed on the wall while a verb is pointed at; every cell
	 * when All is on, none for the wall's own controls. `$lib/theater/aim`'s rule.
	 */
	const aims = marks(
		wall,
		aimsAtChosen(wall, () => index)
	);

	/* The player's bar takes a callback. */
	const aim = (on: boolean) => (on ? aims.onmouseenter() : aims.onmouseleave());

	/* A picture has no playhead and no sound: those controls dim rather than vanish. */
	const still = $derived(cell.playing !== null && cell.playing.media_type !== 'video');
	const timed = $derived(cell.playing !== null && cell.playing.media_type === 'video');

	/* Over plain http a GIF is an `<img>` a hold cannot stop, so the held cells say so. */
	const heldCells = $derived(wall.paused ? wall.cells : cell.paused ? [cell] : []);

	/** Names what the control will land on: icon-only buttons are named by this alone. */
	function verb(what: string): string {
		return wall.everyCell ? `${what} in all cells` : `${what} in cell ${number}`;
	}

	const strip = $derived(usable(cell.sheet) ? cell.sheet : null);
	const mine = $derived(cell.playing !== null && cell.loop.owns(cell.playing.id));

	function togglePlay() {
		// The wall's own hold wins: starting one cell of a stopped wall is a guess.
		if (wall.paused) {
			wall.togglePause();
			return;
		}
		// The cell on the bar decides, so all addressed cells go the same way.
		const next = !cell.paused;
		acting.forEach((one) => (one.paused = next));
	}

	/* Zero means wait for the file to end. */
	function setTimer(seconds: number) {
		const held = seconds > 0 ? seconds : null;
		acting.forEach((one) => (one.timerSeconds = held));
	}

	/* A RELATIVE move, so it reaches every addressed cell, each from its own position. */
	function stepBack() {
		acting.forEach((one) => one.seek?.(Math.max(0, one.position - SKIP_SECONDS)));
	}

	/* Clamped against each cell's OWN length. */
	function stepForward() {
		acting.forEach((one) =>
			one.seek?.(Math.min(one.duration || Number.POSITIVE_INFINITY, one.position + SKIP_SECONDS))
		);
	}

	function repeatNext() {
		const next = nextLoopMode(cell.endBehaviour);
		acting.forEach((one) => (one.endBehaviour = next));
	}

	function markNext() {
		if (cell.playing === null) return;
		cell.loop.mark(cell.playing.id, position);
	}

	const abLabel = $derived(mine ? cell.loop.nextAction : 'Set the loop start');

	/* The marked stretch, saved as a Loop, as the player does (`clipTheStretch`). */
	let saving = $state(false);

	const savable = $derived(mine && cell.loop.running && !saving);

	const saveLabel = $derived(
		savable ? ACTS.saveLoop : saving ? 'Saving it as a Loop' : 'Mark both ends of a loop to save it'
	);

	async function saveLoop() {
		const file = cell.playing;
		const from = cell.loop.a;
		const to = cell.loop.b;
		if (!savable || file === null || from === null || to === null) return;
		saving = true;
		const startMs = Math.round(from * 1000);
		const endMs = Math.round(to * 1000);
		const cut = await clipTheStretch(file.id, startMs, endMs - startMs, { asLoop: true });
		saving = false;
		toasts.show(
			cut.made ? 'Saving it as a loop \u2014 exactly the stretch you marked' : cut.because,
			{ tone: cut.made ? 'success' : 'error' }
		);
	}

	/* The cell carries the act and its view installs it, as `seek` does. */
	const rungs = $derived(cell.plan?.qualities ?? []);
	const watchingAt = $derived(cell.quality?.url ?? cell.plan?.url);

	const choosable = $derived(rungs.length > 1);
	const qualityLabel = $derived(
		choosable
			? ACTS.quality
			: still
				? 'Pictures and GIFs play at one size'
				: 'This file has one size'
	);

	function pickQuality(quality: Quality) {
		cell.changeQuality?.(quality);
	}

	/* Stage View's modes, written to the account's setting from the drawer. */
	const CENTER_STAGE_KEY = 'theater.center_stage';

	const modeLabel = $derived(
		!wall.centerStage
			? 'Previews come with a Stage View layout'
			: wall.newestTakesFocus
				? 'A preview comes up as soon as it starts something new'
				: 'A preview comes up when you double-click it'
	);

	function setMode(newest: boolean) {
		wall.newestTakesFocus = newest;
		void saveSettings({ [CENTER_STAGE_KEY]: newest ? 'newest' : 'pick' }).catch(() => {
			// The mode applied; remembering it failed, and the control will look wrong tomorrow.
			toasts.show("That couldn't be saved as your preference", { tone: 'error' });
		});
	}

	function openBelow(which: 'facts' | 'timer' | 'quality' | null) {
		cell.factsOpen = which === 'facts';
		cell.timing = which === 'timer';
		cell.qualityOpen = which === 'quality';
	}
</script>

<PlayerBar
	{position}
	{duration}
	sheet={strip}
	sheetUrl={cell.playing ? spriteUrl({ id: cell.playing.id, art: cell.playing.art }) : null}
	pointA={mine ? cell.loop.a : null}
	pointB={mine ? cell.loop.b : null}
	{onseek}
	onmark={(which, seconds) => cell.loop.moveTo(which, Math.min(Math.max(seconds, 0), duration))}
	scrubberLabel="Position in cell {number}"
	seekable={!still}
	sound={!still}
	{timed}
	playing={!held}
	onplay={togglePlay}
	onback={goesBack ? () => acting.forEach((one) => void one.back()) : undefined}
	onforward={() => acting.forEach((one) => void one.advance())}
	backLabel={verb(ACTS.previous)}
	forwardLabel={verb(ACTS.next)}
	keyboard="theater"
	variant="theater"
	muted={silent}
	volume={cell.volume}
	onmute={() => wall.setMuted(actingAt, !silent)}
	onvolume={(level) => acting.forEach((one) => (one.volume = level))}
	onhold={(event) => frame?.hold(event)}
	onrelease={() => frame?.wake()}
	onaim={aim}
	lead={picker}
	{tray}
	{below}
	trailing={filled ? trailing : undefined}
/>

<!-- The two ways out, on the row: the wall's own, since the top toolbar is gone while it fills. -->
{#snippet trailing()}
	<Separator vertical />

	<Tooltip
		label={mini.wall ? ACTS.fullSize : ACTS.miniPlayer}
		shortcut={keyOf(mini.wall ? 'fullSize' : 'miniPlayer', 'theater')}
	>
		<Button
			tone="ghost"
			icon="picture_in_picture"
			aria-label={mini.wall ? ACTS.fullSize : ACTS.miniPlayer}
			pressed={mini.wall}
			onclick={toCorner}
		/>
	</Tooltip>

	<Tooltip
		label={stage.filling ? ACTS.leaveFullScreen : ACTS.fullScreen}
		shortcut={keyOf(stage.filling ? 'leaveFullScreen' : 'fullScreen', 'theater')}
	>
		<Button
			tone="ghost"
			icon={stage.filling ? 'fullscreen_exit' : 'fullscreen'}
			aria-label={stage.filling ? ACTS.leaveFullScreen : ACTS.fullScreen}
			pressed={stage.filling}
			onclick={() => stage.toggle()}
		/>
	</Tooltip>
{/snippet}

{#snippet tray()}
	<!--
	The player's drawer, then the cell's own: always fourteen, a control that does not apply dimmed
	with its reason.
	-->
	<ClipButton
		of={cell.playing?.id ?? null}
		playhead={() => position}
		unclippable={still ? 'Only a video can be clipped' : null}
		portalTo={stage.whatFillsTheWindow}
	/>
	<ScreenshotButton
		of={cell.playing?.id ?? null}
		video={pictured?.video ?? null}
		still={pictured?.still ?? null}
		stage={pictured?.stage ?? null}
		portalTo={stage.whatFillsTheWindow}
	/>
	<Tooltip label={abLabel} placement="top">
		<Button
			{...aims}
			tone="ghost"
			icon="all_inclusive"
			aria-label={abLabel}
			pressed={mine && cell.loop.running}
			onclick={markNext}
		/>
	</Tooltip>

	<!-- Always drawn, dimmed until both ends are marked (`unsavable`). -->
	<Tooltip label={saveLabel} placement="top">
		<Button
			{...aims}
			tone="ghost"
			icon="bookmark_add"
			aria-label={saveLabel}
			disabled={!savable}
			onclick={saveLoop}
		/>
	</Tooltip>

	<Tooltip label={ACTS.randomize} placement="top">
		<Button
			{...aims}
			tone="ghost"
			icon="casino"
			aria-label={verb(ACTS.randomize)}
			onclick={() => acting.forEach((one) => void one.somethingElse())}
		/>
	</Tooltip>

	<Tooltip label={loopModeLabel(cell.endBehaviour)} placement="top">
		<Button
			{...aims}
			tone="ghost"
			icon={loopModeIcon(cell.endBehaviour)}
			aria-label={loopModeLabel(cell.endBehaviour)}
			pressed={loopRepeats(cell.endBehaviour)}
			onclick={repeatNext}
		/>
	</Tooltip>
	<Tooltip label={ACTS.shuffle} shortcut={keyOf('shuffle', 'theater')} placement="top">
		<Button
			{...aims}
			tone="ghost"
			icon="shuffle"
			aria-label={verb(ACTS.shuffle)}
			pressed={cell.ordering === 'shuffle'}
			onclick={() => pressShuffle(acting, cell.ordering === 'shuffle')}
		/>
	</Tooltip>

	<!-- The size, always drawn; `qualities?.` since an older server's plan lacks the field. -->
	<Tooltip label={qualityLabel} placement="top">
		<Button
			{...aims}
			tone="ghost"
			icon="video_settings"
			aria-label={qualityLabel}
			disabled={!choosable}
			pressed={choosable && cell.qualityOpen}
			onclick={() => openBelow(cell.qualityOpen ? null : 'quality')}
		/>
	</Tooltip>

	<Tooltip label={ACTS.stats} placement="top">
		<Button
			{...aims}
			tone="ghost"
			icon="cognition_2"
			aria-label={ACTS.stats}
			pressed={cell.factsOpen}
			onclick={() => openBelow(cell.factsOpen ? null : 'facts')}
		/>
	</Tooltip>

	<Tooltip label="Hear only this" placement="top">
		<Button
			{...aims}
			tone="ghost"
			icon="hearing"
			aria-label="Hear only cell {number}"
			onclick={() => wall.solo(index)}
		/>
	</Tooltip>

	<!-- Two controls for a choice with two answers; which is lit is the answer. -->
	{#each MEDIA_KINDS as kind (kind)}
		<Tooltip label={MEDIA_KIND_LABELS[kind]} placement="top">
			<Button
				{...aims}
				tone="ghost"
				icon={kind === 'video_gif' ? 'videocam' : 'video_camera_back_add'}
				aria-label={MEDIA_KIND_LABELS[kind]}
				pressed={cell.mediaKind === kind}
				onclick={() =>
					acting.forEach((one) => {
						one.mediaKind = kind;
						void one.restart();
					})}
			/>
		</Tooltip>
	{/each}

	<!-- The only one needing a number: one field under the bar. -->
	<Tooltip label="Move on after" placement="top">
		<Button
			{...aims}
			tone="ghost"
			icon="timer"
			aria-label="How long cell {number} holds a file"
			pressed={cell.timing || cell.timerSeconds !== null}
			aria-expanded={cell.timing}
			onclick={() => openBelow(cell.timing ? null : 'timer')}
		/>
	</Tooltip>

	<Tooltip label={modeLabel} placement="top">
		<Button
			tone="ghost"
			icon={wall.newestTakesFocus ? 'bolt' : 'touch_app'}
			aria-label={modeLabel}
			disabled={!wall.centerStage}
			pressed={wall.centerStage && wall.newestTakesFocus}
			onclick={() => setMode(!wall.newestTakesFocus)}
		/>
	</Tooltip>
{/snippet}

{#snippet below()}
	{#if cell.qualityOpen && choosable}
		<!-- Not portalled, or it is not drawn while filled; held up while the pointer is in it. -->
		<div
			role="presentation"
			onpointerenter={(event) => frame?.hold(event)}
			onpointermove={(event) => frame?.hold(event)}
			onpointerleave={() => frame?.wake()}
		>
			<BarPanel row label={ACTS.quality}>
				{#each rungs as rung (rung.url)}
					<Button tone="ghost" pressed={watchingAt === rung.url} onclick={() => pickQuality(rung)}>
						<!-- The file's own entry says what it comes to; no "may stutter". -->
						{rung.label}{rung.detail ? ` (${rung.detail})` : ''}
					</Button>
				{/each}
			</BarPanel>
		</div>
	{/if}

	{#if cell.timing}
		<div
			class="cell-settings"
			role="group"
			aria-label="How long cell {number} holds a file"
			onpointerenter={(event) => frame?.hold(event)}
			onpointermove={(event) => frame?.hold(event)}
			onpointerleave={() => frame?.wake()}
		>
			<label class="cell-setting">
				<span class="cell-icon-label">
					<Icon name="timer" size={20} label="Move on after" />
				</span>
				<!-- `NumberInput`, committing on blur; zero is a word, as under Playback. -->
				<NumberInput
					value={cell.timerSeconds ?? 0}
					min={0}
					max={3600}
					width={5}
					unit="sec"
					automatic="No timer"
					label="Seconds before cell {number} moves on"
					onchange={(next) => setTimer(next)}
				/>
			</label>
		</div>
	{/if}
{/snippet}

<style>
	/* `:global` because these render inside the bar; namespaced so they reach nothing else. */
	:global(.cell-settings) {
		display: grid;
		grid-template-columns: auto auto;
		justify-self: start;
		align-items: center;
		gap: var(--space-2) var(--space-3);
		margin-block-start: var(--space-2);
		padding: var(--space-3);
		border-radius: var(--radius-md);
		background: var(--sift-surface-2);
	}

	:global(.cell-setting) {
		display: contents;
		color: var(--sift-ink-2);
		font: var(--text-label);
	}

	/* Centred in its grid cell (`display: contents` on the label). */
	:global(.cell-icon-label) {
		display: inline-flex;
		align-items: center;
		color: var(--sift-ink-2);
	}
</style>
