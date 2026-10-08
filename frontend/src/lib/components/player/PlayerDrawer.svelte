<script lang="ts">
	/*
	 * The player's drawer: everything about the clip rather than the playhead, the things somebody
	 * sets once and then watches. One shape: every control is drawn whether or not it can act, and
	 * one that cannot is dimmed with the reason on it. The three that open a menu stand in the top
	 * row, so each opens above the drawer; then Randomize, what happens at the end and Shuffle, the
	 * three about what comes next; then the loop and the facts. Three rows of three.
	 */
	import { Button, Popover } from '$lib/components/common';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { ACTS } from '$lib/player/acts';
	import type { PlaybackPlan, Quality } from '$lib/player/playback';
	import ClipButton from './ClipButton.svelte';
	import RunControls from './RunControls.svelte';
	import ScreenshotButton from './ScreenshotButton.svelte';
	import type { getStage } from './stage.svelte';
	import type { RepeatControl, ShuffleControl } from './Transport.svelte';

	interface Props {
		/** The file being watched. */
		watching: string;
		/** The file the player was opened on, which the screenshot names. */
		id: string;
		video: HTMLVideoElement | null;
		frame: ReturnType<typeof getStage>;
		plan: PlaybackPlan | null;
		/** The size chosen, or null for the server's own. */
		chosen: Quality | null;
		choosable: boolean;
		canOpen: boolean;
		abLabel: string;
		looping: boolean;
		savable: boolean;
		/** Whether the marked stretch is being saved now. */
		saving: boolean;
		keepOpen: boolean;
		shotOpen: boolean;
		qualityOpen: boolean;
		statsOpen: boolean;
		onquality: (quality: Quality) => void;
		onrandom: () => void;
		/** What happens at the end, and the order: the run's, drawn here beside Randomize. */
		repeat: RepeatControl;
		shuffle: ShuffleControl;
		onmark: () => void;
		onsave: () => void;
		/** Keep the bar up while the pointer is on a portalled menu; let it go again. */
		onhold: (event?: PointerEvent) => void;
		onrelease: () => void;
	}

	let {
		watching,
		id,
		video,
		frame,
		plan,
		chosen,
		choosable,
		canOpen,
		abLabel,
		looping,
		savable,
		saving,
		keepOpen = $bindable(),
		shotOpen = $bindable(),
		qualityOpen = $bindable(),
		statsOpen = $bindable(),
		onquality,
		onrandom,
		repeat,
		shuffle,
		onmark,
		onsave,
		onhold,
		onrelease
	}: Props = $props();

	/* A control that cannot act says why on its label, in a Theater cell's words. */
	const qualityLabel = $derived(choosable ? ACTS.quality : 'This file has one size');
	const randomLabel = $derived(canOpen ? ACTS.randomize : 'Nothing here can open a random file');
	const saveLabel = $derived(
		savable ? ACTS.saveLoop : saving ? 'Saving it as a Loop' : 'Mark both ends of a loop to save it'
	);
</script>

<!-- The last few seconds as a new file, cut beside this one. -->
<ClipButton
	of={watching}
	playhead={() => video?.currentTime ?? 0}
	bind:open={keepOpen}
	portalTo={frame?.isFullscreen ? frame.element : null}
/>

<!-- The frame on screen, or in the desktop app also the player as drawn and the whole window. -->
<ScreenshotButton
	{video}
	of={id}
	bind:open={shotOpen}
	stage={frame?.element ?? null}
	portalTo={frame?.isFullscreen ? frame.element : null}
/>

<!-- The size it is watched at, beside the button and painted inside a filled screen. `qualities?.`:
     a plan that arrives without the field must not take the bar down. -->
<Popover
	bind:open={qualityOpen}
	label={ACTS.quality}
	side="top"
	align="center"
	width="auto"
	inset="sm"
	portalTo={frame?.isFullscreen ? frame.element : null}
>
	{#snippet trigger({ props })}
		<Tooltip label={qualityLabel} placement="top">
			<Button
				{...props}
				tone="ghost"
				icon="video_settings"
				aria-label={qualityLabel}
				disabled={!choosable}
				pressed={choosable && qualityOpen}
			/>
		</Tooltip>
	{/snippet}
	<!-- The menu is portalled, so walking the pointer into it leaves the stage: it holds the bar up
	     itself, or the bar, the drawer and this menu would fade from under the hand. -->
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div class="qualities" onpointerenter={onhold} onpointermove={onhold} onpointerleave={onrelease}>
		{#each plan?.qualities ?? [] as quality (quality.url)}
			<Button
				tone="ghost"
				pressed={(chosen?.url ?? plan?.url) === quality.url}
				onclick={() => onquality(quality)}
			>
				<!-- A rung's name is its size; the file's own entry says what it comes to as well. -->
				{quality.label}{quality.detail ? ` (${quality.detail})` : ''}
			</Button>
		{/each}
	</div>
</Popover>

<!-- Somewhere else, out of the whole library; dimmed where nothing here can open what it finds. -->
<Tooltip label={randomLabel} placement="top">
	<Button
		tone="ghost"
		icon="casino"
		aria-label={randomLabel}
		disabled={!canOpen}
		onclick={onrandom}
	/>
</Tooltip>

<RunControls {repeat} {shuffle} keyboard="player" />

<!-- Two presses set the ends of a loop, a third clears it; the markers on the timeline drag. -->
<Tooltip label={abLabel} placement="top">
	<Button
		tone="ghost"
		icon="all_inclusive"
		aria-label={abLabel}
		pressed={looping}
		onclick={onmark}
	/>
</Tooltip>

<!-- Keep the marked stretch as a Loop, dimmed until both ends are marked. -->
<Tooltip label={saveLabel} placement="top">
	<Button
		tone="ghost"
		icon="bookmark_add"
		aria-label={saveLabel}
		disabled={!savable}
		onclick={onsave}
	/>
</Tooltip>

<!-- The facts, a panel somebody opens, never on screen by default. -->
<Tooltip label={ACTS.stats} placement="top">
	<Button
		tone="ghost"
		icon="cognition_2"
		aria-label={ACTS.stats}
		pressed={statsOpen}
		onclick={() => (statsOpen = !statsOpen)}
	/>
</Tooltip>

<style>
	/* The sizes, a column, each filling it so the whole row is the target. */
	.qualities {
		display: grid;
		gap: var(--space-1);
		justify-items: stretch;
	}
</style>
