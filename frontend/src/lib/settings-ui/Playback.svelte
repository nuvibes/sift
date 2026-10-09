<script lang="ts">
	/* Playback: what happens when you open a video, and what this device will do to make it
	 * play, and, as a group of its own, what a Theater wall starts as. */
	import { onMount } from 'svelte';
	import { LabelledRow, Problem, Select, Skeleton, Switch } from '$lib/components/common';
	import { Destinations } from '$lib/library/destinations.svelte';
	import { session } from '$lib/shell/session.svelte';
	import {
		popoutLeavesToMini,
		recallInterfaceState,
		rememberPopoutLeavesToMini
	} from '$lib/shell/interface-state.svelte';
	import SettingGroup from './SettingGroup.svelte';
	import LayoutGlyph from '$lib/components/theater/LayoutGlyph.svelte';
	import { layout } from '$lib/theater/layouts';
	import SettingsList, { type SettingBlock } from './SettingsList.svelte';
	import { SettingsPanel } from './panel.svelte';
	import { COPY } from './Playback.search';
	import { bridge } from '$lib/bridge';
	import { screenOffer } from '$lib/remote/offer.svelte';
	import { explainAbsentRows, hiddenBy } from '$lib/settings-ui/settings-anchor.svelte';

	const panel = new SettingsPanel();

	const BLOCKS: SettingBlock[] = [
		/* The pair, together: one says whether a place is kept at all and the other says for
		   which videos, and reading either alone leaves the question the other answers. */
		{
			keys: ['playback.resume_enabled', 'playback.resume_minimum_seconds'],
			dependsOn: { 'playback.resume_minimum_seconds': 'playback.resume_enabled' }
		},
		{ keys: ['playback.dwell_pictures', 'playback.max_transcode_height'] },
		/* What the converting costs in disk, beside what it costs in quality. */
		{ keys: ['playback.cache_max_gb'] }
	];

	/* What a screenshot does, taken from the player's drawer: copied, or saved into the library. */
	const SCREENSHOTS: SettingBlock[] = [
		{ heading: COPY.screenshots, keys: ['playback.screenshot'] }
	];
	const SCREENSHOT_FOLDER = 'playback.screenshot_folder';
	const SCREENSHOT = 'playback.screenshot';
	const destinations = new Destinations();
	destinations.follow();

	/* Filed under Playback and never drawn here: the player is their control (see the head). */
	const ON_THE_PLAYER = ['playback.volume', 'playback.muted', 'playback.loop_mode'];

	/* What a search or a link that names a row this pane is not drawing is told, and shown. */
	$effect(() =>
		explainAbsentRows((key) => {
			if (panel.loading || panel.failed) return null;
			if (ON_THE_PLAYER.includes(key)) {
				const label = panel.entry(key)?.label;
				return label ? { because: COPY.onThePlayer(label) } : null;
			}
			if (key !== SCREENSHOT_FOLDER) return null;
			if (!session.isAdmin) return { because: COPY.folderIsAdmins, near: SCREENSHOT };
			const choice = panel.value(SCREENSHOT);
			if (choice === 'save') return null;
			const because = hiddenBy(panel.entry(key), panel.entry(SCREENSHOT), choice);
			return because ? { because, near: SCREENSHOT } : null;
		})
	);

	/* Theater's rows, as one group under its own heading, LAST: one video first, then the popout
	   it plays in, then the phone that can drive either, then the wall of several. */
	const THEATER: SettingBlock[] = [
		{
			heading: COPY.theater,
			keys: [
				'theater.layout',
				'theater.center_stage',
				'theater.autoplay',
				'theater.timer_seconds',
				'theater.resume'
			],
			/* The default layout is the Layouts chooser on the wall, so it draws each shape as
			   the wall's own chooser does, from the same glyph. */
			pictures: { 'theater.layout': layoutPicture },
			tooltips: { 'theater.layout': (value) => layout(value).tooltip }
		}
	];

	/* This account's own answer rather than a registry setting, which is why it is read here and
	   not through `panel`: it lives in the interface document beside the popout's other
	   preference. */
	let toMini = $state(true);

	/* Whether this page is Sift's own app, which offers what it plays to the phone whatever the
	   switch says (`screenOffer`). */
	const inTheApp = bridge.isDesktop();

	onMount(() => {
		void panel.load();
		if (session.isAdmin) void destinations.load();
		void recallInterfaceState().then(() => (toMini = popoutLeavesToMini()));
	});
</script>

<!-- One layout, drawn as its own shape: the same picture the wall's Layouts chooser draws. -->
{#snippet layoutPicture(option: { value: string; label: string })}
	<LayoutGlyph shape={layout(option.value)} />
{/snippet}

{#if panel.loading}
	<Skeleton lines={3} />
{:else if panel.failed}
	<Problem message={COPY.cannotLoad} />
{:else}
	<SettingsList {panel} blocks={BLOCKS} />

	<SettingsList {panel} blocks={SCREENSHOTS} />
	<!-- Where a saved screenshot lands: a folder of the library, the default downloads folder unless
	     one is chosen. Only while Save is the answer, and only for an admin: adding a file to the
	     library is an admin's act, and for anybody else a saved screenshot is a download. -->
	{@const folderRow = panel.entry(SCREENSHOT_FOLDER)}
	{#if panel.value(SCREENSHOT) === 'save' && session.isAdmin && folderRow}
		<SettingGroup>
			<LabelledRow
				id={SCREENSHOT_FOLDER}
				label={folderRow.label}
				help={folderRow.help}
				helpId="playback-screenshot-folder-help"
			>
				<Select
					label={folderRow.label}
					describedBy="playback-screenshot-folder-help"
					value={String(panel.value(SCREENSHOT_FOLDER) ?? '')}
					options={destinations.options}
					onValueChange={(next: string) => void panel.save(SCREENSHOT_FOLDER, next)}
				/>
			</LabelledRow>
		</SettingGroup>
	{/if}

	<!-- The popout, which is where a video is actually watched. -->
	<SettingGroup heading={COPY.popout}>
		<LabelledRow
			id="playback.popout.leave_to_mini"
			label={COPY.leaveToMini.name}
			help={COPY.leaveToMini.help}
			helpId="playback-leave-to-mini-help"
		>
			<Switch
				label={COPY.leaveToMini.name}
				describedBy="playback-leave-to-mini-help"
				checked={toMini}
				onCheckedChange={(on) => {
					rememberPopoutLeavesToMini(on);
					toMini = on;
				}}
			/>
		</LabelledRow>
	</SettingGroup>

	<!-- The phone as a remote: whether a player or wall open in THIS browser offers itself to the
	     Remote tab. Sift's own app always does, so there the switch stands on and still, saying so. -->
	<SettingGroup heading={COPY.remote}>
		<LabelledRow
			id="playback.remote.this_browser"
			label={COPY.thisBrowser.name}
			help={inTheApp ? COPY.thisBrowser.helpApp : COPY.thisBrowser.help}
			helpId="playback-remote-this-browser-help"
		>
			<Switch
				label={COPY.thisBrowser.name}
				describedBy="playback-remote-this-browser-help"
				checked={inTheApp || screenOffer.thisBrowser}
				disabled={inTheApp}
				onCheckedChange={(on) => screenOffer.setThisBrowser(on)}
			/>
		</LabelledRow>
	</SettingGroup>

	<SettingsList {panel} blocks={THEATER} />
{/if}
