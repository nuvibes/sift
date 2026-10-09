<script lang="ts">
	/*
	 * Similar to this: one strip, answered by the perceptual hash or by Smart Search (the tooltip
	 * says which). The heading opens the Files wall at `like:<id>`. Absent when empty. Tiles, so
	 * clips play under the cursor; the shared loader fetches them.
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
		/** Why this file can never be compared, off its own record. */
		cannotCompare?: string | null;
	}

	let { id, cannotCompare = null }: Props = $props();

	/**
	 * Justified at a chosen height; the same as `--strip-tall`, held equal by the test beside this.
	 */
	const TALL = 96;

	let page = $state<SimilarPage | null>(null);

	/* `SideScroll`, shared with `FacesInThis`. */
	const strip = new SideScroll();

	$effect(() => strip.watch());

	const previews = new HoverPreviews();
	onDestroy(() => previews.dispose());

	/* A string, so a replaced record object does not re-run everything for the same file. */
	const heldId = $derived(id);

	$effect(() => {
		// Anything held for the last file goes first.
		const asked = heldId;
		page = null;
		void (async () => {
			try {
				const found = await findSimilar(asked);
				if (asked === heldId) page = found;
			} catch {
				// A feature nobody switched on: the strip simply does not appear.
				if (asked === heldId) page = null;
			}
		})();
	});

	const items = $derived<SimilarItem[]>(page?.items ?? []);

	/* Smart Search switched off, said only to an admin, with the switch as the link. */
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
	whenChanged(settingChanges, () => void readSwitch());

	const saySwitchOff = $derived(smartOff && page?.tier === 'matches' && items.length > 0);

	/* Which way answered, in the heading's tooltip; the hash line promises no copy. */
	const answeredBy = $derived(
		page?.tier === 'looks'
			? 'By Smart Search: other files that look alike.'
			: 'By perceptual hash: files whose frames nearly match.'
	);
</script>

{#snippet arrow(way: -1 | 1, name: 'chevron_left' | 'chevron_right', words: string)}
	<!--
	Named buttons, unlike `Scroller`'s arrows: a strip has no keyboard walk to bring tiles into
	view.
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
		<!-- The heading is a link to the whole answer. -->
		<Fold
			section
			weight={450}
			summary="Similar to this"
			href={likeWall(id)}
			tip={answeredBy}
			remember="sift.file.fold.similar"
		>
			<!-- A file the decoder refused will never gain a lookalike, so it says why. -->
			{#if cannotCompare}
				<Note tone="caution">
					This file can't be compared again &mdash; Sift couldn't read its frames, so it has no
					fingerprint left to match against.
				</Note>
			{/if}
			{#if saySwitchOff}
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
									Moved within the sheet with `showStranger`, spliced after the
									file it was opened from, so the
									bar's Previous and Next still work.
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
	.similar {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	/* `min-inline-size: 0`: the run of tiles is wider than the box by definition. */
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

	/* The padding stops the clipping region shaving each tile's hover ring. */
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
