<script lang="ts" module>
	/*
	 * A file's place as the server said it, with the profile folder's name drawn covered.
	 *
	 * The server takes the name out before a path leaves it (when the person asked for that) and
	 * puts `HIDDEN_NAME` in its place as one whole segment. So there is nothing on the page to
	 * cover: what is drawn is a stand-in word under `Covered`, the filled box the hidden part of a
	 * machine's address is drawn with (Privacy > Network sharing), which reads as "a name is here
	 * and it is hidden" without the name ever being in the page. The address's drawing and not a
	 * blur: one look for a hidden part, wherever it is.
	 */

	/** The segment the server writes where the profile folder's name was. The same string as
	 *  `REDACTED` in `src/sift/kernel/where.py`; a test reads that file to hold the two together. */
	export const HIDDEN_NAME = '[redacted]';

	/** One run of a path: drawn as it is, or the hidden segment. */
	export interface PathPiece {
		text: string;
		hidden: boolean;
	}

	const SEPARATOR = /[\\/]/;

	/**
	 * The path cut into what is drawn as it is and the hidden segments.
	 *
	 * Only a WHOLE segment counts (between separators or the ends), because that is the only shape
	 * the server writes, and a folder whose name merely contains the marker is somebody's folder.
	 */
	export function pathPieces(path: string): PathPiece[] {
		const pieces: PathPiece[] = [];
		let plain = '';
		let at = 0;
		while (at < path.length) {
			const found = path.indexOf(HIDDEN_NAME, at);
			if (found === -1) break;
			const end = found + HIDDEN_NAME.length;
			const before = found === 0 || SEPARATOR.test(path[found - 1]);
			const after = end === path.length || SEPARATOR.test(path[end]);
			if (before && after) {
				plain += path.slice(at, found);
				if (plain) pieces.push({ text: plain, hidden: false });
				plain = '';
				pieces.push({ text: HIDDEN_NAME, hidden: true });
			} else {
				plain += path.slice(at, end);
			}
			at = end;
		}
		plain += path.slice(at);
		if (plain) pieces.push({ text: plain, hidden: false });
		return pieces;
	}
</script>

<script lang="ts">
	import Covered from '$lib/components/common/Covered.svelte';

	interface Props {
		/** The path exactly as the server said it. */
		path: string;
	}

	let { path }: Props = $props();

	const pieces = $derived(pathPieces(path));
</script>

<!-- No whitespace between the tags on purpose: any would land inside the path, as text. The
     marker itself stays in the text (invisible, over the stand-in), so a path selected and copied
     by hand reads the same as the one the copy button gives, and a screen reader hears it. -->
{#each pieces as piece, index (index)}{#if piece.hidden}<span class="hidden-name"
			><span class="stand-in" aria-hidden="true"><Covered>profile</Covered></span><span class="said"
				>{piece.text}</span
			></span
		>{:else}{piece.text}{/if}{/each}

<style>
	.hidden-name {
		position: relative;
		display: inline-block;
	}

	/* The cover is `Covered`'s own, so a change to how a hidden part looks reaches this too. */
	.stand-in {
		user-select: none;
	}

	.said {
		position: absolute;
		inset: 0;
		overflow: hidden;
		white-space: nowrap;
		opacity: 0;
	}
</style>
