<script lang="ts">
	import Scroller from '$lib/components/common/Scroller.svelte';
	import BackButton from '$lib/components/common/BackButton.svelte';
	/* Settings: a list of sections down the left, the chosen one filling the rest. */
	import { untrack, type Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Empty, MarkedText, Pressable } from '$lib/components/common';
	import { session } from '$lib/shell/session.svelte';
	import { labelFor, resolveAddress, SETTINGS_GROUPS, type SettingsSection } from './sections';
	import SettingsSearch from './SettingsSearch.svelte';
	import {
		firstMatch,
		firstResult,
		grouped,
		indexOf,
		matchedIn,
		nameToFind,
		type Searchable
	} from './search';
	import { crumbsOf } from './settings-path';
	import { revealNamed, RING_MS } from '$lib/settings-ui/settings-anchor.svelte';
	import { fetchSettings } from '$lib/settings-ui/settings';
	import { settingChanges } from '$lib/library/changes.svelte';
	import {
		openSettings,
		SETTINGS_LIST_ON_A_PHONE,
		showSettingsSection
	} from '$lib/settings-ui/settings-view';

	interface Props {
		/** The section being shown. Always one of the known ids. */
		current: string;
		/** Whether the section pane is the one on screen. Only differs from `true` on a phone. */
		showingSection: boolean;
		/** Told which section was chosen, instead of the list being links: in the panel the rows
		 * are buttons, since a navigation would unmount the panel over the screen somebody was
		 * on. */
		onselect?: (id: string) => void;
		children: Snippet;
	}

	let { current, showingSection, onselect, children }: Props = $props();

	/* The index, built once when the shell mounts from the same `/settings` read every pane makes
	 * plus what the panes declare. Empty until that lands, rather than answering half. */
	let found = $state<Searchable[]>([]);

	/* What has been typed into the box, held HERE: what it matches, where results go and what
	 * the pane shows are facts about the two columns, which the box cannot see. */
	let typed = $state('');
	const searching = $derived(typed.trim().length > 0);

	/* The pane's scrolling box, handed over by the shared scroll region once it exists. */
	let paneBox: HTMLElement | null = null;

	/* A section opens at its top: the pane is one scrolling box for every section. */
	$effect.pre(() => {
		void current;
		if (paneBox) paneBox.scrollTop = 0;
	});

	$effect(() => {
		/* Read again when a setting moves: which settings are declared depends on the role (a
		 * guest is not sent installation-wide ones). */
		void settingChanges.generation;
		let alive = true;
		void fetchSettings()
			.then((sections) => {
				if (alive) found = indexOf(sections);
			})
			// Silent. The search box is an accelerator, not the way in: the list beside it still
			// reaches every section, so a failed index is a slower screen and not a broken one.
			.catch(() => undefined);
		return () => {
			alive = false;
		};
	});

	/* Opening a result, through the two calls a link would make (`showSettingsSection` inside
	 * the panel, `openSettings` outside). */
	function open(section: string, key?: string, show?: string, name?: string) {
		const named = () => {
			if (!key && name) void revealNamed(name, resolveAddress(section, key, show).section);
		};
		if (!onselect) {
			openSettings(section, key, show);
			named();
			return;
		}
		if (!key && !show) {
			onselect(section);
			named();
			return;
		}
		/* A result that names a ROW, inside the panel: the section swaps and the row is rung as
		 * ONE history step, since a replace then a push would leave closing the panel two clicks
		 * away. */
		showSettingsSection(section, key, show);
	}

	/* A guest is shown the sections a guest can use. */
	const groups = $derived(
		SETTINGS_GROUPS.map((group) => ({
			...group,
			sections: group.sections.filter((section) => !section.admin || session.isAdmin)
		})).filter((group) => group.sections.length > 0)
	);

	/** Every section this account is offered, flat: what a result is allowed to be about. */
	const offered = $derived(groups.flatMap((group) => group.sections));

	/* What was found, under the section each thing is on (`grouped`), from the same list the
	   guest filter allows, so a search offers no pane the list lacks. */
	const results = $derived(grouped(found, offered, typed));

	/* THE PANE FOLLOWS THE FIRST THING FOUND, as it is typed, so the two columns are one
	 * thought. */
	/* EVERY RESULT ROW, FLAT, IN THE ORDER THEY ARE DRAWN: what the arrow keys walk, so "down"
	 * means down across a section boundary too. */
	const walk = $derived(
		results.flatMap((group) => [
			{
				id: `found-${group.section.id}`,
				section: group.section.id,
				key: undefined as string | undefined,
				show: undefined as string | undefined,
				name: undefined as string | undefined
			},
			/* The entry's OWN section, not the group's: an entry naming a retired section or a tab is
			   opened through the resolver, which knows the tab; the group only knows the pane. */
			...group.entries.map((entry) => ({
				id: `found-${group.section.id}-${entry.key ?? entry.name}`,
				section: entry.section,
				key: entry.key,
				show: entry.show,
				name: nameToFind(entry)
			}))
		])
	);

	/** Which row the arrows are on. -1 is none, which is where every fresh search starts. */
	let cursor = $state(-1);

	/* Back to none whenever what was typed changes, or a kept index would light another row. */
	$effect(() => {
		void typed;
		cursor = -1;
	});

	const active = $derived(cursor >= 0 ? walk[cursor] : undefined);

	/* The arrows and Enter, heard on the SLOT, since the list is this screen's and a box is a
	 * box. */
	function onSearchKeys(event: KeyboardEvent) {
		if (!searching || walk.length === 0) return;
		if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
			event.preventDefault();
			const down = event.key === 'ArrowDown';
			// From nowhere, down lands on the first and up lands on the last: the two ends a person
			// means by those keys before anything is picked.
			cursor =
				cursor < 0
					? down
						? 0
						: walk.length - 1
					: (cursor + (down ? 1 : -1) + walk.length) % walk.length;
			document.getElementById(walk[cursor].id)?.scrollIntoView({ block: 'nearest' });
			return;
		}
		if (event.key === 'Enter') {
			// Nothing walked to is the first result, opened the way pressing it opens it.
			const target = active ?? firstResult(results, typed);
			if (!target) return;
			event.preventDefault();
			choose(target.section, target.key, target.show, target.name);
		}
	}

	/* Opening a result, as a person rather than as a preview: the box empties, so the section
	 * list comes back rather than yesterday's search over it. */
	function choose(section: string, key?: string, show?: string, name?: string) {
		open(section, key, show, name);
		typed = '';
	}

	/* A settings path pasted into the box goes there immediately: the paste IS the choice. */
	function onSearchPaste(event: ClipboardEvent) {
		const text = event.clipboardData?.getData('text/plain') ?? '';
		const crumbs = crumbsOf(text);
		if (!crumbs) return;
		const groups = grouped(found, offered, text);
		const target = firstResult(groups, text);
		if (!target) return;
		event.preventDefault();
		choose(target.section, target.key, target.show, target.name);
		/* The box says where the path went while the row's ring lasts (the row's name, or the
		   section's), since an emptied box reads as a dropped paste. */
		landed = groups[0]?.entries[0]?.name ?? groups[0]?.section.label;
	}

	/* What a pasted path opened, said in the box until it is typed in or the moment passes. */
	let landed = $state<string | undefined>(undefined);
	$effect(() => {
		if (landed === undefined) return;
		const gone = setTimeout(() => (landed = undefined), RING_MS);
		return () => clearTimeout(gone);
	});
	$effect(() => {
		if (searching) landed = undefined;
	});

	let previewing: string | null = null;
	$effect(() => {
		const asked = searching;
		const first = firstMatch(results);
		untrack(() => {
			if (!asked) {
				// Emptied. The pane stays where the preview left it, which is where the person was
				// last looking: putting it back would undo a section they may have gone to.
				previewing = null;
				return;
			}
			if (first === null || first === previewing) return;
			previewing = first;
			if (first !== current) open(first);
		});
	});

	/* Through the one declaration, a retired id followed. See `labelFor`. */
	const labelOf = labelFor;
</script>

<div class="settings" class:showing-section={showingSection}>
	<!-- Not a heading: the page's one h1 is the section's. -->
	<p class="title">Settings</p>

	<!--
		Above the list, as a way to a section you are NOT in (above the pane it would read as filtering
		it).
	-->
	<!-- The keys are heard here (`onSearchKeys`); `role="none"`, a slot around the field. -->
	<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
	<div class="search-slot" onkeydown={onSearchKeys} onpaste={onSearchPaste} role="none">
		<SettingsSearch bind:typed activeId={active?.id} {landed} />
	</div>

	<!--
		ONE COLUMN, holding either the list or what a search found, never both: drawn by the box above,
		results would grow its row and push the pane away, so they stand in the column's own bounded
		track, dressed as the rows they replace.
	-->
	<div class="sections-slot">
		<Scroller>
			{#if searching}
				<!--
					What was found, gathered under the section each thing is on: a section is a row
					(pressing it opens that pane), its finds indented under it without icons.
				-->
				<nav class="results" aria-label="Search results">
					{#if results.length === 0}
						<div class="no-match"><Empty scope="block">Nothing here matches that.</Empty></div>
					{:else}
						<ul>
							{#each results as group (group.section.id)}
								{@const here = group.section.id === current}
								{@const id = `found-${group.section.id}`}
								<li>
									<Pressable
										{id}
										class="item {here ? 'active' : ''} {active?.id === id ? 'on' : ''}"
										feedback="wash"
										radius="sm"
										aria-current={here ? 'page' : undefined}
										onclick={() => choose(group.section.id)}
									>
										<Icon name={group.section.icon} size={18} />
										<!-- The name in ONE box: a row is a flex line, so a mark's runs would otherwise
										     be separate flex children with the row's gap between them. -->
										<span class="found">
											<MarkedText text={group.section.label} {typed} />
										</span>
									</Pressable>

									{#if group.entries.length > 0}
										<ul class="under">
											{#each group.entries as entry, at (`${at}:${entry.key ?? entry.name}`)}
												{@const id = `found-${group.section.id}-${entry.key ?? entry.name}`}
												<li>
													<Pressable
														{id}
														class="item {active?.id === id ? 'on' : ''}"
														feedback="wash"
														radius="sm"
														onclick={() =>
															choose(entry.section, entry.key, entry.show, nameToFind(entry))}
													>
														<!--
															The name and, for a row found by its HELP,
															the sentence that matched, inside the one
															`found` box (no flex gap).
														-->
														<span class="found">
															<MarkedText text={entry.name} {typed} />
															{#if entry.help && matchedIn(entry, typed) === 'help'}
																<span class="why">
																	<MarkedText text={entry.help} {typed} />
																</span>
															{/if}
														</span>
													</Pressable>
												</li>
											{/each}
										</ul>
									{/if}
								</li>
							{/each}
						</ul>
					{/if}
				</nav>
			{:else}
				<nav class="sections" aria-label="Settings sections">
					{#each groups as group (group.heading)}
						<div class="group">
							{#if group.heading}
								<p class="heading section-label" id="group-{group.heading}">{group.heading}</p>
							{/if}
							<ul aria-labelledby={group.heading ? `group-${group.heading}` : undefined}>
								{#each group.sections as section (section.id)}
									{@const active = section.id === current}
									<li>
										{#if onselect}
											<!-- A row of the settings list: `Pressable`, a whole row identical to the `<a>`
											     below, which is the same row when this shell is a page. -->
											<Pressable
												class="item {active ? 'active' : ''}"
												feedback="wash"
												radius="sm"
												aria-current={active ? 'page' : undefined}
												onclick={() => onselect(section.id)}
											>
												<Icon name={section.icon} size={18} />
												{section.label}
											</Pressable>
										{:else}
											<a
												href="/settings/{section.id}"
												class="item"
												class:active
												aria-current={active ? 'page' : undefined}
											>
												<Icon name={section.icon} size={18} />
												{section.label}
											</a>
										{/if}
									</li>
								{/each}
							</ul>
						</div>
					{/each}
				</nav>
			{/if}
		</Scroller>
	</div>

	<!-- A section, not a second <main>: the app shell already renders one around this. -->
	<div class="pane-slot">
		<!-- Only on a phone, where the list is More, so the way back is More's own address. -->
		<div class="back-slot">
			<BackButton to={SETTINGS_LIST_ON_A_PHONE.href} label={SETTINGS_LIST_ON_A_PHONE.label} />
		</div>
		<Scroller onviewport={(element) => (paneBox = element)}>
			<section class="pane" aria-label={labelOf(current)}>
				<div class="measure">
					{@render children()}
				</div>
			</section>
		</Scroller>
	</div>
</div>

<style>
	.settings {
		display: grid;
		grid-template-columns: 220px minmax(0, 1fr);
		/* The title takes its own column only, so the pane spanning the search row and the list's row
		   starts its title level with the search box; the search row stays the box's height. */
		grid-template-areas:
			'title .'
			'search pane'
			'nav pane';
		grid-template-rows: auto auto minmax(0, 1fr);
		/* Rows and columns want different gaps. Between the columns it separates two things; under
		   the title it is the space beneath a heading, and the column gap is far too much for that. */
		gap: var(--space-5) var(--space-8);
		align-items: start;
		padding: var(--space-6);
		/* No cap of its own: what holds this already has one, and a second would leave a band of panel. */
		max-inline-size: none;
	}

	.search-slot {
		grid-area: search;
		/* No scrolling of its own: the results are capped at twelve, and nested scrolling is the
		   fault `PageFrame` documents. */
		min-inline-size: 0;
	}

	.title {
		grid-area: title;
		/* The list rows' own inset, so "Settings" starts exactly above the section names. */
		padding-inline: var(--space-3);
	}

	/* The grid's cells, scrolling inside in the shared region, whose bar floats over the content. */
	.sections-slot {
		grid-area: nav;
		min-block-size: 0;
		min-inline-size: 0;
	}

	.pane-slot {
		grid-area: pane;
		min-block-size: 0;
		min-inline-size: 0;
	}

	/* Two columns, two scrollbars: one scroller around the shell would carry the section list
	 * off screen. */
	@media (min-width: 768px) {
		.settings {
			block-size: 100%;
			/* A grid item will not shrink below its content without this, so a long section would
			   push the shell taller than the frame and hand the scroll back to the outside. */
			min-block-size: 0;
			/* Not `start`: the columns FILL the row, or they are as tall as their content. */
			align-items: stretch;
		}

		/* And the title takes the same allowance as the list, so "Settings" and the section
		 * names share one line: the column's ring allowance plus the row's own padding, written
		 * as that sum. */
		.title {
			padding-inline-start: calc(var(--space-2) + var(--space-3));
		}

		/* The pane ends where the shell's own padding ends, matching the panel's other margin; a
		 * scrollbar is painted at its element's edge, so moving the bar means moving the pane. */
		.pane {
			padding-inline-end: 0;
		}

		/* THE BOX LINES UP WITH THE ROWS UNDER IT: inset at the start by the list's ring
		 * allowance, and at the end by the scrollbar's, so field and results share both edges. */
		.search-slot {
			padding-inline: var(--space-2) var(--space-3);
			/* The pane's own top inset, from the same token, so box and section title start level. */
			padding-block-start: var(--space-2);
		}

		.sections,
		.results,
		.pane {
			min-block-size: 0;
			/* A scrolling container clips a focus ring at its edge, so three sides get room; the
			   end stays unpadded, or the scrollbar moves in from the panel edge. */
			padding-block: var(--space-2);
			padding-inline-start: var(--space-2);
		}

		/* The gutter the copy button stands in, left of every name on a pane (`PathCopy`): the
		 * scroller reaches into the column gap by its width and the pane pads it back, so
		 * nothing is clipped. */
		.pane-slot {
			margin-inline-start: calc(-1 * (var(--space-6) + var(--space-1)));
		}

		.pane {
			padding-inline-start: calc(var(--space-2) + var(--space-6) + var(--space-1));
		}

		/* A ROW STOPS SHORT OF THE SCROLLBAR RATHER THAN RUNNING UNDER IT: padding on the
		 * CONTENT (`--space-3`, the sheet's gutter in `app.css`), since a margin would move the
		 * bar too. */
		.sections,
		.results {
			padding-inline-end: var(--space-3);
		}

		/* A hand's width between the last control and the bar, as padding inside the scroller. */
		.pane > :global(*) {
			padding-inline-end: var(--space-4);
		}

		/* The frame stops scrolling so the columns can, and drops its padding (the shell has its
		 * own): which element scrolls is this screen's to say, aimed by `:has` at the frame
		 * holding settings. */
		:global(main:has(.settings .pane)) {
			padding: 0;
			overflow: hidden;
		}
	}

	/* No bottom margin: the grid's row gap is what holds the space under it, and a margin as
	   well would push the list down past the section title it is meant to line up with. */
	.title {
		margin: 0;
		font: var(--text-display);
	}

	.group + .group {
		margin-block-start: var(--space-5);
		padding-block-start: var(--space-5);
		border-block-start: 1px solid var(--sift-line);
	}

	.heading {
		margin: 0 0 var(--space-2);
		padding-inline: var(--space-3);
		/* Not a target. A heading that highlights under the pointer is a heading somebody tries
		   to click, which is exactly the confusion between a group and a section this avoids. */
		cursor: default;
		user-select: none;
	}

	ul {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 2px;
	}

	/* One rule for both, so a row cannot look like a link on one screen and a button on the
	   other. */
	nav :global(.item) {
		position: relative;
		/* Flex, so an item's icon and label share a line; a heading has no icon, which tells them apart. */
		display: flex;
		align-items: center;
		gap: var(--space-3);
		inline-size: 100%;
		padding: var(--space-2) var(--space-3);
		border: 0;
		border-radius: var(--radius-sm);
		background: none;
		color: var(--sift-ink-2);
		text-align: start;
		text-decoration: none;
		font: var(--text-body);
		cursor: pointer;
		/* A row is pressed, not read: a press that drags across one does not select its words. */
		user-select: none;
		transition: background-color var(--dur-instant) var(--ease);
	}

	/* ONE appearance for both ways of aiming at a row, keyboard and pointer, as in the search dropdown. */
	nav :global(.item:hover),
	nav :global(.item.on) {
		background: var(--sift-surface-2);
		color: var(--sift-ink);
	}

	/* The same treatment the rail uses for where you are, so "selected" means one thing in the app. */
	nav :global(.item.active) {
		background: var(--sift-accent-bg);
		color: var(--sift-accent-text);
	}

	/* The bar that says which section you are in, INSIDE the row's empty leading padding, since
	 * a scrolling box clips anything drawn past its edge (the rule `app.css` records for focus
	 * rings). */
	nav :global(.item.active::before) {
		content: '';
		position: absolute;
		inset-block: 6px;
		inset-inline-start: 0;
		inline-size: 3px;
		border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
		background: var(--sift-accent);
	}

	/* Air between one section's results and the next, on the outer list only, so a group keeps
	 * the list's 2px rhythm. */
	.results > ul > li + li {
		margin-block-start: var(--space-4);
	}

	/* What was found ON a section, under it, with no indent of its own: one left edge down the
	 * column; the section is told apart by its icon and accent. */
	.results .under {
		padding-inline-start: 0;
	}

	/* One name, whatever it is made of. See the markup: without it every marked run is a flex
	   child of the row and wears the row's gap, which puts a hand's width inside a word. */
	.results .found {
		min-inline-size: 0;
	}

	/* The sentence that explains a row nothing else on it explains. */
	.results .why {
		display: block;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	/* Nothing matched. The sentence is `Empty`; only the INSET is this screen's, so that the
	   line starts where a row's label starts rather than hard against the edge. */
	.results .no-match {
		padding: var(--space-2) var(--space-3);
	}

	.pane {
		min-inline-size: 0;
	}

	/* No cap on the rows: a row's name and help cap themselves at 68ch, and a control sits at the edge. */
	.measure {
		max-inline-size: none;
	}

	/* The section's name is drawn by the frame (`SettingsTitle`), never a `:global(h1)` here. */

	/* Both panes are on screen above this width, so no way back and no breadcrumb: the list is the trail. */
	.back-slot {
		display: none;
	}

	@media (max-width: 767px) {
		/* The strip at the top of a section: the way back at its start, the panel's close over
		   its end; a finger's height, so the two stand level. */
		.back-slot {
			display: flex;
			flex: none;
			align-items: center;
			min-block-size: var(--touch-target);
			padding-inline-end: calc(var(--touch-target) + var(--space-2));
		}

		/* A phone scrolls the SECTION, as the wide layout does: the panel is the whole screen
		 * and clips, so the shell takes its height and the section's scroller takes what the
		 * strip leaves. */
		.settings {
			block-size: 100%;
			min-block-size: 0;
		}

		.pane-slot {
			display: flex;
			flex-direction: column;
			/* Filling its row, not as tall as its content: the grid sets every cell to the top
			   (see the wide layout's `stretch`, which is the same answer to the same question). */
			align-self: stretch;
		}

		.pane-slot > :global(.scroll-root) {
			flex: 1 1 0;
			/* The bar at the screen's edge, not over the ends of lines: the scroller reaches out
			   through the inset and the section pads it back, on both sides. */
			margin-inline: calc(-1 * (var(--space-4) + var(--safe-left)))
				calc(-1 * (var(--space-4) + var(--safe-right)));
		}

		.pane {
			padding-inline: calc(var(--space-4) + var(--safe-left))
				calc(var(--space-4) + var(--safe-right));
		}

		/* The last row clears the home bar: the section scrolls to the screen's foot, and its end is
		   padded by the foot's safe area rather than the shell stopping short of the glass. */
		.pane {
			padding-block-end: calc(var(--space-4) + var(--safe-bottom));
		}

		.settings {
			grid-template-columns: minmax(0, 1fr);
			/* One column, one of the last two rows filled; every area named, or an element would
			   be placed at `auto`. */
			grid-template-areas:
				'title'
				'search'
				'nav'
				'pane';
			grid-template-rows: auto auto auto minmax(0, 1fr);
			gap: 0;
			/* The screen's edges, less what the phone draws over them: the notch at the top or
			   at a side when it is turned. */
			padding: calc(var(--space-4) + var(--safe-top)) calc(var(--space-4) + var(--safe-right)) 0
				calc(var(--space-4) + var(--safe-left));
		}

		.title {
			margin-block-end: var(--space-4);
		}

		/* With a section open the search box goes with the list. */
		.settings.showing-section .search-slot {
			display: none;
		}

		/* One at a time: the list, or the section. Choosing one is a navigation, so the browser's
		   back button already does the right thing and no state here has to remember anything. */
		.settings.showing-section .sections {
			display: none;
		}

		/* The screen's name goes with the list it names. */
		.settings.showing-section .title {
			display: none;
		}

		.settings:not(.showing-section) .pane {
			display: none;
		}
	}
</style>
