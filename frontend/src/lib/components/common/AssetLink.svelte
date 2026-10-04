<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'AssetLink',
		category: 'primitive',
		role: 'a link to a file that opens it in place instead of tearing down the screen behind it',
		basis: 'site:<a>',
		states: ['default', 'hover']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: it is an anchor. There is nothing to behave: the browser already knows how
	   to be a link, and the one thing added here is that a plain left-click opens the PANEL where it
	   stands instead of navigating, which is `$lib/player/asset-view`'s rule and not a component's. The
	   same arrangement, for the same reason, as `SettingLink` beside it. */

	/*
	 * A link to one FILE, from anywhere in the application.
	 *
	 * ## Why not a bare anchor
	 *
	 * A bare `<a href="/asset/...">` (the source a clip was cut from and the copies made out of it,
	 * a job's subject, a row on the maintenance pane, a download, a match on the tagger) runs the
	 * `/asset/[id]` route, which is the ONE way of reaching that address that has nothing behind
	 * it. The route says so in its own opening comment. The screen underneath would be torn down
	 * and rebuilt, and closing the panel afterwards would go to the library rather than back to the
	 * wall, the job list or the download it came from.
	 *
	 * ## Why it is a component and not four lines at each call site
	 *
	 * Because four lines at each call site are the lines somebody forgets to write, and every place
	 * that draws the link needs them.
	 */
	import type { Snippet } from 'svelte';
	import { openAssetInstead } from '$lib/player/asset-view';

	interface Props {
		/** The file. */
		id: string;
		/** The words. A filename, a title: what it is, never "open". */
		children: Snippet;
		/** A class from the caller, for a link that has to sit in that screen's own type. */
		extra?: string;
	}

	let { id, children, extra = '' }: Props = $props();
</script>

<a class="asset-link {extra}" href="/asset/{id}" onclick={(event) => openAssetInstead(event, id)}>
	{@render children()}
</a>

<style>
	/*
	 * The accent, with the underline held back until hover or focus-visible, the same rule
	 * `SettingLink` gives. A permanent rule under every filename stripes the prose it is embedded
	 * in; the underline arrives on hover, and the keyboard gets it with the focus ring, so nobody
	 * reaching for the link is left relying on colour alone.
	 *
	 * `--sift-accent-text` and not `--sift-accent`: the palette keeps two roles. The fill is solved
	 * for a label sitting on it and measured only against the 3:1 graphics floor
	 * (`contrast.test.ts`); the text role is solved for 4.5:1 on every surface a word can land on,
	 * and a link is a word. `app.css` gives every bare anchor that colour; these declarations stay
	 * because this one takes over the click and dresses itself.
	 */
	.asset-link {
		color: var(--sift-accent-text);
		text-decoration: none;
		text-underline-offset: 2px;
		border-radius: var(--radius-sm);
		/* The Light register, on the resting rule where a transition belongs. Colour
		   only: a rule fading in under a word reads as the text moving, not as the link answering. */
		transition: color var(--dur-instant) var(--ease);
	}

	/* The underline alone, and no colour step: `--sift-accent-hover` is the fill's hover and is not
	   held to the text floor, so the one state where somebody is definitely reading the word would
	   be the one drawn at the looser measure. */
	.asset-link:hover,
	.asset-link:focus-visible {
		text-decoration: underline;
	}
</style>
