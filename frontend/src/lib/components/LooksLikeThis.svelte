<script lang="ts">
	/*
	 * What else in the library is similar to this file.
	 *
	 * ONE strip with one name, "Similar to this", however it was answered. Two ways can answer and
	 * the heading's tooltip says which: the perceptual hash every file carries (a video's
	 * videohash, a picture's pHash), which works on every install and pairs frames that nearly
	 * match; or Smart Search, a model comparing what the pictures are of, for the files it has
	 * described. The server reports which (`tier`); two headings for one strip read as two sections.
	 *
	 * The heading is a way onwards, as the Same music strip's is: it opens the Files wall filtered
	 * to `like:<id>`, which is the same answer at full length with every other filter to hand.
	 *
	 * Absent when there is nothing to draw, which is most installs: a heading over an empty strip
	 * reports a fault where there is none.
	 *
	 * Nothing here decides who may see what: every file in the answer is already resolved against
	 * whoever is asking, so this draws what it is given.
	 *
	 * Tiles, not bare pictures: the same component every wall draws, at the size this strip has
	 * room for, so a video moves under the cursor as it does on every wall, and the hover clip,
	 * running time, shared and hidden marks, keyboard focus and Enter all come with it.
	 *
	 * The clip is fetched by the small shared loader rather than the grid's machinery: the grid
	 * runs a pool of video slots because it plays things nobody is pointing at, and this only plays
	 * what is under the cursor.
	 */
	import Button from '$lib/components/common/Button.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Note from '$lib/components/common/Note.svelte';
	import Scroller from '$lib/components/common/Scroller.svelte';
	import Fold from '$lib/components/common/Fold.svelte';
	import { SideScroll } from '$lib/components/common/side-scroll.svelte';
	import Tile from '$lib/components/Tile.svelte';
	import { showStranger } from '$lib/player/asset-view';
	import { thumbUrl } from '$lib/entity/art';
	import { canPreview, HoverPreviews } from '$lib/grid/hover-preview.svelte';
	import { aspectOf } from '$lib/grid/justify';
	import {
		findSimilar,
		SEMANTIC_ENABLED_KEY,
		type SimilarItem,
		type SimilarPage
	} from '$lib/search/semantic.svelte';
	import SettingLink from '$lib/components/common/SettingLink.svelte';
	import { session } from '$lib/shell/session.svelte';
	import { fetchSettingValues } from '$lib/settings-ui/settings';
	import { settingChanges, whenChanged } from '$lib/library/changes.svelte';
	import { likeWall } from '$lib/search/like';
	import { onDestroy, onMount } from 'svelte';

	interface Props {
		id: string;
		/**
		 * Why this file can never be compared, in the words of whatever read it. Null for every
		 * ordinary file, including one whose fingerprints have simply not been taken yet.
		 *
		 * Passed in rather than asked for here, because the file's own record already carries it:
		 * a second request for a fact that arrived with the file would be a second answer free to
		 * disagree with the one on the record two panels away.
		 */
		cannotCompare?: string | null;
	}

	let { id, cannotCompare = null }: Props = $props();

	/**
	 * How tall one tile in the strip is; its width follows from the file's own shape.
	 *
	 * A strip is a justified row with the height chosen rather than solved for, like Browse's rows,
	 * so a portrait clip is shown uncropped here as everywhere else. `aspectOf` is the grid's own,
	 * including the clamps that stop one broken file taking the whole strip and the assumption made
	 * for a file not yet measured.
	 *
	 * The same number as `--strip-tall` in `app.css`, the design's copy. Written here as well
	 * because `Tile` takes its intrinsic size as a number and a script cannot read a stylesheet
	 * before the first paint. The test beside this file holds the two equal.
	 */
	const TALL = 96;

	let page = $state<SimilarPage | null>(null);

	/*
	 * One row that scrolls sideways across the full width, with an arrow at each end while there is
	 * more that way, so it says there is more of it and reads as a pair with the faces strip below.
	 *
	 * `SideScroll` is that behaviour, shared with `FacesInThis` rather than written twice: two
	 * strips that scrolled at different paces would read as one of them broken. `Scroller
	 * horizontal` owns the scrolling itself and the sideways wheel.
	 */
	const strip = new SideScroll();

	$effect(() => strip.watch());

	const previews = new HoverPreviews();
	onDestroy(() => previews.dispose());

	/*
	 * The file this strip holds, as a string that only moves when the file does. A caller may hand
	 * `id` through an object that is replaced whenever its record is read again (a view counted,
	 * a job's bell), and a prop read through it re-runs everything keyed on it even though the id
	 * is the same; a derived stops at an equal string, so the strip empties only for another file
	 * and never blinks under a playing clip.
	 */
	const heldId = $derived(id);

	$effect(() => {
		// A different file is a different question, so anything held for the last one goes first:
		// otherwise the previous file's lookalikes sit under the new one until the answer arrives.
		const asked = heldId;
		page = null;
		void (async () => {
			try {
				const found = await findSimilar(asked);
				if (asked === heldId) page = found;
			} catch {
				// A feature nobody switched on answering nothing is not something to tell anybody
				// about. The strip simply does not appear.
				if (asked === heldId) page = null;
			}
		})();
	});

	const items = $derived<SimilarItem[]>(page?.items ?? []);

	/*
	 * WHETHER SMART SEARCH IS SWITCHED OFF, for the one sentence that says so.
	 *
	 * With the switch off the server answers by the perceptual hash alone (the switch governs every
	 * read of the description model), so the strip is narrower than it could be, and somebody who
	 * turned the switch off months ago cannot tell that from here. Said only to an admin, the one
	 * account that can turn it on, with the switch itself as the link: a guest is told nothing it
	 * can act on. False until read; a failed read says nothing rather than something wrong.
	 */
	let smartOff = $state(false);

	async function readSwitch() {
		if (!session.isAdmin) return;
		try {
			smartOff = (await fetchSettingValues()).get(SEMANTIC_ENABLED_KEY) === false;
		} catch {
			smartOff = false;
		}
	}

	onMount(() => void readSwitch());
	/* Read again when a setting moves, so the sentence goes the moment the switch is turned on. */
	whenChanged(settingChanges, () => void readSwitch());

	const saySwitchOff = $derived(smartOff && page?.tier === 'matches' && items.length > 0);

	/*
	 * Which way answered, said in the heading's tooltip rather than in a second heading. The hash
	 * line says the comparison and makes no promise of a copy: two different files can hash close.
	 */
	const answeredBy = $derived(
		page?.tier === 'looks'
			? 'By Smart Search: other files that look alike.'
			: 'By perceptual hash: files whose frames nearly match.'
	);
</script>

{#snippet arrow(way: -1 | 1, name: 'chevron_left' | 'chevron_right', words: string)}
	<!--
		THE SHARED BUTTON, and it is NAMED rather than hidden: the same pair of controls, with the
		same reasoning, as the faces strip below this one. `Scroller`'s own arrows are `aria-hidden`
		because a menu's rows are walked by the arrow keys and brought into view on the way; a strip
		has no such walk, so a named button is reachable by everybody rather than by a pointer alone.
		They exist only while there is strip that way, so a file with few lookalikes has no extra tab
		stops at all.
	-->
	<Tooltip label={words}>
		<Button
			tone="ghost"
			size="small"
			icon={name}
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

{#if cannotCompare || items.length > 0}
	<section class="similar">
		<!-- The heading IS the way to the whole answer: an ordinary link, because it leaves the sheet
		     for a wall of files, and the tooltip on it says which way the strip was answered. The
		     arrow beside it folds the strip, as every section under the picture folds. -->
		<Fold
			section
			weight={450}
			summary="Similar to this"
			href={likeWall(id)}
			tip={answeredBy}
			remember="sift.file.fold.similar"
		>
			<!--
			A strip that says why it is empty, rather than not appearing.

			Absent when there is nothing to draw is right for a library nobody has fingerprinted,
			but a file whose frames the decoder refused will never gain a lookalike, and silence
			would read as "Sift looked and found nothing like it". A verdict is written the moment
			the decoder refuses, while the numbers the file was given while it still decoded are
			kept, so a file can carry this sentence and real matches at the same time. The sentence
			sits above the strip; the strip draws whenever there is anything in it.
		-->
			{#if cannotCompare}
				<Note tone="caution">
					This file can't be compared again &mdash; Sift couldn't read its frames, so it has no
					fingerprint left to match against.
				</Note>
			{/if}
			{#if saySwitchOff}
				<!-- The pattern every switched-off feature uses on a screen: say what is off and what
				     that leaves, then the switch itself as the link, landing on its row. -->
				<Note>
					Smart Search is off, so these are only files whose frames nearly match.
					<SettingLink section="importing" setting={SEMANTIC_ENABLED_KEY}>Turn it on</SettingLink>
				</Note>
			{/if}
			{#if items.length > 0}
				<div class="strip">
					{#if strip.canBack}{@render arrow(-1, 'chevron_left', 'Earlier lookalikes')}{/if}
					<Scroller horizontal onviewport={strip.take}>
						<ul>
							{#each items as item (item.id)}
								<li>
									<!--
									Moved within the sheet rather than navigated to, like every
									other way of stepping between files: this strip is inside the
									file's own sheet, and following a route would tear the sheet
									down.

									Given a place in the run, which is why this is `showStranger`
									and not `showAsset`. A lookalike is not in the list the panel
									was opened over, so `neighboursOf` would answer nulls and blank
									the bar's outer pair. The file is spliced in just after the one
									it was opened from, so Previous is where you came from and Next
									carries the list on (Randomize does the same).

									`runs` is the test every wall makes about a row (a video or a
									GIF has an end of its own and a still does not), and is what a
									run advancing by itself reads. Worked out here because this
									strip holds the row.
								-->
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
					{#if strip.canOn}{@render arrow(1, 'chevron_right', 'Later lookalikes')}{/if}
				</div>
			{/if}
		</Fold>
	</section>
{/if}

<style>
	/*
	 * A full-width row with the record under it, so the section is as tall as one row of tiles and
	 * nothing is stretched.
	 */
	.similar {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	/* The arrows and the scrolling region, on one line: the same shape `FacesInThis` wears, because
	   it is the same strip. The arrows take their own width and the region takes the rest;
	   `min-inline-size: 0` is what lets it, because a flex item's floor is its content and the run of
	   tiles inside is wider than the box by definition. */
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

	/*
	 * One row. `flex: none` on each tile so the run keeps its natural width inside the scrolling
	 * box rather than being squeezed.
	 *
	 * The padding stops the tiles being shaved; it is not spacing. A tile draws its hover ring
	 * outside its own box (`--elev-tile-lift`, and it rises by `--lift-y`), and this list
	 * sits directly in a scrolling region, which clips. A negative margin cannot pay for it:
	 * padding and pulling back cancel exactly, and the clip cares where the tile ends up. Four
	 * pixels of room costs eight of strip.
	 *
	 * All four sides, because the ring goes all the way round a tile.
	 */
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
