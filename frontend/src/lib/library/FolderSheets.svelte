<script lang="ts">
	/* The two sheets a folder's menu opens: Share, which decides who may see it, and Visibility,
	 * which reports who can and through what. */
	import ShareDialog from '$lib/components/ShareDialog.svelte';
	import VisibilityDialog from '$lib/components/VisibilityDialog.svelte';
	import type { ShareTarget } from '$lib/library/sharing';

	interface FolderNamed {
		id: string;
		name: string;
	}

	interface Props {
		/** After a share is written: a screen with a selection lets it go. */
		onapplied?: () => void;
	}

	let { onapplied }: Props = $props();

	let sharing = $state<ShareTarget[]>([]);
	let shareOpen = $state(false);
	let reaching = $state<ShareTarget | null>(null);
	let reachOpen = $state(false);

	function target(folder: FolderNamed): ShareTarget {
		return { type: 'folder', id: folder.id, label: folder.name };
	}

	export function share(folders: readonly FolderNamed[]): void {
		if (folders.length === 0) return;
		sharing = folders.map(target);
		shareOpen = true;
	}

	export function visibility(folder: FolderNamed): void {
		reaching = target(folder);
		reachOpen = true;
	}
</script>

<ShareDialog bind:open={shareOpen} targets={sharing} {onapplied} />
<VisibilityDialog bind:open={reachOpen} target={reaching} />
