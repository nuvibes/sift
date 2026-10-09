<script lang="ts">
	/*
	 * Settings, opened at its own address.
	 * WHY NOT FRAMED: for the asset route's reason, and for a second one that is this route's own.
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
		/* The fragment names one setting and `?show=` a tab, and both survive a refresh and a
		   pasted link the same way the section does. */
		enterSettings(
			section,
			page.url.hash.slice(1) || undefined,
			page.url.searchParams.get('show') ?? undefined
		);
	});
</script>

<!-- A section that does not exist is the one thing drawn here. -->
{#if !known}
	<Empty scope="page" icon="settings">That address isn't a settings page.</Empty>
{/if}
