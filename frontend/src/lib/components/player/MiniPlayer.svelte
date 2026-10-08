<script lang="ts">
	/* The player as a small panel over the app, drawn by the shell so it stays across screens, and
	   kept inside the window at every step. The same `Player` the full-size view uses. */
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { offerViewer } from '$lib/remote/offer.svelte';
	import { onMount, untrack } from 'svelte';
	import { arrive } from '$lib/shell/motion.svelte';
	import { handPlace, screenChanges, stageTransition, takePlace } from './motion';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { Button, Empty, KeyEcho, Withheld } from '$lib/components/common';
	import { muteEcho, type Echo } from '$lib/theater/echoes';
	import Player from './Player.svelte';
	import MiniBar from './MiniBar.svelte';
	import MiniOverlay from './MiniOverlay.svelte';
	import { inThePage } from './in-the-page';
	import { inTheCorner, newSitting } from '$lib/player/sitting.svelte';
	import type { PlaybackPlan } from '$lib/player/playback';
	import StillView from './StillView.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { handover, heldOf, mini, resized, shapedTo, type Grip } from '$lib/player/mini.svelte';
	import {
		canStepBack,
		canStepForward,
		leaveAssetPanel,
		lookAhead,
		playOn,
		reopenAsset,
		runGoesOn,
		stepBack,
		stepForward,
		takeRecord
	} from '$lib/player/asset-view';
	import { dwell } from '$lib/player/dwell.svelte';
	import { run } from '$lib/player/run.svelte';
	import TheaterWall from '$lib/components/theater/TheaterWall.svelte';
	import { showing as theater } from '$lib/theater/wall.svelte';
	import { pressed, stepAsked } from '$lib/shell/shortcuts';
	import { panelKeys } from './mini-keys';
	import { api, isMissing } from '$lib/api/client';
	import { firstClip, type Onward } from './audio-run';
	import { libraryChanges, whenChanged } from '$lib/library/changes.svelte';
	import { saveToDevice } from '$lib/capture/copy-out';
	import type { components } from '$lib/api/schema';
	import { phoneWidth } from '$lib/components/common/phone-width.svelte';
	import { ACTS, keyOf } from '$lib/player/acts';

	/** What the panel is showing. Null the rest of the time, and then nothing is drawn. */
	const asset = $derived(mini.asset);

	/* Where a clip in the corner is watched (`inTheCorner`). */
	const corner = $derived(inTheCorner(asset?.from));

	/* A picture rather than a clip; a GIF is one, since a `<video>` shows it blank. */
	const showsPicture = $derived(
		asset !== null && asset.mediaType !== undefined && asset.mediaType !== 'video'
	);

	/* Hidden, read before the media type: a concealed placeholder arrives with an empty type. */
	const concealed = $derived(asset?.concealed === true);

	/* A file deleted elsewhere leaves the panel, whichever shape it is drawn in. */
	whenChanged(libraryChanges, () => {
		const id = asset?.id;
		if (!id || concealed) return;
		void api.get(`/assets/${id}`).catch((error) => {
			if (isMissing(error) && mini.asset?.id === id) mini.close();
		});
	});
	/* What the panel says it is holding, a word for each kind of thing. */
	const holding = $derived(
		mini.wall ? 'Theater' : concealed ? 'Hidden' : showsPicture ? 'Showing' : 'Playing'
	);

	/** How far the panel has got, so going back to full size lands where it left off. */
	let playedTo = $state(0);

	/** The window, as the thing the panel is kept inside, less whatever is drawn above the app. */
	function within() {
		/* The desktop shell's drag strip, where a panel could not be grabbed back. */
		const chrome = Number.parseFloat(
			getComputedStyle(document.documentElement).getPropertyValue('--window-chrome')
		);
		return {
			width: window.innerWidth,
			height: window.innerHeight,
			top: Number.isFinite(chrome) ? chrome : 0
		};
	}

	/* The same file open at full size: one of the two players goes, whichever arrived first. */
	const openFullSize = $derived(
		asset !== null && (page.state.asset === asset.id || page.url.pathname === `/asset/${asset.id}`)
	);

	/* The view is on its way out, so a second run of the effect does not go back twice. */
	let leaving = false;

	$effect(() => {
		if (!openFullSize) {
			// Cleared any earlier, this would close the panel the view had just filled.
			if (mini.handover) mini.left();
			leaving = false;
			return;
		}
		if (!mini.handover) {
			mini.close();
			return;
		}
		if (leaving) return;
		leaving = true;
		untrack(() => {
			// The way the panel's own close leaves.
			leaveAssetPanel();
		});
	});

	/* A picture's sitting: carried on from the panel when handed over, its own when stepped to. */
	const still = newSitting();

	$effect(() => {
		const showing = asset !== null && showsPicture && !concealed ? asset : null;
		untrack(() => {
			if (showing === null) {
				still.end();
				return;
			}
			if (still.on === showing.id) return;
			const handed = showing.sitting;
			if (handed && handed.asset === showing.id) still.resume(handed);
			else still.start(showing.id, inTheCorner(showing.from));
		});
	});

	onMount(() => {
		const onLeave = () => still.end();
		window.addEventListener('pagehide', onLeave);
		return () => {
			window.removeEventListener('pagehide', onLeave);
			still.end();
		};
	});

	onMount(() => {
		const onResize = () => mini.reflow(within());
		window.addEventListener('resize', onResize);
		return () => window.removeEventListener('resize', onResize);
	});

	/* A drag in progress, its shape read once at the start so it never chases its own frame. */
	let gesture: {
		kind: 'move' | Grip;
		x: number;
		y: number;
		place: typeof mini.place;
		shape?: number;
	} | null = null;

	/** What is in the panel as width over height, or nothing where there is no single answer. */
	function shapeOf(): number | undefined {
		if (mini.wall) return undefined;
		const size = showsPicture ? picture?.pictureSize() : player?.pictureSize();
		if (!size || size.width <= 0 || size.height <= 0) return undefined;
		return size.width / size.height;
	}

	function begin(kind: 'move' | Grip, event: PointerEvent) {
		if (event.button !== 0) return;
		/* Never a press on a header button: the header captures the pointer, so the release would go
		   to the header and the button's click would never happen. */
		if ((event.target as HTMLElement | null)?.closest('button')) return;
		gesture = {
			kind,
			x: event.clientX,
			y: event.clientY,
			place: { ...mini.place },
			shape: kind === 'move' ? undefined : (mini.shape ?? shapeOf())
		};
		// So a fast drag that outruns the panel does not stop when it leaves it.
		(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
		event.preventDefault();
	}

	/** Where the panel would be, given how far the pointer has come since the gesture began. */
	function reached(event: PointerEvent) {
		const acrossBy = event.clientX - gesture!.x;
		const downBy = event.clientY - gesture!.y;
		const from = gesture!.place;
		return gesture!.kind === 'move'
			? { ...from, x: from.x + acrossBy, y: from.y + downBy }
			: resized(from, gesture!.kind, acrossBy, downBy, gesture!.shape);
	}

	function drag(event: PointerEvent) {
		if (!gesture) return;
		// Stored only at the end: a synchronous write per pointer event would stall the drag.
		mini.moveTo(reached(event), within());
	}

	function end(event: PointerEvent) {
		if (!gesture) return;
		mini.settle(reached(event), within());
		gesture = null;
		(event.currentTarget as HTMLElement).releasePointerCapture?.(event.pointerId);
	}

	/* Back to full size where the panel had got to, as a new entry so closing returns here. */
	function expand(fill = false) {
		if (!asset) return;
		const returning = asset.id;
		const at = Math.round(playedTo * 1000);
		// So the full-size view does not play what was deliberately paused.
		const held = player?.isPaused() ?? false;
		handPlace(panel?.getBoundingClientRect() ?? null);
		mini.close();
		if (held) handover.wasPaused(returning);
		if (fill) handover.fillsTheScreen(returning);
		reopenAsset(returning, at > 0 ? at : undefined);
	}

	/*
	 * A wall going back to full size: closing the panel hands the wall back, and the navigation puts
	 * it on Theater, since the panel is often closed from somewhere else entirely.
	 */
	function backToWall() {
		mini.close();
		void goto(resolve('/theater'));
	}

	/* Whether anything else on screen is playing: the panel answers its own keys only when it is the
	   only thing playing, since a Theater wall binds Space to stopping everything. */
	const somethingElsePlaying = $derived(
		page.state.asset !== undefined ||
			page.url.pathname.startsWith('/asset/') ||
			page.url.pathname === '/theater'
	);

	/* Save what is in the corner to this device (Ctrl-S: the panel has no menu). The name and kind
	   are fetched when asked, so the record a caller hands the panel stays small. */
	async function save(id: string) {
		try {
			const file = await api.get<components['schemas']['AssetDetail']>(`/assets/${id}`);
			// `saveToDevice`, never `saveAsset`, which copies a still to the clipboard: Save means the
			// disk.
			await saveToDevice(file);
		} catch {
			toasts.show("Sift couldn't save that. The file isn't where it was.", { tone: 'error' });
		}
	}

	/** Whether there is a file before and after this one, asked of the run the popout walks. */
	const around = $derived(
		asset
			? { back: canStepBack(asset.id), forward: canStepForward(asset.id) }
			: { back: false, forward: false }
	);

	/** Next or Back, through the run, and the panel steps to wherever it answers. */
	async function walk(forward: boolean) {
		if (!asset) return;
		const onward = forward ? stepForward : stepBack;
		await step(await onward(asset.id), onward);
	}

	/* The end of a clip moves the run on as in the popout; the Audio player's run holds no photograph. */
	const pictures = $derived(dwell.pictures && !mini.bar);
	const goesOn = $derived(asset !== null && runGoesOn(asset.id, { pictures }));

	const endRule = () => ({ pictures, wraps: !dwell.stopsAtTheEnd });

	async function playedThrough() {
		const from = asset?.id;
		if (!from) return;
		const next = await playOn(from, endRule());
		if (next !== from && asset?.id === from) await step(next, (at) => playOn(at, endRule()));
	}

	/* The next file is found while this one plays, so its end waits only on the media. */
	function started() {
		if (asset && goesOn && run.movesOnAfter(dwell.mode)) lookAhead(asset.id, endRule());
	}

	const recordOf = async (id: string) =>
		takeRecord(id)?.record ??
		(await api.get<components['schemas']['AssetDetail']>(`/assets/${id}`));

	/** Step the panel to a neighbour, from the beginning; on the Audio player, past every picture. */
	async function step(id: string | null, onward: Onward = () => null) {
		if (!id) return;
		try {
			const file = mini.bar ? await firstClip(id, onward, recordOf) : await recordOf(id);
			if (!file) return;
			mini.open(heldOf(file), { width: window.innerWidth, height: window.innerHeight });
		} catch {
			toasts.show("Sift couldn't open that. The file isn't where it was.", { tone: 'error' });
		}
	}

	/* The panel's own keys (`panelKeys`), the last one echoed in the words of `$lib/theater/echoes`. */
	let keyed = $state<Echo>(muteEcho(false));
	let keyedPresses = $state(0);

	function echo(what: Echo): void {
		keyed = what;
		keyedPresses += 1;
	}

	const keys = panelKeys({
		isClip,
		player: () => player,
		assetId: () => asset?.id ?? null,
		docked: () => docked,
		save: (id) => void save(id),
		expand,
		echo,
		flash
	});

	function isClip(): boolean {
		return asset !== null && !concealed && !showsPicture;
	}

	function onKeydown(event: KeyboardEvent) {
		if (!mini.showing || somethingElsePlaying) return;
		if (pressed(event, keys)) return;
		// As on the full-size view; the panel's compact player never steps, so Ctrl is answered here.
		const asked = stepAsked(event, { clip: isClip(), clipSteps: false });
		if (asked !== null) {
			event.preventDefault();
			if (asked === 'next' ? around.forward : around.back) void walk(asked === 'next');
		}
	}

	onMount(() => {
		window.addEventListener('keydown', onKeydown);
		return () => window.removeEventListener('keydown', onKeydown);
	});

	/* The play control over the picture comes with the pointer and goes a second after it leaves. */
	const LINGER_MS = 1000;

	/* Whether the panel's chrome is up: the store's answer (`mini.chromeUp`), so a Theater wall drawn
	   inside reads the same clock. */
	const showControl = $derived(mini.chromeUp);
	let playing = $state(false);

	/*
	 * Docked: at a phone's width the corner player is the Audio player's strip across the screen
	 * above the tabs; a hand-placed panel would sit over the tab bar. A Theater wall is never docked
	 * (no Theater on a phone). Its height is published (`--mini-docked`) for the selection bar.
	 */
	const docked = $derived(phoneWidth.yes && !mini.wall);

	/* Whether the transport has anything in it: the floating bar's whole row always, and play for a
	   clip on the docked strip. An empty one is not drawn, so the strip starts at the picture. */
	const transportHolds = $derived(!docked || (!showsPicture && !concealed));

	/* How tall the docked strip stands: a finger's row of controls and a finger's timeline under it.
	   One number, read by the strip and by the height it publishes. */
	const DOCKED_STRIP = 'calc(2 * var(--touch-target) + var(--space-2))';

	$effect(() => {
		if (!docked || asset === null) return;
		const root = document.documentElement;
		root.style.setProperty('--mini-docked', `calc(${DOCKED_STRIP} + var(--space-2))`);
		return () => root.style.removeProperty('--mini-docked');
	});

	let hideTimer: ReturnType<typeof setTimeout> | null = null;
	/** How long the clip is, so the timeline along the bottom means something. */
	let length = $state(0);
	let player = $state<ReturnType<typeof Player> | null>(null);
	/** How the player is playing this file, handed up so the corner can say why. */
	let plan = $state<PlaybackPlan | null>(null);
	let picture = $state<ReturnType<typeof StillView> | null>(null);

	/* A file gone hidden takes its clock and its play state with it: its player says nothing as it goes. */
	$effect(() => {
		if (!concealed) return;
		playing = false;
		playedTo = 0;
		length = 0;
		plan = null;
	});

	/*
	 * The panel offers itself to the phone while it holds a file: a clip through its player, a
	 * picture through the still view (`offerViewer`). A wall in the panel offers itself.
	 */
	const holdsAFile = $derived(asset !== null && !mini.wall);
	$effect(() => {
		if (!holdsAFile) return;
		return untrack(() => offerViewer(() => player?.remote() ?? picture?.remote() ?? null));
	});

	/*
	 * The panel takes the shape of what is put in it, once per file as it arrives, keeping its area
	 * (`shapedTo`) and settling inside the window, which wins over matching the picture exactly.
	 * Once, because it is a size somebody may then drag away from.
	 */
	let shapedFor: string | null = null;

	function takeTheShapeOf(size: { width: number; height: number }) {
		const held = asset?.id;
		if (!held || mini.wall || size.width <= 0 || size.height <= 0) return;
		/* Told to the STORE, not just used here, because every one of the four things that move the
		   panel has to keep this shape: a drag, a let-go, a window resize, and being reopened. */
		mini.takesTheShape(size.width / size.height);
		if (shapedFor === held) return;
		shapedFor = held;
		const room = within();
		mini.settle(shapedTo(mini.place, size, room), room);
	}

	function flash() {
		if (hideTimer) clearTimeout(hideTimer);
		hideTimer = null;
		mini.chromeUp = true;
	}

	/* A second after the pointer leaves, not the instant it does. Long enough to come back to. */
	function stopFlashing() {
		if (hideTimer) clearTimeout(hideTimer);
		hideTimer = setTimeout(() => (mini.chromeUp = false), LINGER_MS);
	}

	onMount(() => () => {
		if (hideTimer) clearTimeout(hideTimer);
	});

	/*
	 * Docked, it comes and goes as the selection bar does (`arrive`); the floating panel grows out of
	 * the picture it was handed from and fades as it goes (`stageTransition`), and its change to the
	 * Audio player and back is the same movement (`screenChanges`).
	 */
	function docks(node: Element) {
		return docked
			? arrive(node, { y: 16, pace: 'base', spring: true })
			: stageTransition(node, { from: takePlace });
	}

	/* The panel itself, for the full-size view to grow out of on the way back up. */
	let panel = $state<HTMLElement | null>(null);
</script>

{#if (asset || mini.wall) && !openFullSize}
	<!-- A panel, not a dialog: it takes no focus and blocks nothing, and Escape is the screen's. -->
	<!-- svelte-ignore a11y_no_noninteractive_element_interactions: the handlers only WAKE the
	     panel's chrome, and they are on the panel because the header lies over the picture. The focus
	     pair is the keyboard's way to wake it, so Tab never lands on an invisible control. -->
	<section
		class="mini"
		class:bar={mini.bar || docked}
		class:docked
		class:bare={!transportHolds}
		transition:docks
		bind:this={panel}
		use:screenChanges={{ key: mini.bar || docked, fade: true }}
		{@attach mini.bar && !docked ? inThePage : null}
		aria-label={mini.bar ? 'Audio player' : 'Mini player'}
		onpointerenter={flash}
		onpointermove={flash}
		onpointerleave={stopFlashing}
		onfocusin={flash}
		onfocusout={stopFlashing}
		style:--mini-x="{mini.place.x}px"
		style:--mini-y="{mini.place.y}px"
		style:--mini-w="{mini.place.width}px"
		style:--mini-h="{mini.place.height}px"
		style:--docked-strip={DOCKED_STRIP}
	>
		<!-- The handle: a whole edge, since this panel is moved more than anything else in the app. It
		     lies over the picture and comes and goes with the controls, so the panel can be the shape
		     of what is in it, and it keeps the pointer because the panel wakes as the pointer arrives. -->
		<header
			class="grip"
			class:showing={showControl}
			role="toolbar"
			aria-label="Move the mini player"
			tabindex="-1"
			onpointerdown={(event) => begin('move', event)}
			onpointermove={drag}
			onpointerup={end}
			onpointercancel={end}
		>
			<!-- Back to full size, for a wall as for a clip; at the far end from the close button,
			     its opposite. Named by a tooltip, as every glyph-only control is. -->
			<Tooltip label={ACTS.fullSize} shortcut={keyOf('fullSize', 'mini')}>
				<Button
					tone="ghost"
					size="small"
					icon="open_in_full"
					aria-label={ACTS.fullSize}
					onclick={mini.wall ? backToWall : () => expand()}
				/>
			</Tooltip>
			<span class="what">{holding}</span>
			{#if asset !== null && !mini.wall && !concealed && !showsPicture}
				<!-- Smaller again: the sound carries on in a strip along the foot of the window. -->
				<Tooltip label={ACTS.audioPlayer} shortcut={keyOf('audioPlayer', 'mini')}>
					<Button
						tone="ghost"
						size="small"
						icon="cadence"
						aria-label={ACTS.audioPlayer}
						onclick={() => mini.toBar()}
					/>
				</Tooltip>
			{/if}
			<Tooltip label={ACTS.close}>
				<Button
					tone="ghost"
					size="small"
					icon="close"
					aria-label={ACTS.close}
					onclick={() => mini.close()}
				/>
			</Tooltip>
		</header>

		{#if (mini.bar || docked) && asset}
			<MiniBar
				{asset}
				{docked}
				{transportHolds}
				{concealed}
				{showsPicture}
				{holding}
				{playing}
				{player}
				{around}
				onwalk={(forward) => void walk(forward)}
				onexpand={() => expand()}
				{playedTo}
				{length}
			/>
		{/if}

		<!-- A double-click on the picture goes back to full size: at this size "bigger" is all it can
		     mean. `walled` makes resizing the panel resize the wall inside it. -->
		<!-- svelte-ignore a11y_no_static_element_interactions: the panel's buttons carry the keyboard,
		     and `I` does the same from anywhere. -->
		<div
			class="screen"
			class:walled={mini.wall}
			ondblclick={mini.wall ? undefined : () => expand()}
		>
			{#if mini.wall}
				<!-- The WHOLE wall, the same cells that were on the screen. Theater pops out entire:
				     cut down to whichever cell was in front, it would be a different thing from the
				     thing somebody was watching. -->
				{#if theater.wall}
					<TheaterWall wall={theater.wall} />
				{/if}
			{:else if asset && concealed && (mini.bar || docked)}
				<!-- The strip's picture is too small for the sentence; its controls say why. -->
				<Withheld label="Hidden" />
			{:else if asset && concealed}
				<!-- Hidden, said in the picture's own area: the frame, the strip and both arrows stay, so
				     the way on through the list does too. `Empty`'s one-line form fits the smallest
				     panel. -->
				<div class="veiled">
					<Empty scope="block" icon="visibility_off"
						>This one is hidden. It takes the PIN to see.</Empty
					>
				</div>
			{:else if asset && showsPicture}
				<!-- A photograph or a GIF, drawn by the one component that draws them everywhere else.
				     `compact` takes its bar off: the panel's own controls are in the middle of the
				     picture, and a second set along the bottom would be most of what there is here. -->
				<StillView
					bind:this={picture}
					id={asset.id}
					mediaType={asset.mediaType}
					compact
					onpicturesize={takeTheShapeOf}
				/>
			{:else if asset}
				<Player
					bind:this={player}
					id={asset.id}
					poster={asset.poster}
					sprite={asset.sprite}
					art={asset.art}
					compact
					startPaused={asset.paused ?? false}
					onprogress={(seconds) => (playedTo = seconds)}
					onplaystate={(running) => (playing = running)}
					ondurationknown={(seconds) => {
						length = seconds;
						/* The clip's header has been read, which is the same moment its width
						   and height become knowable. So this is the picture's `onpicturesize`,
						   under the name the player already had for it. */
						const size = player?.pictureSize();
						if (size) takeTheShapeOf(size);
					}}
					seekTo={asset.at ? { ms: asset.at * 1000 } : null}
					onplayedthrough={goesOn ? () => void playedThrough() : undefined}
					onstarted={started}
					place={corner}
					onplan={(next) => (plan = next)}
				/>
			{/if}
			<MiniOverlay
				{asset}
				clip={asset !== null && !mini.wall && !concealed && !showsPicture}
				bar={mini.bar || docked}
				showing={showControl}
				{plan}
				{playing}
				{player}
				{playedTo}
				{length}
				{around}
				edges={asset !== null && !mini.wall}
				onwalk={(forward) => void walk(forward)}
			/>
		</div>

		<!-- Every corner and edge resizes; `aria-hidden` because resizing is a pointer gesture and the
		     way to something bigger is the button above. The echo of a key sits over the picture's top
		     corner on the panel and over the small picture on the Audio player. -->
		<div class="key-echoes">
			<KeyEcho
				icon={keyed.icon}
				label={keyed.label}
				detail={keyed.detail}
				muted={keyed.muted}
				press={keyedPresses}
			/>
		</div>
		{#each ['nw', 'ne', 'sw', 'se', 'n', 's', 'e', 'w'] as const as grip (grip)}
			<span
				class="corner {grip}"
				aria-hidden="true"
				onpointerdown={(event) => begin(grip, event)}
				onpointermove={drag}
				onpointerup={end}
				onpointercancel={end}
			></span>
		{/each}
	</section>
{/if}

<style>
	/* Placed by properties set on the element, which compile to `setProperty`: a style attribute in
	   the markup is refused by the policy this app is served under. */
	.mini {
		/* The header strip's height, named because the strip and the marks under it must agree. */
		--grip-band: 28px;
		position: fixed;
		inset-block-start: var(--mini-y);
		inset-inline-start: var(--mini-x);
		inline-size: var(--mini-w);
		block-size: var(--mini-h);
		z-index: var(--z-mini-player);
		/* One row: the header is drawn over the picture, so the panel can be its shape. */
		display: block;
		overflow: hidden;
		/* The panel's corner, published for the progress line, which meets the bottom curves. */
		--frame-corner: var(--radius-md);
		border-radius: var(--frame-corner);
		background: var(--sift-bg);
		box-shadow: var(--elev-3);
		/* No `border`: the hairline is the ring below, painted inside the box, so the progress line
		   can be the panel's bottom edge. */
	}

	/* Clear of the strip across the top, as the notice is; over the picture, under every control. */
	.key-echoes {
		position: absolute;
		inset-block-start: calc(var(--grip-band) + var(--space-2));
		inset-inline-start: var(--space-2);
		z-index: 5;
		pointer-events: none;
	}

	/* On the Audio player the badge stands on the strip's small picture, in its grid area. */
	.mini.bar .key-echoes {
		grid-area: start;
		inset-block-start: 50%;
		inset-inline-start: 0;
		translate: 0 -50%;
	}

	.mini::after {
		content: '';
		position: absolute;
		inset: 0;
		border-radius: inherit;
		border: 1px solid var(--sift-line);
		pointer-events: none;
		/* Over the picture, under the progress line and under every control. */
		z-index: 2;
	}

	.grip {
		position: absolute;
		inset: 0 0 auto 0;
		z-index: 4;
		display: flex;
		align-items: center;
		gap: var(--space-1);
		padding-inline: var(--space-2);
		block-size: var(--grip-band);
		/* Its own ground, since a white glyph over an unknown frame may or may not be legible. */
		background: color-mix(in oklab, var(--sift-bg) 70%, transparent);
		cursor: move;
		/* Or a drag across the panel selects the words in the header instead of moving it. */
		user-select: none;
		touch-action: none;
		opacity: 0;
		transition: opacity var(--dur-base) var(--ease);
	}

	/* Up while the pointer is on the panel, and up while anything in it has the keyboard: a header
	   nobody can see is a close button nobody can Tab to. Both are `showing`; see the panel. */
	.grip.showing {
		opacity: 1;
		transition: opacity var(--dur-fast) var(--ease);
	}

	/* The header's buttons sit above the resize corners, which reach the top two, so a press on a
	   button's edge closes rather than resizes. */
	.grip :global(button) {
		position: relative;
		z-index: 6;
	}

	/*
	 * The header's icons light up rather than growing a box: they sit on a photograph inside a strip
	 * with its own translucent ground, where a second slab would be clutter. Brighter and larger
	 * under the pointer, on a `scale` that moves nothing around it.
	 */
	.grip :global(button:hover:not(:disabled)) {
		background: none;
		color: var(--sift-ink);
		scale: 1.12;
	}

	.grip :global(button) {
		transition:
			color var(--dur-instant) var(--ease),
			scale var(--dur-instant) var(--ease);
	}

	/* The answer without the travel, matching every other reduced-motion rule in the app: the glyph
	   still brightens and is still bigger, it simply arrives. */
	:global(:root[data-motion='reduce']) .grip :global(button) {
		transition: none;
	}

	/* What the panel holds, centred in the space between the buttons so it reads as the panel
	   speaking rather than as a label on one of them. */
	.what {
		flex: 1;
		min-inline-size: 0;
		font: var(--text-label);
		/* Padding rather than a taller line keeps descenders clear of the ellipsis's clip, and the
		   strip, which centres its contents, keeps the words where they are. */
		padding-block: var(--space-1);
		/* The glyphs' own ink: it is the one thing in the strip saying what the panel holds. */
		color: var(--sift-ink-2);
		text-align: center;
		overflow: hidden;
		white-space: nowrap;
		text-overflow: ellipsis;
	}

	/* The player fills what is left. `position: relative` because the player draws its bar and its
	   progress line against this box rather than against the page. */
	.screen {
		position: relative;
		block-size: 100%;
		min-block-size: 0;
		background: var(--sift-bg);
	}

	/* What a hidden file gets in place of a picture: the shared empty state, centred in the box the
	   picture would have filled. The frame around it is untouched. See the markup. */
	.veiled {
		display: grid;
		place-items: center;
		block-size: 100%;
		padding: var(--space-4);
	}

	/* A wall here has to be told how tall it is: `flex: 1` is ignored in a block, and a feed's size
	   is worked out from the wall's height. `min-block-size: 0` lets it shrink too. */
	.screen.walled {
		display: flex;
	}

	/*
	 * Whatever is in the panel is fitted to it, whole: `contain`, as every surface in Sift does, so a
	 * resize shows more of the same thing. With the panel keeping the file's shape there is usually
	 * nothing to letterbox. The canvas is a GIF drawn frame by frame where the browser can decode one.
	 */
	.screen :global(:is(video, img, canvas)) {
		inline-size: 100%;
		block-size: 100%;
		object-fit: contain;
		/* Rounded like the panel, by the picture itself: a composited `<video>` inside a rounded,
		   clipped parent leaves a bright line along each curve in Chromium. The panel's own radius. */
		border-radius: var(--radius-md);
	}

	/* How far through, along the bottom edge: the player's hairline, placed by the frame it is drawn
	   in, as the full-size stage places it. */
	.screen :global(.player-progress) {
		position: absolute;
		/* On the bottom edge; the line reads `--frame-corner` and stops where it meets each curve
		   (its rule in `Player.svelte` says the arithmetic). */
		inset-block-end: 0;
		z-index: 3;
		opacity: 1;
		transition: opacity var(--dur-fast) var(--ease);
	}

	/* Handed over to the real timeline the moment anything is reaching for it, rather than the two
	   sitting two pixels apart. */
	.screen:has(:global(.timeline-slot.showing)) :global(.player-progress) {
		opacity: 0;
	}

	:global(:root[data-motion='reduce']) .grip,
	:global(:root[data-motion='reduce']) .screen :global(.player-progress) {
		transition: none;
	}

	.corner {
		position: absolute;
		inline-size: var(--space-4);
		block-size: var(--space-4);
		touch-action: none;
		/* Above the player's own bar, which reaches the same corners. */
		z-index: 5;
	}

	.nw {
		inset: 0 auto auto 0;
		cursor: nwse-resize;
	}

	.ne {
		inset: 0 0 auto auto;
		cursor: nesw-resize;
	}

	.sw {
		inset: auto auto 0 0;
		cursor: nesw-resize;
	}

	.se {
		inset: auto 0 0 auto;
		cursor: nwse-resize;
	}

	/* The edges between them. Inset by the corners' width at each end so the two never overlap:
	   a corner that lost its outer few pixels to an edge is a corner that resizes one dimension. */
	.n,
	.s {
		inset-inline: 16px;
		inline-size: auto;
		block-size: var(--space-2);
		cursor: ns-resize;
	}

	.n {
		inset-block-start: 0;
	}

	.s {
		inset-block-end: 0;
	}

	.e,
	.w {
		inset-block: 16px;
		block-size: auto;
		inline-size: var(--space-2);
		cursor: ew-resize;
	}

	.w {
		inset-inline-start: 0;
	}

	.e {
		inset-inline-end: 0;
	}

	/* The Audio player, in Theater's bar's shell: the scrub line on top; under the timeline, the
	   picture from its start, the transport, and the ends flush with its end. Tall by its content.
	   Centred in the PAGE (`inThePage`) less a drawer beside it, so it never runs under the rail. */
	.mini.bar {
		--frame-corner: var(--radius-xl);
		--bar-picture: calc(var(--touch-target) * 16 / 9);
		--bar-room: calc(var(--frame-w, 100%) - var(--drawer-beside, 0px));
		inset-block-start: auto;
		inset-block-end: var(--space-4);
		inset-inline-start: calc(var(--frame-x, 0px) + var(--bar-room) / 2);
		translate: -50% 0;
		inline-size: min(800px, calc(var(--bar-room) - var(--space-8)));
		block-size: auto;
		padding: var(--space-2) var(--space-3);
		/* The frame under the pointer stands above the bar, which has nothing else to clip. */
		overflow: visible;
		display: grid;
		grid-template-columns: auto auto auto minmax(0, 1fr) auto;
		grid-template-rows: auto auto;
		grid-template-areas:
			'line line line line line'
			'. start transport ends .';
		align-items: center;
		column-gap: var(--space-3);
		row-gap: var(--space-1);
		border: 1px solid var(--sift-line);
		background: var(--sift-scrim);
		backdrop-filter: blur(var(--blur-glass));
		box-shadow: none;
	}

	/* The border is the edge here, not the panel's inside ring. */
	.mini.bar:not(.docked)::after {
		display: none;
	}

	/*
	 * Above a screen's footer, not across it: the pager lives there, and a bar over it swallows
	 * every press aimed at a page. The same clearance the selection bar takes from the frame.
	 */
	:global(:root:has(.frame > .frame-footer)) .mini.bar {
		inset-block-end: calc(var(--page-footer-height) + var(--space-4));
	}

	/* Docked at a phone's width: the bar the width of the screen less its gutter, on top of the tabs
	   by the one height they are drawn at, above the home indicator and a page's footer. */
	.mini.docked {
		inset-block-end: calc(var(--tab-bar-height) + var(--safe-bottom) + var(--space-2));
		inset-inline: var(--space-2);
		inline-size: auto;
		translate: none;
		/* A finger-high scrub line over the controls; the selection bar reads `DOCKED_STRIP`. */
		block-size: var(--docked-strip);
		grid-template-rows: var(--touch-target) minmax(0, 1fr);
		row-gap: 0;
		padding-block: var(--space-1);
		border: none;
		background: var(--sift-scrim-strong);
		box-shadow: var(--elev-3);
		/* The floating bar's order in a thumb's width. The name gives way, so Play stands after it
		   rather than on the centre line; a picture's strip has no Play (`bare`). */
		grid-template-columns: auto minmax(0, 1fr) auto auto;
		grid-template-areas:
			'line line line line'
			'picture title transport ends';
	}

	:global(:root:has(.frame > .frame-footer)) .mini.docked {
		inset-block-end: calc(
			var(--tab-bar-height) + var(--safe-bottom) + var(--page-footer-height) + var(--space-2)
		);
	}

	.mini.docked.bare {
		grid-template-columns: auto minmax(0, 1fr) auto;
		grid-template-areas:
			'line line line'
			'picture title ends';
	}

	/* The panel's own chrome has no place on the bar: its controls are the bar's, drawn above. */
	.mini.bar .grip,
	.mini.bar .corner,
	.mini.bar .screen :global(.player-progress) {
		display: none;
	}

	/* The picture, kept playing and drawn small (the same element, so nothing reloads), no taller
	   than Play. */
	.mini.bar .screen {
		grid-area: start;
		inline-size: var(--bar-picture);
		block-size: var(--touch-target);
		overflow: hidden;
		border-radius: var(--radius-sm);
	}

	/* Docked, the picture and its badge in their place; `MiniBar` places the strip's own parts. */
	.mini.docked .screen,
	.mini.docked .key-echoes {
		grid-area: picture;
	}
</style>
