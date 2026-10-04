<script lang="ts">
	/*
	 * The settings sub-page: what a `PresetGroup`'s Edit opens.
	 *
	 * Its own component rather than five lines inside `SettingsPane`, and the reason is testability
	 * rather than tidiness. The page draws a snippet that belongs to a component deep inside a pane,
	 * so a unit test that mounts one pane on its own has no way to see what Edit opened: the state
	 * is set and nothing renders it. With the page as a component, a test mounts this beside the
	 * pane and drives exactly what a person drives.
	 *
	 * It draws nothing at all when no group is open, so mounting it costs an empty comment node.
	 */
	import { BackButton } from '$lib/components/common';
	import { drilldown } from './drilldown.svelte';
	import SettingsTitle from './SettingsTitle.svelte';
	import { pagePlace } from './settings-path';

	interface Props {
		/** What is behind: the section's own name, never the word "Back". See `BackButton`. */
		behind: string;
	}

	let { behind }: Props = $props();

	let top = $state<HTMLElement | null>(null);

	/* The page's title and the press that opened it are crumbs of every settings path on it. */
	pagePlace(() => [drilldown.title ?? undefined, drilldown.door ?? undefined]);

	/* A page that opens starts at ITS top, not at the pane's scroll offset.
	 *
	 * The pane behind is hidden rather than unmounted (see `drilldown.svelte.ts`), so the
	 * SCROLLER keeps whatever offset it had, and a page opened from a row two-thirds of the way
	 * down would open two-thirds of the way down: no title, no way back, just rows.
	 *
	 * `scrollIntoView` on this component's own first element rather than reaching for the
	 * scroller, because nothing here knows which of its ancestors scrolls: Settings is a page
	 * in one place and a panel over a screen in another.
	 */
	$effect(() => {
		if (drilldown.title === null) return;
		/* Guarded, because jsdom has no `scrollIntoView` at all: it is not a stub that does
		   nothing, it is absent, so calling it throws inside the effect and takes the component
		   down. Every settings test that mounts this page hit it. */
		if (typeof top?.scrollIntoView === 'function') {
			top.scrollIntoView({ block: 'start', behavior: 'auto' });
		}
	});
</script>

<!-- The title is the frame's one title, without an icon: see `SettingsTitle`. The page is its own
     `section-stack`, so its first group opens without a rule whatever the pane behind it holds.
     DRESSED BY: .section-stack (SectionHeading reads it; it draws nothing itself) -->
{#if drilldown.title !== null && drilldown.body}
	<div class="sub-page section-stack" bind:this={top}>
		<BackButton label={behind} onback={() => drilldown.close()} />
		<div class="title-slot">
			<SettingsTitle label={drilldown.title} />
		</div>
		{@render drilldown.body()}
	</div>
{/if}

<style>
	/* The page is a block like the pane it stands in for; it adds no box of its own. */
	.sub-page {
		display: block;
	}

	/* The back link sits directly above the title, so the title takes a little air from it. */
	.title-slot {
		margin-block-start: var(--space-3);
	}
</style>
