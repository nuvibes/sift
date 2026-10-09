<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'SettingLink',
		category: 'primitive',
		role: 'a link to a setting that lands on its row and rings it',
		basis: 'site:<a>',
		states: ['default', 'hover']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: it is an anchor. There is nothing to behave: the browser already knows how
	to be a link; a plain left-click opens the settings panel instead. */

	/*
	 * A link to one setting, kept a real anchor so a middle-click, a copied address and a tab work.
	 */
	import type { Snippet } from 'svelte';
	import { openSettingsInstead } from '$lib/settings-ui/settings-view';
	import { resolveAddress, settingsPath } from '$lib/settings-ui/sections';

	interface Props {
		/** The pane's id: `performance`, `library`, `connections`. See `settings-ui/sections`. */
		section: string;
		/** The setting's key: the address's fragment, which scrolls to the row and rings it. */
		setting?: string;
		/** The words. Say what is there, not "click here". */
		children: Snippet;
	}

	let { section, setting, children }: Props = $props();

	/* The current address, so a link to a moved section still works. */
	const href = $derived(settingsPath(resolveAddress(section, setting)));
</script>

<a class="setting-link" {href} onclick={(event) => openSettingsInstead(event, section, setting)}>
	{@render children()}
</a>

<style>
	/* No underline until hover or focus; the text accent, which holds 4.5:1 on every surface. */
	.setting-link {
		color: var(--sift-accent-text);
		text-decoration: none;
		text-underline-offset: 2px;
		border-radius: var(--radius-sm);
		/* Colour only: a fading underline reads as the text moving. */
		transition: color var(--dur-instant) var(--ease);
	}

	/* The underline alone: the accent's hover is not held to the text floor. */
	.setting-link:hover,
	.setting-link:focus-visible {
		text-decoration: underline;
	}

	/* A finger's reach on a phone, as a link-tone Button draws it. */
	@media (max-width: 767px) {
		.setting-link {
			position: relative;
		}

		.setting-link::after {
			content: '';
			position: absolute;
			inset-block: min(0px, calc((100% - var(--touch-target)) / 2));
			inset-inline: min(0px, calc((100% - var(--touch-target)) / 2));
		}
	}
</style>
