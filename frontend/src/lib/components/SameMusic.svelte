<script lang="ts">
	/*
	 * The files sharing a song with this one: the lookalikes' strip, and its reasons, exactly. A
	 * group by fingerprint, not a ranking; the heading counts it and opens the Files wall at
	 * `same_music:<id>`. Absent when empty; the server counts only what this account may see.
	 */
	import Button from '$lib/components/common/Button.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Scroller from '$lib/components/common/Scroller.svelte';
	import Fold from '$lib/components/common/Fold.svelte';
	import { SideScroll } from '$lib/components/common/side-scroll.svelte';
	import Tile from '$lib/components/Tile.svelte';
	import { rememberFacetNames } from '$lib/components/shell/facet-labels';
	import { showStranger } from '$lib/player/asset-view';
	import { thumbUrl } from '$lib/entity/art';
	import { canPreview, HoverPreviews } from '$lib/grid/hover-preview.svelte';
	import { aspectOf } from '$lib/grid/justify';
	import {
		SAME_MUSIC_FIELD,
		sameMusicOf,
		sameMusicWall,
		type SameMusic,
		type SameMusicFile
	} from '$lib/player/music';
	import { sameMusicChanges, whenChanged } from '$lib/library/changes.svelte';
	import { onDestroy } from 'svelte';

	interface Props {
		id: string;
		/** For the chip on the Files wall, which would otherwise read the ID. */
		name?: string | null;
	}

	let { id, name = null }: Props = $props();

	/** `--strip-tall`, held equal by the test beside this, as the lookalikes' is. */
	const TALL = 96;

	let group = $state<SameMusic | null>(null);

	const strip = new SideScroll();

	$effect(() => strip.watch());

	const previews = new HoverPreviews();
	onDestroy(() => previews.dispose());

	$effect(() => {
		// The last file's group goes first.
		const asked = id;
		group = null;
		void (async () => {
			try {
				const found = await sameMusicOf(asked);
				if (asked === id) group = found;
			} catch {
				// A feature nobody switched on: no strip.
				if (asked === id) group = null;
			}
		})();
	});

	/* Read again in place on a pairing pass, never blanked. */
	whenChanged(sameMusicChanges, () => {
		const asked = id;
		sameMusicOf(asked).then(
			(found) => {
				if (asked === id) group = found;
			},
			() => {}
		);
	});

	const files = $derived<SameMusicFile[]>(group?.files ?? []);

	const count = $derived(group?.count ?? files.length);
	const tail = $derived(count === 1 ? '1 file' : `${count.toLocaleString()} files`);

	/* Name the chip first (`facetValueLabel`). */
	function toWall() {
		if (name) rememberFacetNames(SAME_MUSIC_FIELD, [{ value: id, label: name, count }]);
	}
</script>

{#snippet arrow(way: -1 | 1, icon: 'chevron_left' | 'chevron_right', words: string)}
	<!-- Named buttons, as on the lookalikes. -->
	<Tooltip label={words}>
		<Button
			tone="ghost"
			size="small"
			{icon}
			iconSize={16}
			tall
			aria-label={words}
			onclick={() => strip.nudge(way)}
			onpointerenter={() => strip.nudge(way)}
			onpointerleave={strip.stop}
			onpointerdown={() => strip.nudge(way)}
			onpointerup={strip.stop}
		/>
	</Tooltip>
{/snippet}

{#if files.length > 0}
	<section class="same-music">
		<!-- The heading leaves for the wall of the whole group, its count in the tail. -->
		<Fold
			section
			weight={450}
			summary="Same music"
			{tail}
			href={sameMusicWall(id)}
			onclick={toWall}
			remember="sift.file.fold.music"
		>
			<div class="strip">
				{#if strip.canBack}{@render arrow(-1, 'chevron_left', 'Earlier files')}{/if}
				<Scroller horizontal onviewport={strip.take}>
					<ul>
						{#each files as item (item.id)}
							<li>
								<!-- `showStranger`, as the lookalikes. -->
								<Tile
									{item}
									width={Math.round(TALL * aspectOf(item))}
									height={TALL}
									thumbSrc={thumbUrl(item)}
									previewSrc={previews.srcFor(item.id)}
									playing={previews.playing(item.id)}
									onopen={(opened) =>
										showStranger(
											opened,
											id,
											item.media_type === 'video' || item.media_type === 'gif'
										)}
									onhover={(_id, hovering) => previews.enter(item, hovering, canPreview(item))}
								/>
							</li>
						{/each}
					</ul>
				</Scroller>
				{#if strip.canOn}{@render arrow(1, 'chevron_right', 'Later files')}{/if}
			</div>
		</Fold>
	</section>
{/if}

<style>
	.same-music {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	.strip {
		display: flex;
		align-items: stretch;
		gap: var(--space-1);
		min-inline-size: 0;
	}

	.strip :global(.scroll-root) {
		min-inline-size: 0;
		flex: 1 1 auto;
	}

	/* Padded so a tile's hover ring is not shaved. */
	ul {
		display: flex;
		flex-wrap: nowrap;
		gap: var(--space-2);
		list-style: none;
		margin: 0;
		padding: var(--space-1);
	}

	li {
		flex: none;
	}
</style>
