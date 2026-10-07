<script lang="ts">
	/*
	 * The Audio player's strip, in every player bar's shape: the scrub line on top; under it the
	 * transport at the start, the panel's own small picture centred, the sound and the ways out at
	 * the end. Docked on a phone: the picture, the name, Play, back to full size and close.
	 */
	import { untrack } from 'svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Separator from '$lib/components/common/Separator.svelte';
	import { Button, Slider } from '$lib/components/common';
	import { loudness } from '$lib/player/loudness.svelte';
	import { run } from '$lib/player/run.svelte';
	import { toggleShuffle } from '$lib/player/asset-view';
	import { nextLoopMode } from '$lib/player/loop-modes';
	import { dwell } from '$lib/player/dwell.svelte';
	import { mini } from '$lib/player/mini.svelte';
	import { spriteUrl } from '$lib/entity/art';
	import { api } from '$lib/api/client';
	import { libraryChanges, whenChanged } from '$lib/library/changes.svelte';
	import type { components } from '$lib/api/schema';
	import { ACTS, keyOf } from '$lib/player/acts';
	import type Player from './Player.svelte';
	import ScrubLine from './ScrubLine.svelte';
	import Transport, { NOTHING_AFTER, NOTHING_BEFORE } from './Transport.svelte';

	interface Props {
		asset: NonNullable<typeof mini.asset>;
		docked: boolean;
		/** Whether the transport has anything in it; an empty one is not drawn. */
		transportHolds: boolean;
		concealed: boolean;
		showsPicture: boolean;
		/** The word the panel's strip uses for what it holds, said for a Hidden file. */
		holding: string;
		playing: boolean;
		player: ReturnType<typeof Player> | null;
		around: { back: boolean; forward: boolean };
		onwalk: (forward: boolean) => void;
		onexpand: () => void;
		playedTo: number;
		length: number;
	}

	let {
		asset,
		docked,
		transportHolds,
		concealed,
		showsPicture,
		holding,
		playing,
		player,
		around,
		onwalk,
		onexpand,
		playedTo,
		length
	}: Props = $props();

	const HIDDEN_REASON = 'This one is hidden';

	/* Where the clip is, on the scrub line: a clip whose length is known. */
	const timed = $derived(!concealed && !showsPicture && length > 0);

	/* The docked strip's name, asked for by id; never for a Hidden file, which is named as hidden. */
	let named = $state('');
	$effect(() => {
		const id = concealed || !docked ? null : asset.id;
		untrack(() => {
			named = '';
			if (id === null) return;
			void api
				.get<components['schemas']['AssetDetail']>(`/assets/${id}`)
				.then((file) => {
					if (mini.asset?.id === id) named = file.filename ?? '';
				})
				.catch(() => {});
		});
	});

	/* A change elsewhere: the docked name is asked again. A file gone leaves through the panel. */
	whenChanged(libraryChanges, () => {
		const id = asset.id;
		if (concealed || !docked) return;
		void api
			.get<components['schemas']['AssetDetail']>(`/assets/${id}`)
			.then((file) => {
				if (mini.asset?.id === id) named = file.filename ?? '';
			})
			.catch(() => {});
	});
</script>

<!-- The scrub line along the top, as every player bar draws it: the time so far at its start, the
     length at its end. Dimmed, never removed, where there is no playhead. First in the markup, so
     Tab reaches it before the presses, as it does on the other bars. -->
<div class="bar-timeline">
	<ScrubLine
		position={playedTo}
		duration={length}
		sheet={asset.sprite}
		sheetUrl={spriteUrl({ id: asset.id, art: asset.art })}
		disabled={concealed || showsPicture}
		onseek={(seconds) => player?.goTo(seconds)}
		{timed}
		keepHeight
	/>
</div>
{#if docked}
	<span class="named">
		<span class="title">{concealed ? holding : named}</span>
	</span>
{/if}
{#if transportHolds}
	<!-- The transport every player bar draws; Play alone on the docked strip. -->
	<div class="transport" class:docked>
		<Transport
			{playing}
			onplay={() => player?.togglePlayback()}
			playable={!concealed}
			playWhy={concealed ? HIDDEN_REASON : undefined}
			onback={around.back ? () => onwalk(false) : undefined}
			onforward={around.forward ? () => onwalk(true) : undefined}
			backWhy={NOTHING_BEFORE}
			forwardWhy={NOTHING_AFTER}
			shuffle={{
				on: run.shuffle,
				onpress: () => toggleShuffle(asset.id),
				why: concealed ? HIDDEN_REASON : undefined
			}}
			repeat={{
				mode: dwell.mode,
				onpress: () => void dwell.choose(nextLoopMode(dwell.mode)),
				why: concealed ? HIDDEN_REASON : undefined
			}}
			playOnly={docked}
			keyboard="mini"
			placement="top"
		/>
	</div>
{/if}
<div class="ends" class:docked>
	{#if !docked}
		<!-- How loud, as the player's own bar says it; dimmed on a Hidden clip, so the strip keeps
		     its shape. -->
		{@const muted = player?.isMuted() ?? false}
		{@const muteWords = concealed ? HIDDEN_REASON : muted ? ACTS.unmute : ACTS.mute}
		<div class="volume" role="group" aria-label="Volume">
			<Tooltip
				label={muteWords}
				shortcut={player ? keyOf(muted ? 'unmute' : 'mute', 'mini') : undefined}
			>
				<Button
					tone="ghost"
					icon={muted || loudness.level === 0 ? 'volume_off' : 'volume_up'}
					aria-label={muteWords}
					disabled={!player}
					onclick={() => player?.toggleSound()}
				/>
			</Tooltip>
			<Slider
				class="bar-level"
				label="Volume"
				valueText="{loudness.level}%"
				value={loudness.level}
				disabled={!player}
				oninput={(level: number) => loudness.set(level)}
			/>
		</div>
		<Separator vertical />
	{/if}
	{#if !docked}
		<Tooltip label={ACTS.miniPlayer} shortcut={keyOf('miniPlayer', 'mini')}>
			<Button
				tone="ghost"
				icon="picture_in_picture"
				aria-label={ACTS.miniPlayer}
				onclick={() => mini.toPanel()}
			/>
		</Tooltip>
	{/if}
	<!-- I takes the docked strip to full size and the floating strip to the mini player, so the key
	     is shown only where it does what the button does. -->
	<Tooltip label={ACTS.fullSize} shortcut={docked ? keyOf('fullSize', 'mini') : undefined}>
		<Button tone="ghost" icon="open_in_full" aria-label={ACTS.fullSize} onclick={onexpand} />
	</Tooltip>
	<Tooltip label={ACTS.close}>
		<Button tone="ghost" icon="close" aria-label={ACTS.close} onclick={() => mini.close()} />
	</Tooltip>
</div>

<style>
	/* Each part stands in a named area of MiniPlayer's grid ('line' on top; 'transport', 'start' and
	   'ends' under it; docked, 'picture' and 'title' at the start). */
	.volume {
		display: flex;
		align-items: center;
		gap: var(--space-1);
	}

	.volume :global(.bar-level) {
		inline-size: var(--space-16);
	}

	.transport.docked :global(button),
	.ends.docked :global(button) {
		inline-size: var(--touch-target);
		block-size: var(--touch-target);
	}

	.transport {
		grid-area: transport;
		display: flex;
	}

	.ends {
		grid-area: ends;
		justify-self: end;
		display: flex;
		align-items: center;
		gap: var(--space-1);
	}

	/* The docked strip's name, after the picture: the one part that gives way. */
	.named {
		grid-area: title;
		display: flex;
		flex-direction: column;
		min-inline-size: 0;
	}

	.title {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font: var(--text-body-sm);
		color: var(--sift-ink);
	}

	/* The scrub line, the strip's whole width inside its padding. */
	.bar-timeline {
		grid-area: line;
		align-self: center;
		min-inline-size: 0;
	}

	/* The Audio player: the scrub line shares the bar's columns (a clock, the picture, the
	   transport, the ends, a clock), so the row under it starts and ends where the timeline does. */
	:global(.mini.bar:not(.docked)) > .bar-timeline {
		display: grid;
		grid-template-columns: subgrid;
	}

	:global(.mini.bar:not(.docked)) > .bar-timeline > :global(.scrub-line) {
		grid-column: 1 / -1;
	}

	:global(.mini.bar:not(.docked)) > .bar-timeline > :global(.scrub-line.clocked) {
		grid-template-columns: subgrid;
	}

	:global(.mini.bar:not(.docked)) > .bar-timeline > :global(.scrub-line.clocked > .timeline) {
		grid-column: 2 / 5;
	}

	:global(.mini.bar:not(.docked)) > .bar-timeline > :global(.scrub-line.clocked > .time.end) {
		grid-column: 5;
	}

	/* No clock: the timeline starts where the picture does, one gap in. */
	:global(.mini.bar:not(.docked)) > .bar-timeline > :global(.scrub-line:not(.clocked)) {
		padding-inline: var(--space-3);
	}
</style>
