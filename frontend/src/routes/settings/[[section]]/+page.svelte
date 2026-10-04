<script lang="ts">
	/*
	 * Settings, opened at its own address.
	 *
	 * There is no page form of this either. See the asset route, which does the same thing for
	 * the same reason. Settings is a panel over whatever you were doing, so one address is one
	 * screen.
	 *
	 * Reaching settings from inside the app never runs this route: it pushes the address and the
	 * screen underneath stays mounted. This runs only on a direct hit or a refresh, and it puts the
	 * panel up at the address that is already correct. Closing it goes to the library.
	 *
	 * WHY NOT FRAMED: for the asset route's reason, and for a second one that is this route's own.
	 * Reached at a section that exists it draws nothing (the panel is the layout's), so a frame
	 * would stand empty behind it. Reached at one that does not, the stub below is already inset by
	 * the shell's own scrolling region: this route is NOT in `FULL_BLEED_ROUTES`, so `main` pads it
	 * and scrolls it, and a frame inside that box would be a second scrolling region inside the
	 * first with the page's inset counted twice. The stub's title stands exactly where a framed
	 * screen's title stands.
	 */
	import { onMount } from 'svelte';
	import { replaceState } from '$app/navigation';
	import { page } from '$app/state';
	import { Empty } from '$lib/components/common';
	import { enterSettings } from '$lib/settings-ui/settings-view';
	import { session } from '$lib/shell/session.svelte';
	import {
		firstSectionFor,
		isKnownSection,
		sectionFor,
		settingsPath
	} from '$lib/settings-ui/sections';

	// The first section on this account's own list: a guest never lands on an admin's pane.
	const section = $derived(page.params.section ?? firstSectionFor(session.isAdmin));
	const known = $derived(isKnownSection(section));
	/* An admin's pane is not a guest's, even at its own address: every request it makes would be
	   refused, so a guest is sent to their first section before the pane can ask anything. */
	const theirs = $derived(session.isAdmin || !sectionFor(section)?.admin);

	onMount(() => {
		if (!known) return;
		if (!theirs) {
			const first = firstSectionFor(false);
			replaceState(settingsPath({ section: first }), {});
			enterSettings(first);
			return;
		}
		/* The fragment names one setting and `?show=` a tab, and both survive a refresh and a pasted
		   link the same way the section does. `slice(1)` because `hash` keeps the `#`. An old address
		   is rewritten to the current one inside `enterSettings`, through the one resolver. */
		enterSettings(
			section,
			page.url.hash.slice(1) || undefined,
			page.url.searchParams.get('show') ?? undefined
		);
	});
</script>

<!-- A section that does not exist is the one thing drawn here. Opening a panel onto nothing would
     say less than the address being wrong does. -->
{#if !known}
	<Empty scope="page" icon="settings">That address isn't a settings page.</Empty>
{/if}
