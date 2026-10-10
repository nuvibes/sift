<script lang="ts">
	/*
	 * What the player says in place of the picture while it is not playing it: the file cannot be
	 * read, the ask failed, the plan is on its way, Sift has not read the file yet, or Sift
	 * cannot convert it as fast as it plays. Drawn OVER the stage rather than instead of it, so the
	 * element the browser is holding fullscreen is never taken away.
	 */
	import Icon from '$lib/components/Icon.svelte';
	import { Button } from '$lib/components/common';
	import type { PlaybackPlan } from '$lib/player/playback';
	import FileUnreachable from './FileUnreachable.svelte';

	interface Props {
		/** No copy of the file can be read. */
		failed: boolean;
		/** A scan that will find the file if it moved is waiting or running. */
		scanQueued?: boolean;
		/** The ask itself failed, not the file. */
		unasked: boolean;
		plan: PlaybackPlan | null;
		/** Whether somebody chose to try a file that would stall. */
		overridden: boolean;
		/** Ask for the plan again. */
		onretry: () => void;
		/** Try the file anyway. */
		onoverride: () => void;
	}

	let {
		failed,
		scanQueued = false,
		unasked,
		plan,
		overridden,
		onretry,
		onoverride
	}: Props = $props();
</script>

{#if failed}
	<!-- The row is here, the bytes are not: the page a picture draws for the same fact. -->
	<div class="over"><FileUnreachable {scanQueued} /></div>
{:else if unasked}
	<div class="warning over">
		<Icon name="warning" size={20} label="" />
		<p>Couldn't ask how to play this.</p>
		<Button tone="primary" onclick={onretry}>Try again</Button>
	</div>
{:else if !plan}
	<p class="note over">Loading&hellip;</p>
{:else if plan.route === 'unread'}
	<!-- Not an error: the ask put the read at the front of the queue, and this re-plans as it lands. -->
	<div class="warning over">
		<Icon name="schedule" size={20} label="" />
		<p>{plan.reason}</p>
	</div>
{:else if !plan.streamable && !overridden}
	<!-- Said, with the estimate offered to be overruled. -->
	<div class="warning over">
		<Icon name="warning" size={20} label="" />
		<p>{plan.reason}</p>
		<Button tone="primary" onclick={onoverride}>Try anyway</Button>
	</div>
{/if}

<style>
	.over {
		position: absolute;
		inset: 0;
		z-index: 2;
		display: grid;
		align-content: center;
		justify-items: center;
		background: var(--sift-bg);
	}

	.warning {
		display: grid;
		justify-items: center;
		gap: var(--space-3);
		padding: var(--space-8) var(--space-4);
		text-align: center;
		color: var(--sift-ink-2);
	}

	.warning p {
		margin: 0;
		max-width: 46ch;
		font: var(--text-body);
	}

	.note {
		margin: 0;
		padding: var(--space-8);
		text-align: center;
		font: var(--text-body);
		color: var(--sift-ink-3);
	}
</style>
