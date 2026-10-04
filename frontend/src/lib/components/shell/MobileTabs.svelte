<script lang="ts">
	/* NOT ON THE GALLERY: the phone tab bar is a singleton the layout renders, fixed to the bottom of the
	   window and hidden above 767px. Drawing a second one would put a duplicate bar over the page at
	   the one width where it is visible, which is the width the gallery is hardest to read at. */

	import { page } from '$app/state';
	import Icon from '$lib/components/Icon.svelte';
	import { session } from '$lib/shell/session.svelte';
	import { MOBILE_TABS, isTabActive } from './nav';

	const pathname = $derived(page.url.pathname);

	/* A guest is not offered a tab whose screen is an admin's, for the rail's reason: the server
	   refuses what is behind it, and a door that will not open is worse than none. */
	const tabs = $derived(MOBILE_TABS.filter((item) => !item.admin || session.isAdmin));
</script>

<nav class="tabs" aria-label="Main">
	{#each tabs as item (item.href)}
		{@const active = isTabActive(item, pathname)}
		<!-- Every tab is a plain link to a screen of its own: More is a list at its own address, not
		     a panel, so Back from anything opened on it lands on it again. -->
		<a href={item.href} class="tab" class:active aria-current={active ? 'page' : undefined}>
			<Icon name={item.icon} filled={active} size={20} />
			<span>{item.label}</span>
		</a>
	{/each}
</nav>

<style>
	/* Hidden by default and shown on a narrow window, and the rule lives here rather than in the
	   layout. A component's own scoped style outranks a :global() rule aimed at it from outside
	   (same class, one more selector) so the layout saying `display: none` loses to this saying
	   `display: flex`, and the tabs sit on top of the desktop app. Each piece of the shell decides
	   its own visibility; the layout arranges what is showing. */
	.tabs {
		grid-area: tabs;
		display: none;
		background: var(--sift-surface-1);
		border-top: 1px solid var(--border);
		/* Clear of the home indicator on a phone, and nothing extra where there isn't one. */
		padding-bottom: var(--safe-bottom);
	}

	@media (max-width: 767px) {
		.tabs {
			display: flex;
		}
	}

	.tab {
		flex: 1;
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 2px;
		/* 44 is the smallest a finger reliably hits. Nothing tappable goes under it. */
		min-height: var(--tab-bar-height);
		color: var(--sift-ink-2);
		text-decoration: none;
		font: var(--text-label);
		/* Becoming the lit tab is a state change, so the colour arrives over `--dur-instant` rather
		   than between frames, as a selection does everywhere; the glyph's fill follows on its own
		   clock (`Icon`). The bar itself never comes or goes: it is the phone's floor. */
		transition: color var(--dur-instant) var(--ease);
	}

	.tab.active {
		color: var(--sift-accent-text);
	}
</style>
