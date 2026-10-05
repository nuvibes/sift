<script lang="ts">
	/* One cell of the wall: the player's stage, sized to its file's shape, with a run and an
	   end-behaviour of its own. Not the player itself, whose queue and panels a cell must not have.
	   Every value Sift owns is written onto the element (`$lib/theater/element`), so a whole wall
	   starts inside one gesture. */
	import { onDestroy, untrack } from 'svelte';
	import { Pressable } from '$lib/components/common';
	import type { CellEcho } from '$lib/theater/echoes';
	import MediaStage from '$lib/components/player/MediaStage.svelte';
	import StageNotice from '$lib/components/player/StageNotice.svelte';
	import { noticeLabel, noticeWords } from '$lib/player/facts';
	import { seekTo } from '$lib/player/seek';
	import StatsPanel from '$lib/components/player/StatsPanel.svelte';
	import { attach, type Attachment, type PlaybackPlan } from '$lib/player/playback';
	import { canDriveAnimations, driveAnimation, type Animation } from '$lib/player/animation';
	import { restFor } from '$lib/player/dwell.svelte';
	import { thumbUrl } from '$lib/entity/art';
	import type { Cell, CellPicture } from '$lib/theater/cell.svelte';
	import { settle } from '$lib/theater/element';
	import { STALLED_WORDS, stallLimit, watchForStalls } from '$lib/theater/stall';
	import { ASSUMED } from '$lib/theater/fit';
	import { countView, type Wall } from '$lib/theater/wall.svelte';
	import { FilledClock, SpeedTimes } from '$lib/player/inside';
	import type { CellSitting } from '$lib/theater/cell.svelte';
	import ContextMenu from '$lib/components/common/ContextMenu.svelte';
	import { stage } from '$lib/components/shell/stage.svelte';
	import CellMenu from './CellMenu.svelte';
	import CellMarks from './CellMarks.svelte';

	interface Props {
		wall: Wall;
		cell: Cell;
		/** Which cell this is, from one. What the keyboard and the labels call it. */
		index: number;
		/** Whether the marks saying which cell is being heard are currently lit. See the page. */
		marksUp: boolean;
		/** Whether the wall's chrome is up, handed in because only the surface drawing the wall
		   knows its clock (`TheaterWall`). */
		chromeUp: boolean;
		/** Whether this cell says which number it is. Not in the corner panel. See `TheaterWall`. */
		numbered: boolean;
		/** What a key last did at this cell, or null where nobody is typing. See `KeyEcho`. */
		echo?: CellEcho | null;
		/** Open the picker that sets this cell's source. */
		onpick: () => void;
		/** Fill the screen, or stop. The shell's box, so the controls come with it. */
		onfullscreen: () => void;
		/** Where the wall's shape puts this cell, as a `grid-area`. Absent in the strip, which is not a
		 *  grid. See `TheaterWall`. */
		place?: string;
		/** A preview in the strip. */
		preview?: boolean;
		/** What a double press does instead of filling the screen: a preview comes up into the wall. */
		onpress?: () => void;
	}

	let {
		wall,
		cell,
		index,
		marksUp,
		chromeUp,
		numbered,
		echo = null,
		onpick,
		onfullscreen,
		place = undefined,
		preview = false,
		onpress = undefined
	}: Props = $props();

	/** The cell's own box: what the menu hangs off, and what the marks are positioned against. */
	let frame = $state<HTMLElement | null>(null);

	let video = $state<HTMLVideoElement | null>(null);
	/** Where the element last said it was, for the handoff. Plain rather than state: nothing draws
	    it, and the element that said it may already be gone by the time it is read. */
	let reached = 0;
	/* The GIF on screen, and the canvas that holds still frame of it. See `heldStill`. */
	let picture = $state<HTMLImageElement | null>(null);
	let frozen = $state<HTMLCanvasElement | null>(null);
	/** The canvas an UNDRIVEN GIF is frozen onto. See `holdTheFrame`. */
	let stillFrame = $state<HTMLCanvasElement | null>(null);
	/** Whether that frozen frame is what is on screen right now. */
	let showingFrame = $state(false);
	/** How many pictures this cell has loaded, which reruns the frame-holding effect; a count,
	   since two loads can leave one size. */
	let pictureLoads = $state(0);
	/* Written onto the cell, which the filled window's one bar reads (`Cell.position`). */
	const position = $derived(cell.position);
	const duration = $derived(cell.duration);
	let attachment: Attachment | null = null;
	/** Which file the element is currently pointed at, so a change is noticed once. */
	let attached: string | null = null;
	let timer: ReturnType<typeof setTimeout> | null = null;
	/** How long a GIF is held, which is the only end an `<img>` can be given. */
	let heldFor: ReturnType<typeof setTimeout> | null = null;

	const number = $derived(index + 1);
	/* Whether the wall's chrome has gone quiet, which the number follows: the bar's condition, since a
	   cell has no bar of its own. */
	const chromeGone = $derived(!chromeUp);
	const audible = $derived(wall.isAudible(index));

	/* Which press this cell flashes for; a number, so `{#key}` replays the wash. */
	let flashing = $state<number | null>(null);

	/* A cell chosen by its number says so, read from the counter inside `untrack`; `> 0` so cell
	   one does not flash as the wall opens (`Wall.chooseByNumber`). */
	$effect(() => {
		const times = wall.chosenTimes;
		if (times === 0) return;
		if (!untrack(() => wall.everyCell || wall.focused === index)) return;
		flashing = times;
	});
	/* Both marks fade together once the sound has been still. A wall that permanently outlines one
	   cell is carrying a decoration rather than an answer. */
	const marked = $derived(audible && marksUp);
	/* A picture, a GIF included: a `<video>` draws a GIF blank. */
	const still = $derived(cell.playing !== null && cell.playing.media_type !== 'video');

	/* What the cell says about how its file plays: a conversion (which wins) or a corrected copy,
	   in `noticeWords`, never on a still or an empty cell. */
	const markWords = $derived.by(() => {
		if (still || cell.playing === null) return '';
		return noticeWords(cell.plan, cell.repair);
	});

	const markLabel = $derived(noticeLabel(cell.plan, cell.repair));
	/** A GIF, which ends, several times a second, rather than never. See `restFor`. */
	const animation = $derived(cell.playing?.media_type === 'gif');

	/* Whether this GIF is ours to drive: decoded into a canvas a hold really stops, or, without
	   WebCodecs (a secure context), a plain `<img>` (`$lib/player/animation`). */
	const driven = $derived(animation && canDriveAnimations());
	/** The GIF this cell is playing, while it is playing one. */
	let running: Animation | null = null;
	/** Which file the GIF was started for, so a re-render does not start it again. */
	let animating: string | null = null;

	/* The wall's answer (`Cell.shape`), so the cell and its row agree; `ASSUMED` as `fit.ts` does. */
	const shape = $derived(`${cell.shape ?? ASSUMED}`);
	/* What the player measured for the file on screen, for the facts panel. */
	const seen = $derived(cell.measured?.file === cell.playing?.id ? cell.measured : null);

	const poster = $derived(
		cell.playing?.thumb ? thumbUrl({ id: cell.playing.id, art: cell.playing.art }) : undefined
	);

	/* The poster's size, for a video with no stored size, so its cell has a shape before the video. */
	$effect(() => {
		const file = cell.playing;
		const url = poster;
		if (!file || !url || still || (file.width && file.height)) return;
		const image = new Image();
		image.onload = () => cell.measurePoster(file.id, image.naturalWidth, image.naturalHeight);
		image.src = url;
		return () => (image.onload = null);
	});

	/* A picture's own file, off the plan; a GIF's thumbnail is one frame. */
	const pictureUrl = $derived(still ? (cell.plan?.url ?? null) : null);

	/* The stall clock, and whether this file has had its one fresh request. Declared ahead of the
	   effect that resets both on a new file; see `stalledOut` for what they are for. */
	let recovered = false;
	const watch = watchForStalls(stalledOut, () => stallLimit(cell.plan));

	/* Point the element at the plan; emptying it drops the source, closing the stream. */
	$effect(() => {
		const element = video;
		const plan = cell.plan;
		const id = cell.playing?.id ?? null;

		/* No element (a still replaced it) still has to let go, or the clip before it keeps its
		   stream. */
		if (!element) {
			attachment?.detach();
			attachment = null;
			attached = null;
			return;
		}
		if (id === attached) return;

		attachment?.detach();
		attachment = null;
		attached = id;
		cell.position = 0;
		cell.duration = 0;
		// A new file has its own one recovery, and nothing is being waited for yet.
		watch.stop();
		recovered = false;

		if (id === null || plan === null) {
			release(element);
			return;
		}
		connect(element, plan);
	});

	/* Hand the element the plan and start it unless the wall is held, for a new file or a stalled
	   one asked again. `attach` retries the recoverable errors and reports a fatal one. */
	function connect(element: HTMLVideoElement, plan: PlaybackPlan): void {
		attachment = attach(element, plan, brokeDown);
		settle(element, { muted: muted(), volume: cell.volume / 100, rate: cell.rate });
		if (!wall.paused) start(element);
	}

	/* A picture that has stopped arriving (`$lib/theater/stall`): once the clock runs out it is
	   asked for again once from where it reached, and a second stall is a failure. Only while the
	   cell is meant to move. */
	function stalledOut() {
		const element = video;
		const plan = cell.plan;
		if (!element || plan === null || attached === null) return;
		/* A hold stops the clock where it pauses the element (see the hold's own effect); this is
		   the same fact read off the element, for a pause that arrived by any other road. */
		if (element.paused || element.ended) return;
		if (!recovered) {
			recovered = true;
			attachment?.detach();
			if (reached > 0) cell.resumeAt = reached;
			connect(element, plan);
			/* The clock runs again: a request that reaches no byte may say nothing. */
			watch.waiting();
			return;
		}
		void cell.failed(STALLED_WORDS);
	}

	/* Sift's answers, written again whenever one of them changes. A value that is only set at attach
	   drifts the moment anything moves it: the master mute, a solo, a volume drag. */
	$effect(() => {
		if (video) settle(video, { muted: muted(), volume: cell.volume / 100, rate: cell.rate });
	});

	/* Start the GIF this cell shows and let go of the last, keyed on the file and guarded by
	   `animating` so a rerun never decodes it twice. */
	$effect(() => {
		const canvas = frozen;
		const url = pictureUrl;
		const id = driven ? (cell.playing?.id ?? null) : null;
		if (id === animating) return;
		running?.close();
		running = null;
		animating = id;
		if (id === null || canvas === null || url === null) return;
		void driveAnimation(url, canvas, {
			held: wall.paused || cell.paused,
			onsize: (size) => {
				cell.measure(id, size.width, size.height);
				/* Its first frame has been decoded into the canvas, which is when a driven GIF
				   is on screen: the same moment `shown` is for one drawn as an ordinary picture. */
				onScreen(id);
			},
			onfail: brokeDown
		}).then((made) => {
			// The cell may have moved on while the file was being fetched, and then this belongs to
			// nothing: close it rather than leaving a decoder drawing into a canvas somebody else has.
			if (made === null) return;
			if (animating !== id) made.close();
			else running = made;
		});
	});

	/* Holding a GIF this browser will not let us drive: the frame on screen is copied onto a canvas
	   in its place while the GIF runs on behind it. It waits for a decoded frame (`pictureLoads`
	   reruns it), since a wall opens held. */
	$effect(() => {
		const held = wall.paused || cell.paused;
		/* Read for the dependency, not for the number: this has to run again when a picture finishes
		   loading, because that is the moment there is finally something to copy. */
		void pictureLoads;
		if (!animation || driven) {
			showingFrame = false;
			return;
		}
		if (!held) {
			showingFrame = false;
			return;
		}
		const canvas = stillFrame;
		const image = picture;
		if (canvas === null || image === null || image.naturalWidth === 0) return;
		canvas.width = image.naturalWidth;
		canvas.height = image.naturalHeight;
		canvas.getContext('2d')?.drawImage(image, 0, 0);
		showingFrame = true;
	});

	/* Held or running, from the same two holds every other kind of file on this wall obeys. */
	$effect(() => {
		const held = wall.paused || cell.paused;
		if (running === null) return;
		if (held) running.pause();
		else running.play();
	});

	/* Playing or held, from the wall's control: held, not torn down. */
	$effect(() => {
		const element = video;
		if (!element || attached === null) return;
		// Either hold stops it: the wall's, and this cell's own.
		if (wall.paused || cell.paused) {
			element.pause();
			// A held cell is not expected to move, so it is not waiting for anything.
			watch.stop();
		} else start(element);
	});

	/* The cell's own clock, a second way to move on: on a video it cuts a long file short; on
	   photographs (no end to reach) it is the only way, so they get one. */
	$effect(() => {
		const seconds = cell.timerSeconds;
		const showing = cell.playing?.id;
		const held = wall.paused || cell.paused;
		if (timer) clearTimeout(timer);
		timer = null;
		if (held || showing === undefined || seconds === null || seconds <= 0) return;
		timer = setTimeout(() => void cell.advance(), seconds * 1000);
	});

	/* A GIF's end, its length twice through (`restFor`); `ended`, so Repeat this works. */
	$effect(() => {
		const playing = cell.playing;
		const held = wall.paused || cell.paused;
		if (heldFor) clearTimeout(heldFor);
		heldFor = null;
		if (held || playing === null || playing.media_type !== 'gif') return;
		const rest = restFor('gif', playing.duration_ms, { pictures: false });
		if (rest === null) return;
		heldFor = setTimeout(() => void cell.ended(), rest);
	});

	onDestroy(() => {
		watch.stop();
		running?.close();
		running = null;
		if (heldFor) clearTimeout(heldFor);
		if (timer) clearTimeout(timer);
		/* Where the clip had reached is the cell's, written back before the element goes, so a wall
		   handed between the screen and the corner panel resumes each clip (see `known`). */
		if (cell.playing !== null && reached > 0) cell.resumeAt = reached;
		// Before the element goes. What this cell gave the file it was holding is otherwise simply
		// lost, and closing the wall is the commonest way a cell ends.
		reportWatched();
		attachment?.detach();
		if (video) release(video);
	});

	/** Whether this cell's element should be silent, which is either answer saying so. */
	function muted(): boolean {
		return wall.masterMuted || cell.muted;
	}

	/* Drop the source and reload. Removing the element is not enough: the memory and the socket both
	   outlive it, and a paused element costs very nearly what a playing one does. */
	function release(element: HTMLVideoElement): void {
		element.removeAttribute('src');
		element.load();
	}

	function start(element: HTMLVideoElement): void {
		// Nothing is awaited before this. An await between the press and the play loses the user
		// activation on one of the two engines, and the whole wall then refuses to start.
		void element.play().catch(() => {
			// A refused play is a cell that is not moving, which is visible. Nothing to say.
		});
	}

	function tick() {
		if (!video) return;
		// The picture moved, so whatever a stall clock was waiting for has arrived.
		if (video.currentTime !== reached) watch.moving();
		reached = video.currentTime;
		cell.position = video.currentTime;
		const loop = cell.loop;
		const id = cell.playing?.id;
		// The stretch between the two marks, played over and over. Enforced here because seeking is
		// the element's (only it can move the playhead) while which stretch is the cell's.
		if (id && loop.owns(id) && loop.running && loop.b !== null && video.currentTime >= loop.b) {
			video.currentTime = loop.a ?? 0;
		}
	}

	/** Whether this cell is waiting for a seek to land before it may be seen. See `known`. */
	let arriving = $state(false);

	function known() {
		if (!video) return;
		cell.duration = Number.isFinite(video.duration) ? video.duration : 0;
		/* Resume a clip from the strip, once the element knows its length. */
		if (cell.resumeAt !== null) {
			const at = Math.min(cell.resumeAt, cell.duration || cell.resumeAt);
			cell.resumeAt = null;
			/* Hidden until the seek lands: a browser paints the first frame before it honours the seek,
			   and the flash reads as the clip starting again. */
			arriving = true;
			video.currentTime = at;
			cell.position = at;
		}
		const id = cell.playing?.id;
		if (id !== undefined) cell.measure(id, video.videoWidth, video.videoHeight);
	}

	/** The same, for a photograph, which has no metadata event and no element to ask. */
	function shown(event: Event) {
		const image = event.currentTarget as HTMLImageElement;
		pictureLoads += 1;
		// And the picture is ON SCREEN, which is the moment its time starts. See `onScreen`.
		const id = cell.playing?.id;
		if (id === undefined) return;
		cell.measure(id, image.naturalWidth, image.naturalHeight);
		onScreen(id);
	}

	/* This file cannot be played here: one handler for the element's `error`, the picture's and a
	   fatal stream failure, as one fact about the cell (`Cell.failed`). */
	function brokeDown() {
		void cell.failed();
	}

	/* A cell times what it shows and reports it on moving off, with no position, so a shuffled file
	   never offers to resume. The sitting is the cell's (`Cell.sittingWith`); `onScreen` is the one
	   door. */
	let watchingId: string | null = null;
	let sitting: CellSitting | null = null;
	let watchedMs = 0;
	let watchingFrom = 0;
	/* What happened inside the sitting (`$lib/player/inside`): time at each speed, time filled and
	   plays through; made afresh per file. */
	let speedTimes = new SpeedTimes();
	let filled = new FilledClock();
	let passes = 0;

	function playing() {
		watch.moving();
		const id = cell.playing?.id;
		/* Told BEFORE, and outside, the counting question: a run of failures ending is a fact about
		   playback, not a preference. */
		cell.started();
		/* And the wall, where Center Stage is decided, on the element's own start: an effect would
		   fire on the swap it caused. */
		wall.previewStarted(index, cell.playing?.id ?? null);
		if (id === undefined) return;
		onScreen(id);
	}

	/* A file is on screen in this cell: a different file finishes the last sitting first. The clock
	   starts only while nothing holds the wall, since a wall OPENS held; the hold's effect below
	   starts it on release. */
	function onScreen(id: string) {
		if (id !== watchingId) {
			reportWatched();
			watchingId = id;
			watchedMs = 0;
			speedTimes = new SpeedTimes();
			filled = new FilledClock();
			filled.start();
			passes = 0;
			sitting = cell.sittingWith(id);
			wall.session?.shown(id);
		}
		if (!watchingFrom && !(wall.paused || cell.paused)) watchingFrom = performance.now();
	}

	/** Fold the running stretch into the total. Called whenever the cell stops holding the file on
	 *  screen: a hold, a change of file, the wall going away. */
	function foldIn() {
		if (!watchingFrom) return;
		const spent = performance.now() - watchingFrom;
		watchedMs += spent;
		// A speed is a video's; a picture or a GIF on a wall is shown, not played at a rate.
		if (video) speedTimes.add(cell.rate, spent);
		watchingFrom = 0;
	}

	/* Send what this cell gave one file, and forget it. Fire and forget with `keepalive`, because
	   the commonest way a wall ends is the window closing. */
	function reportWatched() {
		foldIn();
		const piece = sitting;
		const spent = Math.round(watchedMs);
		filled.stop();
		const inside = {
			speeds: speedTimes.read(),
			fullscreen_ms: filled.read(),
			completions: passes
		};
		watchingId = null;
		sitting = null;
		watchedMs = 0;
		if (piece === null || spent <= 0) return;
		void countView(piece, spent, wall.session?.id ?? null, inside);
	}

	/* A hold stops the clock. A wall paused overnight is not a wall that watched all night, and the
	   whole reason the threshold exists is to keep a number like that out of the library. */
	$effect(() => {
		if (wall.paused || cell.paused) foldIn();
		else if (watchingId !== null && !watchingFrom) watchingFrom = performance.now();
	});

	function ended() {
		watch.stop();
		// Counted before the cell moves on: a played-through file is a pass whatever comes next.
		if (watchingId !== null) passes += 1;
		void cell.ended();
	}

	function seek(seconds: number) {
		if (video) seekTo(video, seconds);
		cell.position = seconds;
	}

	/* The cell is handed what only this component can do, taken back on the way out so no bar seeks
	   an element that let go of its stream. */
	$effect(() => {
		cell.seek = seek;
		cell.replay = replay;
		cell.shot = pictured;
		return () => {
			if (cell.seek === seek) cell.seek = null;
			if (cell.replay === replay) cell.replay = null;
			if (cell.shot === pictured) cell.shot = null;
		};
	});

	/* What a screenshot is taken from: the video or the picture showing, and the cell's box. */
	function pictured(): CellPicture {
		const still = video ? null : driven ? frozen : showingFrame ? stillFrame : picture;
		return { video, still, stage: frame };
	}

	/* Play this file again from the top (Repeat this): an ENDED element is paused, so seek to zero
	   and then play, not awaited, or one engine loses the user activation. */
	function replay() {
		if (!video) return;
		video.currentTime = 0;
		cell.position = 0;
		start(video);
	}

	/* A press chooses this cell and nothing else, since every control acts on the chosen cell. */
	function pressed() {
		wall.focus(index);
	}

	/* A double press fills the screen; in the strip it brings the preview up instead. */
	function pressedTwice() {
		if (onpress) {
			onpress();
			return;
		}
		wall.focus(index);
		onfullscreen();
	}
</script>

<!-- Handed to the stage only while this cell is converting: a snippet written inside the tag is
     passed whatever happens, and the stage would draw an empty mark on every cell. -->
{#snippet cellNotice()}
	<!-- Neither sentence is written here: a conversion's reason is the SERVER'S, and the interleave
	     sentences are shared with the file's own page, so they agree. -->
	<StageNotice label={markLabel}>{markWords}</StageNotice>
{/snippet}

<!-- DRESSED BY: .chosen (CellMarks draws the chosen look from its own `chosen` prop; the class names the cell's state) -->
<section
	class="cell"
	class:audible={marked}
	class:chosen={wall.focused === index}
	style:grid-area={place}
	style:--shape={shape}
	aria-label="Cell {number}"
	bind:this={frame}
>
	<!-- The cell's menu, inside the cell because the section is the grid item; `portalTo` draws it
	     in whatever fills the window. The browser's own menu is refused, and a cell has no
	     transport. -->
	<ContextMenu
		items={cellMenu}
		label="Cell {number}"
		triggerClass="cell-trigger"
		portalTo={stage.whatFillsTheWindow}
	>
		<MediaStage notice={markWords ? cellNotice : undefined}>
			{#snippet media()}
				{#if cell.playing && !still}
					<!-- `muted`, `preload`, `loop`, `autoplay` and `playsinline` are written from
					     script (`$lib/theater/element`) and here too, so the first paint is already
					     Sift's. -->
					<!-- svelte-ignore a11y_media_has_caption -->
					<video
						bind:this={video}
						class="picture"
						class:arriving
						{poster}
						muted
						preload="none"
						playsinline
						ontimeupdate={tick}
						onloadedmetadata={known}
						onseeked={() => (arriving = false)}
						onplaying={playing}
						onwaiting={watch.waiting}
						onstalled={watch.waiting}
						onended={ended}
						onerror={brokeDown}
						onclick={pressed}
						ondblclick={pressedTwice}
					></video>
				{:else if cell.playing && pictureUrl}
					<!-- A photograph or a GIF: a photograph waits for the cell's timer, a GIF is
					     given a length (`heldFor`). A button, so pressing a cell is reachable from
					     a keyboard. -->
					<Pressable
						class="still"
						feedback="none"
						radius="md"
						onclick={pressed}
						ondblclick={pressedTwice}
					>
						<!-- One of two, by what the BROWSER can do: a GIF it can decode is played into a canvas
						     so a hold really stops it; anything else is an `<img>`. See `driven`. -->
						{#if driven}
							<canvas class="picture" bind:this={frozen}></canvas>
						{:else}
							<!-- Both mounted: copying the held frame needs the image. -->
							<img
								class="picture"
								class:hidden={showingFrame}
								bind:this={picture}
								src={pictureUrl}
								alt=""
								onload={shown}
								onerror={brokeDown}
							/>
							<canvas class="picture" class:hidden={!showingFrame} bind:this={stillFrame}></canvas>
						{/if}
					</Pressable>
				{/if}

				{#if cell.state !== 'ready'}
					<!-- What the cell is doing, on a button (styled via `.cell`). -->
					<Pressable
						class="say-press"
						feedback="none"
						radius="sm"
						onclick={pressed}
						ondblclick={pressedTwice}
					>
						<span class="say">
							<!-- An empty cell while the wall is opening is busy rather than unchosen, so it falls
							     through to the loading sentence. See `Wall.opening`. -->
							{#if cell.state === 'empty' && !wall.opening}
								Nothing chosen yet — pick what this cell should play.
							{:else if cell.state === 'loading' || cell.state === 'empty'}
								Finding something to play&hellip;
							{:else if cell.state === 'waiting'}
								Waiting for the converter — this install converts one file at a time.
							{:else if cell.state === 'stopped'}
								Stopped at the end.
							{:else if cell.state === 'nothing_here' && cell.unreachable}
								Sift can't reach the files this cell found. A drive may not be mounted, or a folder
								may have moved.
							{:else if cell.state === 'nothing_here'}
								Nothing here matches what this cell is set to.
							{:else}
								{cell.problem ?? "That file couldn't be played."}
							{/if}
						</span>
					</Pressable>
				{/if}

				<CellMarks
					{number}
					{numbered}
					quiet={chromeGone}
					{marked}
					chosen={wall.focused === index}
					aimed={wall.aimedAt(index)}
					{flashing}
					onflashed={() => (flashing = null)}
					waits={cell.narrowingWaits}
					{echo}
				/>

				<!-- The player's facts, in the player's corner and words. It does not fade with the bar:
				     a panel somebody switched on and is reading. -->
				{#if cell.factsOpen}
					<StatsPanel
						file={cell.facts}
						plan={cell.plan}
						{position}
						{duration}
						media={video}
						playingWide={seen?.width ?? 0}
						playingTall={seen?.height ?? 0}
						source={cell.source.trim() || 'Everything'}
						state={cell.state}
						{still}
						kind={still ? (animation ? 'GIF' : 'Photo') : null}
						label="Stats for nerds, cell {number}"
					/>
				{/if}
			{/snippet}
		</MediaStage>
	</ContextMenu>
</section>

<!-- What the menu SAYS, handed to it as rows. Its own file, because a cell has a dozen verbs and
     the list of them is worth reading on its own. -->
{#snippet cellMenu()}
	<CellMenu {wall} {cell} {index} {onpick} ontimer={() => (cell.timing = true)} />
{/snippet}

<style>
	/* The cell takes the place the layout gave it, and the stage fills the cell: `--stage-height` is
	   sized for a dialog by default, and a wall wants every pixel of its box. */
	.cell {
		position: relative;
		min-inline-size: 0;
		min-block-size: 0;
		/* The row's height; the fallback is a floor for absolute children. */
		aspect-ratio: var(--shape, auto);
		/* The row height from the wall (`$lib/theater/fit`), or the whole row. */
		block-size: var(--feed-height, 100%);
		inline-size: auto;
		/* Centred rather than stretched, both ways: a stretched grid item would pull the height back
		   out to the whole row and undo the shape. */
		align-self: center;
		justify-self: center;
		--stage-height: 100%;
		/* Square corners: on a wall the feeds meet, and a rounded corner between two is a notch. */
		--stage-radius: 0;
		/* A cell arriving says the wall changed shape (`rise`). */
		animation: rise var(--dur-base) var(--ease);
	}

	/* The menu's trigger box, exactly the cell (`:global`: the class is handed on). The height is
	   load-bearing: `--stage-height: 100%` resolves against it. */
	:global(.cell-trigger) {
		block-size: 100%;
		inline-size: 100%;
	}

	/* Not `display: none`: the element has to stay laid out and decoding, which is the whole of
	   what it is doing while this is on. See `known`. */
	.picture.arriving {
		visibility: hidden;
	}

	/* One of the pair is always out of the way. `display: none` rather than a transparent element:
	   the hidden one must not take the pointer, and an `<img>` with no size takes no layout. */
	.picture.hidden {
		display: none;
	}

	/* The fit is the stage's one rule (`MediaStage`): fitted and centred, never cropped, so a file in
	   a cell held to another shape letterboxes. What is the cell's own is the pointer. */
	.cell .picture {
		cursor: pointer;
	}

	/* The photograph's press target fills the cell. `feedback="none"` because the cell already
	   answers the pointer. `:global` because the class is handed to a component. */
	.cell :global(.still) {
		inline-size: 100%;
		block-size: 100%;
	}

	/* Less movement means the cell fades in without moving or scaling. It is not removed: knowing
	   the shape changed is the point of it. */
	:global(:root[data-motion='reduce']) .cell {
		animation-name: appear;
	}

	/* The cell somebody is hearing. Inside the frame rather than around it, so it cannot be clipped
	   by whatever the wall does at its edges. */
	.cell::after {
		content: '';
		position: absolute;
		inset: 0;
		z-index: 4;
		border: 2px solid var(--sift-accent);
		opacity: 0;
		transition: opacity var(--dur-base) var(--ease);
		pointer-events: none;
	}

	.cell.audible::after {
		opacity: 1;
	}

	/* The overlay saying what the cell is doing, on the shared `Pressable` (`:global` under
	   `.cell`): the button is the box, the sentence keeps the type and the fade. */
	.cell :global(.say-press) {
		position: absolute;
		inset: 0;
		z-index: 1;
		display: grid;
		place-items: center;
		padding: var(--space-5);
	}

	.say {
		animation: appear var(--dur-fast) var(--ease);
		margin: 0;
		color: var(--sift-ink-2);
		font: var(--text-body);
		text-align: center;
	}
</style>
