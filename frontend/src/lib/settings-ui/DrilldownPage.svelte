<script lang="ts">
	/* The settings sub-page: what a `PresetGroup`'s Edit opens. */
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

	/* A page that opens starts at ITS top, not at the pane's scroll offset. */
	$effect(() => {
		if (drilldown.title === null) return;
		/* Guarded, because jsdom has no `scrollIntoView` at all: it is not a stub that does
		   nothing, it is absent, so calling it throws inside the effect and takes the component
		   down. */
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
