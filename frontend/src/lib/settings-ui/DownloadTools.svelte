<script lang="ts">
	/* Settings > Updates: which programs do the fetching, and which version of each. */
	import { onMount } from 'svelte';
	import { jobChanges, whenChanged } from '$lib/library/changes.svelte';
	import { api } from '$lib/api/client';
	import type { components } from '$lib/api/schema';
	import ActionRow from './ActionRow.svelte';
	import FactRow from './FactRow.svelte';
	import SettingGroup from './SettingGroup.svelte';
	import { COPY } from './DownloadTools.search';

	type Tool = components['schemas']['DownloadTool'];
	type Release = components['schemas']['DownloadToolRelease'];

	/* What each one is for, in a line. Keyed by the server's key so a tool the server adds later
	 * still draws (with no sentence) rather than not at all. */
	const PURPOSE: Record<string, string> = COPY.purpose;

	let tools = $state<Tool[] | null>(null);
	let unreadable = $state(false);
	let checking = $state(false);
	let release = $state<Release | null>(null);
	let checkFailed = $state(false);

	async function readTools(): Promise<void> {
		try {
			tools = (await api.get<components['schemas']['DownloadTools']>('/download-tools')).tools;
			unreadable = false;
		} catch {
			unreadable = true;
		}
	}

	onMount(() => void readTools());
	/* A tool is fetched or updated by a job, and a job that ends rings the jobs bell: the
	   versions on this pane follow it rather than waiting for the pane to be opened again. */
	whenChanged(jobChanges, () => void readTools());

	function label(tool: Tool): string {
		return tool.key === 'js-runtime' ? COPY.engine : tool.name;
	}

	function help(tool: Tool): string {
		const origin = tool.shipped
			? COPY.shipped
			: tool.version === null
				? COPY.missing
				: COPY.devicesOwn;
		return [PURPOSE[tool.key], origin].filter(Boolean).join(' ');
	}

	function fact(tool: Tool): string {
		if (tool.key === 'js-runtime') {
			if (tool.name === 'none') return COPY.noneFound;
			if (tool.name === 'unknown') return COPY.notResponding;
			return tool.version ? `${tool.name} ${tool.version}` : tool.name;
		}
		return tool.version ?? COPY.notResponding;
	}

	async function check(): Promise<void> {
		checking = true;
		checkFailed = false;
		try {
			release = await api.post<Release>('/download-tools/latest', { body: { tool: 'yt-dlp' } });
		} catch {
			release = null;
			checkFailed = true;
		} finally {
			checking = false;
		}
	}

	/* What pressing the button found, as one sentence. Null until it has been pressed. */
	const found = $derived.by((): string | null => {
		if (checkFailed) return COPY.cannotCheck;
		if (release === null) return null;
		if (release.latest === null) return COPY.unreachable;
		if (release.newer) return COPY.newer(release.latest);
		return COPY.newest(release.latest);
	});
</script>

<SettingGroup id="updates.download_tools" heading={COPY.name} help={COPY.help}>
	{#if unreadable}
		<FactRow label={COPY.name} fact={COPY.cannotAsk} />
	{:else if tools === null}
		<FactRow label={COPY.name} fact={COPY.checking} />
	{:else}
		{#each tools as tool (tool.key)}
			<FactRow label={label(tool)} help={help(tool)} fact={fact(tool)} />
			{#if tool.key === 'yt-dlp'}
				<!-- Its own row under yt-dlp's, so the version stays in the column every tool's
				     version is in, and the press and what it found are one row and its foot line
				     rather than two things stacked in the control column. -->
				<ActionRow
					id="updates.download_tools.yt-dlp"
					label={COPY.checkLabel}
					help={COPY.checkHelp}
					action={COPY.check}
					actionLabel={COPY.checkLabel}
					icon="search"
					busy={checking}
					onclick={check}
				>
					{#if found}<span role="status">{found}</span>{/if}
				</ActionRow>
			{/if}
		{/each}
	{/if}
</SettingGroup>
