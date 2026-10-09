<script lang="ts" module>
	/* A path with the profile folder's name drawn as `Covered`: the server never sends the name. */

	/** `REDACTED` in `src/sift/kernel/where.py`; a test holds the two together. */
	export const HIDDEN_NAME = '[redacted]';

	export interface PathPiece {
		text: string;
		hidden: boolean;
	}

	const SEPARATOR = /[\\/]/;

	/** Only a WHOLE segment counts, the only shape the server writes. */
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
		path: string;
	}

	let { path }: Props = $props();

	const pieces = $derived(pathPieces(path));
</script>

<!-- No whitespace between tags; the marker stays in the text, so a copy reads the same. -->
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
