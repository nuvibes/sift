<script lang="ts">
	/*
	 * The files in the library that share a song with this one.
	 *
	 * The same strip as the lookalikes above it, and deliberately so: it draws the same `Tile` at
	 * the same height, fetches a hovered clip through the same small loader, scrolls sideways in the
	 * same `Scroller` with the same arrows, and moves within the sheet the same way when a tile is
	 * pressed. Every reason written in `LooksLikeThis` for each of those holds here word for word, so
	 * it is not written twice; what is written here is what is different.
	 *
	 * ## What is different
	 *
	 * **The answer is a group, not a ranking.** The server pairs two files when their music
	 * fingerprints line up (a bit error rate under a quarter over one 30-second window), and a file
	 * shares a song with the files it is paired with and the files THOSE are paired with: one hop.
	 * It comes back closest first, so the order means something, but nothing in it is a guess the
	 * way "Similar to this" is: a file is either in the group or not.
	 *
	 * **The heading carries the count, and it is a way onwards.** "Same music" with "11 files" in
	 * its tail, and pressing it opens the Files wall filtered to exactly this group
	 * (`same_music:<id>`), with the chip on the bar reading this file's name rather than its ID. A
	 * strip shows a row; the wall shows all of them with every other filter still to hand.
	 *
	 * **Absent when there is nothing to draw**, which is every file with no fingerprint and every
	 * file whose song nobody else in the library shares. There is no "cannot compare" sentence as
	 * the lookalikes have: a file whose sound could not be read simply has no fingerprint, and
	 * Activity is where a read that failed is said.
	 *
	 * Nothing here decides who may see what. The server answers only with files this account may
	 * see and counts only those (a number that included the rest would be a way of learning they
	 * exist) and this draws what it is given.
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
		/**
		 * What this file is called, for the chip the heading's link puts on the Files wall.
		 *
		 * The link carries the file's ID, because that is what the filter takes; the chip is drawn
		 * from the address alone, so without the name it would read as a ULID. Null leaves the chip
		 * reading the ID, which is honest and still removable.
		 */
		name?: string | null;
	}

	let { id, name = null }: Props = $props();

	/**
	 * How TALL one tile in the strip is: the lookalikes' height, so the two strips one under the
	 * other are one row size. The same number as `--strip-tall` in `app.css`; the test beside this
	 * file holds the two equal, as the lookalikes' does.
	 */
	const TALL = 96;

	let group = $state<SameMusic | null>(null);

	const strip = new SideScroll();

	$effect(() => strip.watch());

	const previews = new HoverPreviews();
	onDestroy(() => previews.dispose());

	$effect(() => {
		// A different file is a different group, so the last file's goes first: otherwise its
		// strip sits under the new file until the answer arrives.
		const asked = id;
		group = null;
		void (async () => {
			try {
				const found = await sameMusicOf(asked);
				if (asked === id) group = found;
			} catch {
				// A feature nobody switched on answering nothing is not something to tell anybody
				// about. The strip simply does not appear.
				if (asked === id) group = null;
			}
		})();
	});

	/* A pairing pass rings this as it pairs files: the group is read again in place, never
	   blanked first, and a failed read leaves the strip that is up. */
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

	/* The server's count, which is how many are in `files`, and never a larger number: a file this
	   account may not see is not counted either. */
	const count = $derived(group?.count ?? files.length);
	const tail = $derived(count === 1 ? '1 file' : `${count.toLocaleString()} files`);

	/* Name the chip before the wall draws it. The chip reads the value's name out of the one memory
	   every id-valued chip reads (`facetValueLabel`); a link followed with nothing remembered draws
	   the ID, which is what arriving on a pasted address does too. */
	function toWall() {
		if (name) rememberFacetNames(SAME_MUSIC_FIELD, [{ value: id, label: name, count }]);
	}
</script>

{#snippet arrow(way: -1 | 1, icon: 'chevron_left' | 'chevron_right', words: string)}
	<!-- Named buttons rather than the Scroller's hidden arrows, for the reason the lookalikes and
	     the faces strip give: a strip has no arrow-key walk, so a named button is reachable by
	     everybody. They exist only while there is strip that way. -->
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
		<!-- The heading IS the way to the whole group: an ordinary link, because it leaves the sheet
		     for a wall of files: the one move here that should. The count sits in its tail, in
		     the quieter ink, so the words read first. -->
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
								<!-- Moved within the sheet and given a place in the run just after this file,
							     through `showStranger`, the lookalikes' reasoning, unchanged: a file in
							     this group is not in the list the sheet was opened over. -->
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
	/* The lookalikes' shape exactly (a full-width row one tile tall) because the two strips sit
	   one under the other and must read as a pair. */
	.same-music {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	/* The count, quieter than the words, after a gap rather than a separator character. */

	.strip {
		display: flex;
		/* Stretched, so each arrow stands the strip's whole height (`tall` on the button). */
		align-items: stretch;
		gap: var(--space-1);
		min-inline-size: 0;
	}

	.strip :global(.scroll-root) {
		min-inline-size: 0;
		flex: 1 1 auto;
	}

	/* One row, and padded so a tile's hover ring is not shaved by the scrolling box. See the
	   lookalikes' note on why a negative margin cannot pay for it. */
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
