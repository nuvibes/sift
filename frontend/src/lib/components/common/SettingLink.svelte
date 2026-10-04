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
	   to be a link, and the one thing added here is that a plain left-click opens the settings PANEL
	   instead of navigating, which is `$lib/settings-ui/settings-view`'s rule and not a component's. */

	/*
	 * A link to ONE setting.
	 *
	 * ## What it is instead of
	 *
	 * A sentence of the form "that is switched off in Settings, under Performance", in a dialog,
	 * in an empty state, under a video that is stuttering. Such a sentence names a place and then
	 * leaves the reader to go and find it, on a pane with thirty rows on it, having lost whatever
	 * they were looking at to get there.
	 *
	 * ## Why it stays a real anchor
	 *
	 * Because everything a link can do is worth keeping: middle-click opens a tab, ctrl-click opens
	 * a tab, "copy link address" copies an address that works, and somebody with JavaScript failing
	 * still gets the page. `openSettingsInstead` takes only a plain left-click and leaves every
	 * modifier alone, for exactly that reason. A button would look identical and do none of it.
	 *
	 * ## Why the key is optional
	 *
	 * Some of these genuinely are about a whole pane: "add a stash-box" is not one row. Naming a
	 * key when there is one and the section when there is not keeps both honest, rather than
	 * inventing an anchor so every call site can look the same.
	 */
	import type { Snippet } from 'svelte';
	import { openSettingsInstead } from '$lib/settings-ui/settings-view';
	import { resolveAddress, settingsPath } from '$lib/settings-ui/sections';

	interface Props {
		/** The pane's id: `performance`, `library`, `connections`. See `settings-ui/sections`. */
		section: string;
		/**
		 * The setting's key, where the link is about one row.
		 *
		 * It becomes the address's fragment, and `$lib/settings-ui/settings-anchor` scrolls to that row and
		 * rings it for two seconds once the pane has drawn.
		 */
		setting?: string;
		/** The words. Say what is there, not "click here". */
		children: Snippet;
	}

	let { section, setting, children }: Props = $props();

	/* The address as it is NOW, through the one resolver: a link written against a section that has
	   since moved still copies, middle-clicks and opens in a tab at the current address. */
	const href = $derived(settingsPath(resolveAddress(section, setting)));
</script>

<a class="setting-link" {href} onclick={(event) => openSettingsInstead(event, section, setting)}>
	{@render children()}
</a>

<style>
	/*
	 * The accent, and no underline until a pointer or the keyboard arrives. A permanent rule under
	 * every settings link (several in a single paragraph) stripes the running text; instead the
	 * keyboard gets the focus ring and the underline, and a pointer gets the underline as soon as
	 * it is over the words, so nobody looking for the link relies on colour alone.
	 *
	 * `--sift-accent-text` and not `--sift-accent`: the fill is solved for a label sitting on it
	 * and measured only against the 3:1 graphics floor (`contrast.test.ts`), while the text role is
	 * solved for 4.5:1 on every surface a word can land on, and a link is a word. `app.css` gives
	 * every bare anchor that colour; these declarations stay because this one takes over the click
	 * and dresses itself.
	 */
	.setting-link {
		color: var(--sift-accent-text);
		text-decoration: none;
		text-underline-offset: 2px;
		border-radius: var(--radius-sm);
		/* The Light register, on the resting rule where a transition belongs. Colour
		   only: the underline is not transitioned, because a rule fading in under a word reads as
		   the text moving rather than as the link answering. */
		transition: color var(--dur-instant) var(--ease);
	}

	/* The underline alone. A colour STEP would reach for `--sift-accent-hover`, which is the
	   fill's hover and is not held to the text floor, so the one state where somebody is definitely
	   reading the word would be the one drawn at the looser measure. */
	.setting-link:hover,
	.setting-link:focus-visible {
		text-decoration: underline;
	}

	/* A finger's reach on a phone, the words keeping their line: the ring the link tone of a button
	   draws (`Button`), for a link standing at the end of a card as much as one in a sentence. */
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
