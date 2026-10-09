<script lang="ts">
	/*
	 * STATS: every figure of a period, family by family, each table a panel.
	 *
	 * The same answer Insights draws (`GET /api/insights`), in the board's families: the index down
	 * the side, and for each family its panels, each one table with its chart drawn small, its
	 * sentence and its Copy (`StatsPanel`). A board tile opens the view at `#<block>`, or at one
	 * panel's own id: the panels answering are scrolled to and ringed. Back returns to Insights at
	 * the period on screen; the tabs and arrows keep the address on this screen.
	 */
	import { tick } from 'svelte';

	import { page } from '$app/state';
	import {
		BackButton,
		Problem,
		SectionHeading,
		Skeleton,
		type Crumb
	} from '$lib/components/common';
	import SectionIndex from '$lib/components/charts/SectionIndex.svelte';
	import { PeriodAnswer } from '$lib/components/insights/answer.svelte';
	import PeriodBar from '$lib/components/insights/PeriodBar.svelte';
	import { STATS_PATH, addressOf, type Place } from '$lib/components/insights/period';
	import { INSIGHTS_WORDS, STATS_WORDS } from '$lib/components/insights/words';
	import PageFrame from '$lib/components/shell/PageFrame.svelte';
	import PageHeader from '$lib/components/shell/PageHeader.svelte';
	import { onAssetStateChange, reloadOnLibraryChange } from '$lib/library/changes.svelte';

	import { answers, familiesOf, type Family } from './panels';
	import StatsPanel from './StatsPanel.svelte';

	let { data }: { data: Place } = $props();

	const read = new PeriodAnswer(() => data);
	reloadOnLibraryChange(() => void read.reread());
	onAssetStateChange(() => void read.reread());
	const answer = $derived(read.answer);
	const failed = $derived(read.failed);
	const loading = $derived(read.loading);

	const back = $derived(addressOf(data));
	const crumbs = $derived<Crumb[]>([
		{ label: INSIGHTS_WORDS.title, href: back },
		{ label: STATS_WORDS.title }
	]);
	const families = $derived(familiesOf(answer?.blocks ?? []));

	const sectionOf = (family: Family) => `family-${family}`;
	const paintOf = (family: Family) => `var(--family-${family}-ink)`;
	const entries = $derived(
		families.map(({ family, panels }) => {
			const tables = panels.filter((one) => one.table !== null).length;
			return {
				id: sectionOf(family),
				label: STATS_WORDS.families[family],
				note: tables > 0 ? STATS_WORDS.tables(tables) : undefined,
				paint: paintOf(family)
			};
		})
	);

	/* Where the address sends the reader: a block, a panel or a family. */
	const hash = $derived(decodeURIComponent(page.url.hash.slice(1)));
	const current = $derived(
		families.find(
			({ family, panels }) =>
				sectionOf(family) === hash || panels.some((panel) => answers(panel, hash))
		)?.family ?? null
	);

	/* Once the panels the address names are drawn, bring the first into view and hand it focus. */
	$effect(() => {
		const to = hash;
		if (to === '' || families.length === 0) return;
		void tick().then(() => {
			const found =
				document.getElementById(to) ??
				[...document.querySelectorAll<HTMLElement>('[data-block]')].find(
					(one) => one.dataset.block === to
				);
			found?.scrollIntoView?.({ block: 'start' });
			if (found?.tabIndex === -1) found.focus({ preventScroll: true });
		});
	});
</script>

<svelte:head><title>{STATS_WORDS.title}</title></svelte:head>

<PageFrame {crumbs}>
	{#snippet header()}
		<div class="head">
			<BackButton to={back} label={INSIGHTS_WORDS.title} />
			<PageHeader title={STATS_WORDS.title} icon="insights">
				{#snippet lede()}{STATS_WORDS.headLine}{/snippet}
			</PageHeader>
		</div>
	{/snippet}
	{#snippet tools()}
		<PeriodBar place={data} {answer} {loading} path={STATS_PATH} />
	{/snippet}

	{#if failed}
		<Problem message={INSIGHTS_WORDS.failed} />
	{:else if answer}
		<div class="stats" class:stale={loading} aria-busy={loading}>
			<SectionIndex
				label={STATS_WORDS.index}
				{entries}
				current={current === null ? null : sectionOf(current)}
			/>
			<div class="families">
				{#each families as { family, panels } (family)}
					<section
						class="family"
						id={sectionOf(family)}
						aria-labelledby={`${sectionOf(family)}-title`}
					>
						<SectionHeading id={`${sectionOf(family)}-title`}>
							{STATS_WORDS.families[family]}
						</SectionHeading>
						<div class="panels">
							{#each panels as panel (panel.id)}
								<StatsPanel {panel} lit={answers(panel, hash)} />
							{/each}
						</div>
					</section>
				{/each}
			</div>
		</div>
	{:else}
		<Skeleton lines={6} />
	{/if}
</PageFrame>

<style>
	.head {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: var(--space-2);
	}

	/* The index down the side, the families taking the rest of the width. */
	.stats {
		display: grid;
		grid-template-columns: max-content minmax(0, 1fr);
		gap: var(--space-8);
		align-items: start;
	}

	.families {
		display: flex;
		flex-direction: column;
		gap: var(--space-10);
		min-inline-size: 0;
		container-type: inline-size;
	}

	.family {
		display: flex;
		flex-direction: column;
		gap: var(--space-4);
		scroll-margin-block-start: var(--space-4);
	}

	/*
	 * Panels side by side as the width allows, every row's panels one height. A long chart or a
	 * calendar takes the whole row; a chart over its table two rows beside two
	 * short panels.
	 */
	.panels {
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		grid-auto-flow: row dense;
		gap: var(--space-4);
	}

	.panels > :global(.wide) {
		grid-column: 1 / -1;
	}

	@container (min-width: 720px) {
		.panels {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}

		.panels > :global(.tall:not(.wide)) {
			grid-row: span 2;
		}
	}

	@container (min-width: 1200px) {
		.panels {
			grid-template-columns: repeat(3, minmax(0, 1fr));
		}
	}

	@media (max-width: 767px) {
		.stats {
			grid-template-columns: minmax(0, 1fr);
		}
	}

	.stale {
		opacity: 0.55;
		transition: opacity var(--dur-instant) var(--ease);
	}
</style>
