<script lang="ts" module>
	import type { Snippet } from 'svelte';

	/**
	 * How a panel hands the tab line its controls; null on leaving, or the next queue wears them.
	 */
	export type OnTools = (tools: Snippet | null) => void;
</script>

<script lang="ts">
	/* The top of every Organize screen: the trail carries all navigation (nothing on the right),
	 * every screen has a title, and the tabs sit under it with the lit tab's sentence after. */
	import PageHeader from '$lib/components/shell/PageHeader.svelte';
	import Tabs, { type TabLink } from '$lib/components/common/Tabs.svelte';
	import { titleOf } from '$lib/organize/bands';
	import { tabsFor } from '$lib/organize/bands';
	import { heldBoard } from '$lib/organize/organize.svelte';
	import type { IconName } from '$lib/design/icons';

	interface Props {
		/**
		 * The queue this screen belongs to (a detail names its pile); absent for a fixed screen.
		 */
		queue?: string;
		/** The last crumb, and the title of a detail screen. Absent on a queue's own screen. */
		here?: string;
		/** The page's name where the screen is about one THING; see `showTitle`. */
		title?: string;
		icon?: IconName;
		/** Title the page with `title` and `icon`: a screen about one THING, whose name is the page. */
		showTitle?: boolean;
		lede?: Snippet;
		/** The screen's own controls at the row's end: things it does, never a way back. */
		controls?: Snippet;
		/** A band of the screen's own in place of the group's tabs (one person's three states). */
		tabs?: TabLink[];
		/** Which of `tabs` is lit; not `here`, which is the last crumb. */
		current?: string;
		/** The lit tab's count while its list is searched, in place of the board's count of all. */
		litCount?: number;
	}

	let {
		queue,
		here,
		title,
		icon,
		showTitle = false,
		lede,
		controls,
		tabs: given,
		current: lit,
		litCount
	}: Props = $props();

	/* A detail screen fetches the board too, or its middle crumb would read "Organize". */
	$effect(() => {
		void heldBoard.ensure();
	});

	/* The board as last held, which every Organize screen already reads. */
	const queues = $derived(heldBoard.found?.queues ?? []);
	const mine = $derived(queues.find((one) => one.name === queue));

	const tabs = $derived<TabLink[]>(
		given ??
			(queue === undefined ? [] : tabsFor(queues, queue)).map((one) => ({
				id: one.name,
				label: one.title,
				href: `/organize/${one.name}`,
				count: one.name === queue && litCount !== undefined ? litCount : one.count
			}))
	);

	/* Which tab is lit. A screen supplying its own band says so; otherwise it is this queue. */
	const current = $derived(lit ?? queue ?? '');

	/* Titled with the page's word, the one the board and the trail use, so the three agree. */
	const sharing = $derived(queue === undefined ? [] : tabsFor(queues, queue));
	const pageName = $derived(
		mine === undefined
			? undefined
			: titleOf({ lead: sharing[0] ?? mine, queues: sharing.length > 0 ? sharing : [mine] })
	);
	const pageTitle = $derived(showTitle ? (title ?? '') : (here ?? pageName ?? title ?? 'Organize'));
	/* A detail screen wears the glyph of the pile it belongs to. */
	const queueIcon = $derived((sharing[0] ?? mine)?.icon as IconName | undefined);
	const pageIcon = $derived(showTitle ? icon : (icon ?? queueIcon));
</script>

<PageHeader
	title={pageTitle}
	icon={pageIcon}
	controls={tabs.length > 0 ? undefined : controls}
	lede={tabs.length > 0 ? undefined : lede}
/>

{#if tabs.length > 0}
	<!--
	The tabs under the title, their controls at the line's end, then the lit tab's sentence.
	-->
	<div class="tab-line">
		<Tabs {tabs} {current} label={given ? 'What to show' : 'Sections on this page'} />
		<div class="tools">{@render controls?.()}</div>
	</div>
	{#if lede}
		<p class="lede">{@render lede()}</p>
	{/if}
{/if}

<style>
	.tab-line {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-3);
		margin-block-start: var(--space-3);
	}

	/* The tab's controls at the far end of the tab line. */
	.tools {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		margin-inline-start: auto;
	}

	/* The page header's own sentence, in its words and measure, under the tabs it describes. */
	.lede {
		max-width: 60ch;
		margin: var(--space-2) 0 0;
		font: var(--text-body);
		color: var(--sift-ink-3);
	}
</style>
