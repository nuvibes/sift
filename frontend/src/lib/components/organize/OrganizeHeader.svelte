<script lang="ts" module>
	import type { Snippet } from 'svelte';

	/**
	 * How a panel hands the tab line its controls: the one slot on an Organize screen for them.
	 *
	 * Shaped exactly like `OnPaging`, for the same reason: a panel is drawn by the queue route,
	 * which draws the header, so the panel cannot put anything in the header itself. One slot, at
	 * the far end of the row the tabs are on, rather than each panel drawing its own band of
	 * controls over the work at its own height.
	 *
	 * A panel reports a snippet, and reports `null` as it goes, the half that is easy to forget:
	 * without it the next queue on the same screen would wear the last one's controls. A panel that
	 * reports nothing draws nothing, and the route treats never hearing from a panel as that.
	 */
	export type OnTools = (tools: Snippet | null) => void;
</script>

<script lang="ts">
	/*
	 * The top of every screen under Organize, so there is one answer to what one looks like.
	 *
	 * ## Why this exists
	 *
	 * Screens that each build their own top come to disagree: a queue's name in one title, "A group
	 * of faces" in another, a **Back button on the right** saying "All ignored" (a fourth kind of
	 * navigation, competing with the rail, the crumb trail and the tabs), a Back button and no
	 * trail at all on a third.
	 *
	 * The rule this settles: **there is no navigation on the right side of an Organize screen, and
	 * every band sits under the crumb trail rather than beside it.**
	 *
	 * So the shape is settled here and a screen supplies only what is its own: which queue it
	 * belongs to, what the last crumb says, and its sentence.
	 *
	 * ## The shape
	 *
	 * **The trail carries the navigation, at every depth.** It is already the way back on every
	 * other screen in the application, it says where you are as well as how to leave, and it costs
	 * no room, which a button on the right does, in the row the tabs need.
	 *
	 * **Every screen has a title**, the way every other page in Sift does: the page's name, which
	 * is the group's name where the queue is one tab of a page ("Faces"), the queue's own where it
	 * stands alone ("Shoots"), and the detail's where the screen is one item ("A group of faces").
	 * A screen ABOUT something (one person's identified faces) names that thing with `showTitle`.
	 *
	 * **The tabs sit directly under the title**, as they do under an entity page's cover, with the
	 * tab's own controls at the far end of their line. The sentence comes after them, because it
	 * describes the tab that is lit rather than the page.
	 */
	import PageHeader from '$lib/components/shell/PageHeader.svelte';
	import Tabs, { type TabLink } from '$lib/components/common/Tabs.svelte';
	import { titleOf } from '$lib/organize/bands';
	import { tabsFor } from '$lib/organize/bands';
	import { heldBoard } from '$lib/organize/organize.svelte';
	import type { IconName } from '$lib/design/icons';

	interface Props {
		/**
		 * Which queue this screen belongs to. What the tabs are drawn from, and what the middle
		 * crumb points at.
		 *
		 * A DETAIL screen names the queue it is a detail OF (the pile screen names `unidentified`
		 * or `ignored`) so the trail leads back through the pile it came from rather than jumping
		 * to the board.
		 *
		 * Absent for a fixed screen that is not a queue and is not under one: the decision record.
		 * It then has no tabs (it is in no group) and the trail is Organize, then its own name.
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
		/**
		 * The screen's own controls, drawn at the far end of the row.
		 *
		 * Deliberately NOT navigation. What belongs here is something the screen DOES (a rescan, a
		 * rule) and what does not is a way back, which the trail already is.
		 *
		 * The one exception is a FILTER's way out: "Back to the groups" on the small groups of
		 * Faces to name leaves a filter rather than the screen, which is why it shares the slot
		 * with the chip that entered it. A queue's panel fills this through `OnTools`; a screen
		 * drawing its own header passes it straight in.
		 */
		controls?: Snippet;
		/**
		 * A band of this screen's OWN, drawn where the group's tabs would be.
		 *
		 * For a screen whose alternatives are not the queue's siblings: one person's identified
		 * faces offers *Needs your input / Recognized by Sift / Confirmed*, which are
		 * three states of that person rather than three queues. Given here rather than as the
		 * screen's `controls` because it is the same KIND of thing as the group tabs and belongs in
		 * the same place: under the trail, never at the far end of the row.
		 */
		tabs?: TabLink[];
		/**
		 * Which of `tabs` is lit. Only meaningful beside an own band.
		 *
		 * Its own prop rather than reusing `here`, which is the last CRUMB: on one person's page the
		 * crumb is their name and the lit tab is which of their three states is showing, and folding
		 * the two together would put a tab key in the trail.
		 */
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

	/* The board is what names the queue in the trail. A queue screen fetches it for its own counts;
	   a DETAIL screen does not, and its middle crumb would then read "Organize". See `ensure`. */
	$effect(() => {
		void heldBoard.ensure();
	});

	/* The board as last held. Every Organize screen already reads it for its own counts, so the
	   tabs cost nothing extra, and a screen arriving cold draws no tabs for one frame rather than
	   asking a second time. */
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

	/*
	 * The page's name. A queue that is one tab of a page is titled with the page ("Faces"), the
	 * word the board's row and the trail's middle crumb already use (`titleOf`, `groupCrumb`), so
	 * the three cannot disagree; a queue standing alone with its own title. The board has to have
	 * answered for either, and until it has the title is the board's own name rather than a guess.
	 */
	const sharing = $derived(queue === undefined ? [] : tabsFor(queues, queue));
	const pageName = $derived(
		mine === undefined
			? undefined
			: titleOf({ lead: sharing[0] ?? mine, queues: sharing.length > 0 ? sharing : [mine] })
	);
	const pageTitle = $derived(showTitle ? (title ?? '') : (here ?? pageName ?? title ?? 'Organize'));
	/* Every Organize title wears a glyph: the one a screen names, or else its queue's own, the one
	   the board's row draws, so a detail screen (a group, a review) wears the glyph of the pile it
	   belongs to. */
	const queueIcon = $derived((sharing[0] ?? mine)?.icon as IconName | undefined);
	const pageIcon = $derived(showTitle ? icon : (icon ?? queueIcon));

	/*
	 * The trail is the frame's band (`PageFrame.crumbs`), and each Organize screen computes it with
	 * `organizeCrumbs` from the same board this header reads, which is why `ensure` above still
	 * runs here: the queue's title in the trail arrives with the board.
	 */
</script>

<PageHeader
	title={pageTitle}
	icon={pageIcon}
	controls={tabs.length > 0 ? undefined : controls}
	lede={tabs.length > 0 ? undefined : lede}
/>

{#if tabs.length > 0}
	<!-- The tabs directly under the title, with the lit tab's controls at the far end of their
	     line, then the sentence about the lit tab. -->
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
