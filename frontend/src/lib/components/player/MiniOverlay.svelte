<script lang="ts">
	/*
	 * What the corner panel draws over its picture: the mark saying why a clip takes the path it
	 * takes, play in the middle, the timeline along the bottom edge, and the way through the list at
	 * the picture's edges. All of it comes and goes on the panel's wake, kept in the tree and faded,
	 * since on a panel this small a control appearing between frames reads as a flicker.
	 */
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { Button } from '$lib/components/common';
	import { keepFocusOnPress } from '$lib/components/common/menu-touch';
	import { noticeLabel, noticeWords } from '$lib/player/facts';
	import type { PlaybackPlan } from '$lib/player/playback';
	import { mini } from '$lib/player/mini.svelte';
	import { spriteUrl } from '$lib/entity/art';
	import { ACTS, keyOf } from '$lib/player/acts';
	import type Player from './Player.svelte';
	import StageNotice from './StageNotice.svelte';
	import Scrubber from './Scrubber.svelte';

	interface Props {
		asset: NonNullable<typeof mini.asset> | null;
		/** Whether the panel holds a clip, which is what has a transport. */
		clip: boolean;
		/** Whether the panel is the Audio player's strip, which draws none of this. */
		bar: boolean;
		/** Whether the panel's chrome is up. */
		showing: boolean;
		plan: PlaybackPlan | null;
		playing: boolean;
		player: ReturnType<typeof Player> | null;
		playedTo: number;
		length: number;
		/** Whether there is a file before and after, in the run the panel was opened from. */
		around: { back: boolean; forward: boolean };
		/** Whether the way through the list is drawn at all: not for a wall. */
		edges: boolean;
		onwalk: (forward: boolean) => void;
	}

	let {
		asset,
		clip,
		bar,
		showing,
		plan,
		playing,
		player,
		playedTo,
		length,
		around,
		edges,
		onwalk
	}: Props = $props();
</script>

{#if clip && asset}
	<!-- Why the file takes the path it takes, as the file page and a Theater cell mark it. -->
	{#if noticeWords(plan, null)}
		<div class="notice" class:bar class:showing>
			<StageNotice label={noticeLabel(plan, null)}>{noticeWords(plan, null)}</StageNotice>
		</div>
	{/if}

	<!-- One control in the middle rather than a bar across the bottom, which at this size is most of
	     the picture. -->
	<div class="tapped" class:bar class:showing aria-hidden={!showing}>
		<Tooltip
			label={playing ? ACTS.pause : ACTS.play}
			shortcut={keyOf(playing ? 'pause' : 'play', 'mini')}
		>
			<Button
				tone="ghost"
				class="tap"
				icon={playing ? 'pause' : 'play_arrow'}
				aria-label={playing ? ACTS.pause : ACTS.play}
				tabindex={showing ? 0 : -1}
				onpointerdown={keepFocusOnPress}
				onclick={() => player?.togglePlayback()}
				ondblclick={(event) => event.stopPropagation()}
			/>
		</Tooltip>
	</div>

	<!-- At rest the player's hairline along the bottom edge (`player-progress`), under the pointer the
	     real timeline, the two crossing over. The strip draws its own: one timeline at a time. -->
	{#if !bar}
		<div class="timeline-slot" class:showing aria-hidden={!showing}>
			<Scrubber
				position={playedTo}
				duration={length}
				sheet={asset.sprite}
				sheetUrl={spriteUrl({ id: asset.id, art: asset.art })}
				onseek={(seconds) => player?.goTo(seconds)}
			/>
		</div>
	{/if}
{/if}
<!-- The way through the list at the picture's edges, only where the run has somewhere to step. -->
{#if edges && (around.back || around.forward)}
	<div class="tapped" class:bar class:showing aria-hidden={!showing}>
		<!-- The place is on a span of this file's own, so the tooltip's wrapper is not what the edge is
		     measured from. -->
		{#if around.back}
			<span class="edge back">
				<Tooltip label={ACTS.previous} shortcut={keyOf('previous', 'mini')}>
					<Button
						tone="ghost"
						class="tap small-tap"
						icon="skip_previous"
						aria-label={ACTS.previous}
						tabindex={showing ? 0 : -1}
						onpointerdown={keepFocusOnPress}
						onclick={() => onwalk(false)}
						ondblclick={(event) => event.stopPropagation()}
					/>
				</Tooltip>
			</span>
		{/if}
		{#if around.forward}
			<span class="edge forward">
				<Tooltip label={ACTS.next} shortcut={keyOf('next', 'mini')}>
					<Button
						tone="ghost"
						class="tap small-tap"
						icon="skip_next"
						aria-label={ACTS.next}
						tabindex={showing ? 0 : -1}
						onpointerdown={keepFocusOnPress}
						onclick={() => onwalk(true)}
						ondblclick={(event) => event.stopPropagation()}
					/>
				</Tooltip>
			</span>
		{/if}
	</div>
{/if}

<style>
	/* `--frame-corner`, `--grip-band`, `--bar-height` and `--bar-band` are MiniPlayer's, set on the
	   frame around this; each fallback is its default there. */
	/* In the picture's top corner, below the header strip, cleared by the strip's measured height. */
	.notice {
		position: absolute;
		inset-block-start: calc(var(--grip-band, 28px) + var(--space-2));
		inset-inline-end: var(--space-2);
		z-index: 5;
		opacity: 0;
		pointer-events: none;
		transition: opacity var(--dur-fast) var(--ease);
	}

	.notice.showing {
		opacity: 1;
		pointer-events: auto;
	}

	/* The whole picture, so the jumps sit against its edges; only the buttons take the pointer, and
	   `visibility` goes with the opacity so nothing invisible can be pressed. */
	.tapped {
		position: absolute;
		inset: 0;
		z-index: 4;
		display: flex;
		align-items: center;
		justify-content: center;
		pointer-events: none;
		opacity: 0;
		visibility: hidden;
		/* Hidden once the fade has ended, shown at once on the way in. */
		transition:
			opacity var(--dur-base) var(--ease),
			visibility var(--dur-base) linear;
	}

	.tapped.showing {
		opacity: 1;
		visibility: visible;
		transition: opacity var(--dur-fast) var(--ease);
	}

	/* A disc with its own translucent ground, because over video there is no container to blur.
	   `:global` because the classes are handed to `Button`. */
	.tapped :global(.tap) {
		pointer-events: auto;
		inline-size: var(--space-12);
		block-size: var(--space-12);
		border-radius: 50%;
		background: color-mix(in oklab, var(--sift-bg) 70%, transparent);
		color: var(--sift-ink);
		transition: background var(--dur-instant) var(--ease);
	}

	.tapped :global(.tap:hover) {
		background: color-mix(in oklab, var(--sift-bg) 85%, transparent);
	}

	/* Half the size of the one in the middle, which is the one a hand goes to. */
	.tapped :global(.small-tap) {
		inline-size: var(--space-6);
		block-size: var(--space-6);
	}

	.tapped :global(.edge) {
		position: absolute;
		inset-block-start: 50%;
		translate: 0 -50%;
	}

	.tapped :global(.edge.back) {
		inset-inline-start: var(--space-2);
	}

	.tapped :global(.edge.forward) {
		inset-inline-end: var(--space-2);
	}

	/* In a box of the frame's own: how a timeline looks is the component's, where it sits is the
	   frame's. Clear of the bottom curves, because it has a thumb the hairline does not. */
	.timeline-slot {
		position: absolute;
		inset-block-end: 2px;
		inset-inline: var(--frame-corner, var(--radius-md));
		z-index: 4;
		opacity: 0;
		visibility: hidden;
		transition:
			opacity var(--dur-base) var(--ease),
			visibility var(--dur-base) linear;
	}

	.timeline-slot.showing {
		opacity: 1;
		visibility: visible;
		transition: opacity var(--dur-fast) var(--ease);
	}

	:global(:root[data-motion='reduce']) .tapped,
	:global(:root[data-motion='reduce']) .timeline-slot {
		transition: none;
	}

	/* The Audio player's strip carries its own controls. */
	.tapped.bar,
	.notice.bar {
		display: none;
	}
</style>
