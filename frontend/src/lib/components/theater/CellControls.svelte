<script lang="ts">
	/*
	 * One cell's bar, which IS the player's bar.
	 *
	 * Literally the same component: the scrubber, the transport, the clock, the volume popup and the
	 * drawer all come from the one the ordinary player draws. A cell's bar is judged against the bar
	 * somebody has already learned, so a second implementation that merely resembled it would be
	 * wrong in exactly the way that is most irritating to use: two copies drift, into a two-line
	 * bar and a one-line one.
	 *
	 * What is a cell's own goes in the DRAWER, which is what a drawer is for: the source it draws
	 * from, how it plays, the A-B loop, and hearing this one alone. None of those belong on a row a
	 * wall of nine has to fit several copies of.
	 *
	 * Muting, soloing and volume are all pressed rather than restored. A browser pauses an element
	 * that is unmuted without somebody having interacted with the page, so an unmute applied on load
	 * or from a hover silently stops the cell instead of unmuting it.
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
	import { nextLoopMode } from '$lib/player/loop-modes';
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
		/** Where the playhead is, in seconds. */
		position: number;
		/** How long the file is, in seconds. Zero until the element has said. */
		duration: number;
		/** Move the playhead. The element is the only thing that can seek, so it is asked to. */
		onseek: (seconds: number) => void;
		/*
		 * WHAT IS OPEN UNDER THIS BAR IS THE CELL'S ANSWER, not a prop.
		 *
		 * The facts panel, the timer field and the list of sizes are not bound props here. Each
		 * would be bound to the identical field on the cell, because they have to be: the
		 * control is on this row, the panel it opens is drawn over the cell's own picture by a
		 * different component, and the cell's own right-click menu opens two of them as well. Three
		 * things asking one question, so the answer is on the object all three can see. See
		 * `Cell.factsOpen`.
		 */
		/**
		 * Whether this bar has the whole screen across, at the foot of a filled wall.
		 *
		 * It names the SURFACE rather than one behaviour: the two ways out (the corner and the screen)
		 * join the row there.
		 *
		 * No filter on this bar, on either surface: what a cell plays is
		 * chosen from Filter on the bar above the wall, whose chip says which cell it changes, and
		 * from the cell's own menu (What it plays).
		 */
		filled?: boolean;
		/** Drawn at the start of the transport row, left of the transport. The cell picker. */
		picker?: Snippet<[number]>;
	}

	let { wall, cell, index, position, duration, onseek, filled = false, picker }: Props = $props();

	/* The frame this is drawn in, which decides when its chrome fades.
	 *
	 * It has to be TOLD the pointer is on the controls, and stopping the event from reaching it is
	 * not the same thing: that only means the idle clock it already started is never restarted, so
	 * the bar fades a couple of seconds later with the pointer still on it. `hold` clears the clock;
	 * `wake` on the way out starts it again, or the controls stay up forever. */
	const frame = getStage();
	/* What a screenshot of this cell is taken from, read again whenever the cell's elements change. */
	const pictured = $derived(cell.shot?.() ?? null);

	const number = $derived(index + 1);
	const silent = $derived(wall.masterMuted || cell.muted);
	const held = $derived(wall.paused || cell.paused);

	/*
	 * What the verbs on this row land on: the cell drawn here, or every cell of the wall
	 * (`Wall.addressed`). The scrubber is a position on one clip and stays with the cell it draws;
	 * a relative move ("next", "louder", "five seconds back") lands on every addressed cell.
	 */
	const acting = $derived(wall.addressed);
	const actingAt = $derived(wall.addressedAt);
	const goesBack = $derived(acting.some((one) => one.hasBack));

	/*
	 * WHAT THIS BAR IS ABOUT TO ACT ON, WASHED ON THE WALL WHILE A VERB IS POINTED AT.
	 *
	 * The bar drives whichever cell is chosen, out of up to nine on screen, and nothing on the bar
	 * itself can say which: the answer is a rectangle somewhere else. The filter button washes its
	 * target, so the same answer is given by everything else here that acts on a cell: the transport on the row, and the drawer.
	 *
	 * `every` when the All chip is on, because then the verb really does land on all of them and a
	 * wash on one would be a lie about what is about to happen. Read at the moment the pointer
	 * arrives rather than captured, so switching between one cell and all of them under a held
	 * pointer marks the right thing.
	 *
	 * NOT on the controls that are about the WALL rather than about a cell: the two ways out, and
	 * Center stage's mode. Washing every cell for a control that changes none of them is the mark
	 * meaning nothing, which is how a mark stops being read.
	 */
	/* One object, spread on each control that answers this question. Written out per control it is
	   four handlers times a dozen buttons, and the one that drifts is a cell left lit under a pointer
	   that has gone.

	   It is `$lib/theater/aim`'s, so the cell chooser at the head of the filter panel lights its
	   target by the same rule rather than by a copy of it. Aliased on the way in so `aims` stays the
	   name every control below spreads. */
	const aims = marks(
		wall,
		aimsAtChosen(wall, () => index)
	);

	/* The player's own bar takes a CALLBACK rather than the four handlers, because it binds them to
	   controls of its own inside that file. Same question, asked the way that component asks it. */
	const aim = (on: boolean) => (on ? aims.onmouseenter() : aims.onmouseleave());

	/*
	 * Whether what is playing is a PICTURE rather than a clip, and a GIF is a picture.
	 *
	 * Two controls on this row cannot do anything for one: an `<img>` has no playhead to move along
	 * and no sound to turn up. They are dimmed rather than removed, which is the rule the tile-size
	 * slider on the top bar already follows: a control that vanishes between one file and the next
	 * is one people stop reaching for, because they cannot tell "not here" from "not there yet".
	 */
	const still = $derived(cell.playing !== null && cell.playing.media_type !== 'video');
	/* A clock only for a clip. A picture has no length, and a cell with nothing in it has nothing
	   to time: "0:00 / 0:00" there reads as a clip that will not start. */
	const timed = $derived(cell.playing !== null && cell.playing.media_type === 'video');

	/*
	 * Why a held wall still has something moving in it, said where the pause control is.
	 *
	 * A GIF is played frame by frame onto a canvas wherever the browser will decode one, and
	 * that is what makes a hold a real hold. The decoder is a secure-context feature: a page opened
	 * over plain http does not get one, so the cell draws the ordinary `<img>`, which animates and
	 * cannot be stopped by anything. See `$lib/player/animation`, which measures it, and
	 * `StillView`, which answers the same fact by not offering the control at all.
	 *
	 * A cell cannot do that. The hold is the wall's control and it is right for every clip on the
	 * wall, so the honest answer is to keep the control and say what it could not reach, rather
	 * than show a paused wall with one picture still moving in it and nothing saying why.
	 *
	 * Only while something is held, because until then there is nothing to explain, and over the
	 * cells the hold actually landed on: everything drawn when the whole wall is stopped, this cell
	 * alone when it is the one that was stopped.
	 */
	const heldCells = $derived(wall.paused ? wall.cells : cell.paused ? [cell] : []);

	/*
	 * What a control on this row is called, naming what it will actually land on.
	 *
	 * The names are not decoration here: several of these are icon-only, so the label is the whole
	 * of what a screen reader and a speech command have, and a button that says "cell 2" while it
	 * is about to act on nine is the one kind of wrong label that cannot be recovered from by
	 * looking at the screen.
	 */
	function verb(what: string): string {
		return wall.everyCell ? `${what} in every cell` : `${what} in cell ${number}`;
	}

	const strip = $derived(usable(cell.sheet) ? cell.sheet : null);
	const mine = $derived(cell.playing !== null && cell.loop.owns(cell.playing.id));

	function togglePlay() {
		// The wall's own hold wins over a cell's: pressing play while everything is stopped means
		// start, and starting one cell out of a stopped wall is not a thing to guess at.
		if (wall.paused) {
			wall.togglePause();
			return;
		}
		// The cell on the bar decides which way this goes, so pressing it with every cell selected
		// does one thing to all of them rather than flipping each to the opposite of itself.
		const next = !cell.paused;
		acting.forEach((one) => (one.paused = next));
	}

	/* A timer of nothing is no timer. Zero is how the control says "wait for the file to end", which
	   is the same thing the absence of one means: one control, two readings, rather than a switch
	   beside a number. */
	function setTimer(seconds: number) {
		const held = seconds > 0 ? seconds : null;
		acting.forEach((one) => (one.timerSeconds = held));
	}

	/*
	 * FIVE SECONDS BACK, in everything this row is addressing.
	 *
	 * A RELATIVE move, which is why it reaches the whole wall where the scrubber above it does not:
	 * "five seconds ago" is true of nine clips at the same time, and "four minutes and eleven
	 * seconds in" is true of exactly the one whose timeline is drawn.
	 *
	 * Each cell is asked to seek itself, from its own position: the element is the only thing that
	 * can move a playhead, and a cell that is not on screen has handed nothing in.
	 */
	function stepBack() {
		acting.forEach((one) => one.seek?.(Math.max(0, one.position - SKIP_SECONDS)));
	}

	/*
	 * FIVE SECONDS ON, and it is the same relative move as its twin above.
	 *
	 * The keyboard has it (`theater.forward` is Right, and says 'five seconds on, in the cell you
	 * are on'), and so does the bar: a key with no button is a control only somebody who read the
	 * sheet knows about.
	 *
	 * Clamped against each cell's OWN length rather than the one drawn on this row: the row shows
	 * one cell's clock and this move lands on every cell the bar is addressing.
	 */
	function stepForward() {
		acting.forEach((one) =>
			one.seek?.(Math.min(one.duration || Number.POSITIVE_INFINITY, one.position + SKIP_SECONDS))
		);
	}

	function markNext() {
		if (cell.playing === null) return;
		cell.loop.mark(cell.playing.id, position);
	}

	/* What the A-B control says it will do next, the way the player's does. Two presses set the ends
	   of a loop and a third clears it, so the label is a different sentence at each step. */
	const abLabel = $derived(mine ? cell.loop.nextAction : 'Set the loop start');

	/*
	 * KEEP IT: the marked stretch, saved as a Loop: the player's control, on a cell.
	 *
	 * A cell has an A-B loop of its own, so the two ends can be marked here, and what they mark can
	 * be kept. The same call the
	 * player makes, in the same words: one press, one request, and the server marks the clip it
	 * produces rather than the film it was cut from. See `clipTheStretch`.
	 *
	 * It does not change the marks. What is on the timeline stays a thing for this sitting.
	 */
	let saving = $state(false);

	const savable = $derived(mine && cell.loop.running && !saving);

	/* The Save control is drawn whether or not it can act, so its label says why when it cannot. */
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

	/*
	 * The size this cell is being watched at.
	 *
	 * Only the thing holding the ELEMENT can change a stream under it and carry the playhead across,
	 * so the cell carries the act and its view installs it: the same seam `seek` and `replay` use.
	 * A cell plays through exactly the same ladder the player does, and this says which rung.
	 */
	const rungs = $derived(cell.plan?.qualities ?? []);
	const watchingAt = $derived(cell.quality?.url ?? cell.plan?.url);

	/* A choice needs two sizes. A picture, a GIF, or a clip with nothing below it has one. */
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

	/*
	 * CENTER STAGE'S TWO MODES, on the drawer of the bar the wall is being driven from.
	 *
	 * Here because it is a decision somebody makes WHILE watching ('stop changing under me' is
	 * thought at the wall, not on the settings screen) and the drawer is where this bar keeps the
	 * answers that are about how the thing behaves rather than about the playhead.
	 *
	 * It is still the account's preference underneath, and that is deliberate rather than a
	 * leftover: a mode that lived only in the browser would be forgotten every restart, and this
	 * app's preferences are one registry with one screen generated from it. So the drawer WRITES
	 * the setting, the same value the Theater section shows, which is the pattern the two
	 * controls beside it already follow.
	 */
	const CENTER_STAGE_KEY = 'theater.center_stage';

	const modeLabel = $derived(
		!wall.centerStage
			? 'Previews come with a Center stage layout'
			: wall.newestTakesFocus
				? 'A preview comes up as soon as it starts something new'
				: 'A preview comes up when you double-click it'
	);

	function setMode(newest: boolean) {
		wall.newestTakesFocus = newest;
		void saveSettings({ [CENTER_STAGE_KEY]: newest ? 'newest' : 'pick' }).catch(() => {
			// The wall is already in the mode that was asked for; what failed is remembering it for next
			// time. Said out loud rather than swallowed, because the control will look wrong tomorrow.
			toasts.show("That couldn't be saved as your preference", { tone: 'error' });
		});
	}

	/** Open one panel under the bar, and shut the other two. Only one of them fits. */
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
	shuffle={{
		on: cell.ordering === 'shuffle',
		onpress: () => pressShuffle(acting, cell.ordering === 'shuffle')
	}}
	repeat={{
		mode: cell.endBehaviour,
		onpress: () => {
			const next = nextLoopMode(cell.endBehaviour);
			acting.forEach((one) => (one.endBehaviour = next));
		}
	}}
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

<!--
	The two ways out, on the row, which is where the ordinary player keeps them. The Theater toolbar
	is at the top of the window and is not drawn at all while the wall fills it, so without these a
	filled wall would have a bar with everything on it except the two controls that take you off it.

	The wall's verbs rather than the player's: the corner takes the whole wall (a wall cut down to
	one cell is a different thing from what somebody was watching), and the screen is the shell's
	own fill rather than the media stage's, because that is what put this bar on screen.
	`FullscreenButton` is the player's answer to the same question and asks the media stage, which
	is not the thing filling the screen here.
-->
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

<!-- The drawer. Everything a cell has that a player does not, and nothing that would fit on the row.
     Dressed by the bar, which is what lets one drawer hold a caller's own controls. -->
{#snippet tray()}
	<!--
		The player's drawer, then the two controls a cell has that a player does not.

		Same icons, same order, same labels as the ordinary player's, which is the point of it: a
		cell's drawer holding a different set of controls for the same questions is the drift the
		one bar exists to stop.

		Twelve, always all twelve, so the drawer is one shape for a video, a GIF, any selection and
		any layout (Shuffle and what happens at the end stand on the bar beside the step pair): a control that does not apply to what is playing is dimmed with the reason
		as its label rather than left out. No divider: it would span the three columns and leave
		empty places beside it.
	-->
	<!-- Clip, then Screenshot, first, where the player's drawer keeps them: the same two controls
	     and the same doors (`keepTheLast`, `takeShot`), on this cell's file at this cell's moment.
	     Clip is this cell's alone, like Screenshot, so neither washes the wall. See `Cell.shot`. -->
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

	<!--
		KEEP THE MARKED STRETCH. Same glyph, same word, same request as the player's.

		Always drawn, and dimmed until both ends are marked, with the reason as its label. The drawer
		keeps one shape: a control that comes and goes moves every icon after it, so the drawer
		would change shape under the hand whenever a file or a mark changed. See `unsavable`.
	-->
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

	<!-- Somewhere else entirely, out of whatever this cell draws from. The player's `casino`, doing
	     for one cell what it does for the queue, and in the player's own word, Randomize, because a
	     drawer that reads differently on two surfaces is the drift one drawer exists to stop. -->
	<Tooltip label={ACTS.randomize} placement="top">
		<Button
			{...aims}
			tone="ghost"
			icon="casino"
			aria-label={verb(ACTS.randomize)}
			onclick={() => acting.forEach((one) => void one.somethingElse())}
		/>
	</Tooltip>

	<!--
		THE SIZE THIS CELL IS BEING WATCHED AT. Always drawn, and dimmed with the reason as its label
		where there is no choice (a picture, a GIF, a clip with nothing below it), so the drawer is one
		shape whatever the cell is playing.

		A cell plays through exactly the same ladder a player does (same plan, same rungs), and this
		says which one. On a wall it is the control that matters MOST, because four streams
		share one connection budget and one of them is often the reason the others stutter.

		`qualities?.` and not `qualities.`: the `?.` after `plan` guards the plan and nothing else, so
		a plan that arrives without the field throws on `.length` and takes the whole bar down. The
		field crosses the wire, so an older server or a fixture written before it existed produces
		exactly that plan.
	-->
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

	<!-- The facts, for whoever wants them. The player's fifth control, doing here what it does
	     there: never on screen by default, because this is a panel somebody opens rather than a
	     readout the picture has to share the frame with. -->
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

	<!-- Then the cell's own four. Hearing this one alone is meaningless in a player, which has
	     nothing to be alone from; the other three are what a cell draws and how long it holds it. -->
	<Tooltip label="Hear only this" placement="top">
		<Button
			{...aims}
			tone="ghost"
			icon="hearing"
			aria-label="Hear only cell {number}"
			onclick={() => wall.solo(index)}
		/>
	</Tooltip>

	<!--
		What this cell draws, as two controls in the drawer rather than behind one that opens a panel.

		A panel holding these two would be one press too many for a choice with two answers: a
		door in front of a room with two things in it. They are the same size and shape as everything else in the drawer, so they sit in it directly.

		Which of them is lit is the answer. Both are always drawn: a single toggle would say what
		the cell is set to and never what the other answer is.
	-->
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

	<!-- How long the cell holds one file. The only one of these that needs a NUMBER, so it is the
	     only one that still opens something: a single field under the bar, rather than a panel. -->
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

	<!-- Dimmed, with the reason as its label, where there is no strip to have a mode about, so the
	     drawer keeps its shape across layouts. See `setMode`. -->
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
		<!-- The app's own panel and the app's own buttons, and nothing portalled: a panel moved to the
		     end of the document is not drawn at all while the screen is filled, which is exactly where
		     somebody reaches for this. Held up while the pointer is in it, the way the timer field
		     below is: reading five sizes takes longer than the bar's idle clock. -->
		<div
			role="presentation"
			onpointerenter={(event) => frame?.hold(event)}
			onpointermove={(event) => frame?.hold(event)}
			onpointerleave={() => frame?.wake()}
		>
			<BarPanel row label={ACTS.quality}>
				{#each rungs as rung (rung.url)}
					<Button tone="ghost" pressed={watchingAt === rung.url} onclick={() => pickQuality(rung)}>
						<!-- The file's own entry says what it comes to as well as what it is
						     called, as in the player: "Original" names the file and says nothing
						     about its size.

						     No "may stutter" after a rung: the warning reads as a fault in the
						     file. `rung.smooth` still says which rungs the machine can carry. -->
						{rung.label}{rung.detail ? ` (${rung.detail})` : ''}
					</Button>
				{/each}
			</BarPanel>
		</div>
	{/if}

	{#if cell.timing}
		<!-- Held up the same way the bar is: reading it for a couple of seconds would take the
		     whole bar, field included, away under the pointer. -->
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
				<!-- `NumberInput`, not a bare `type="number"`: that draws the operating system's
				     own stepper arrows, in its look, at a size the page has no say over, and this
				     one sits over the video. It also commits on blur rather than per keystroke, so
				     typing 60 does not set the timer to 6 on the way past. -->
				<!-- Zero is a word, as the same setting reads under Playback: a bare 0 would read as a timer of
				     no time, where it means the cell waits for the file to end. -->
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
	/*
	 * Everything below is `:global` and namespaced, and both halves of that are deliberate.
	 *
	 * GLOBAL because these are written here and RENDERED inside the bar: a snippet is styled where
	 * it is drawn, so an ordinary scoped rule would reach nothing and every one of these controls
	 * would go out wearing the browser's own chrome.
	 *
	 * NAMESPACED because a global rule is exactly that: `.source` or `.settings` would dress
	 * something on a screen nobody was thinking about when this was written.
	 */
	/* As wide as the glyph and the field, at the start of the bar: the field says a few seconds, and
	   stretched across the bar it would be a long empty box with its words at the far end. */
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

	/* The glyph in front of the seconds field. `display: contents` on the label above puts its two
	   children straight into the panel's grid, so this box is a grid cell and has to centre what is
	   in it. Without this rule the icon sits on the text baseline a few pixels above the middle of
	   the field beside it. */
	:global(.cell-icon-label) {
		display: inline-flex;
		align-items: center;
		color: var(--sift-ink-2);
	}
</style>
