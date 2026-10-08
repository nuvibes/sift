<script lang="ts">
	import { onMount, untrack } from 'svelte';

	import '$lib/generated/fonts.css';
	import '../app.css';

	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { capture } from '$lib/capture/capture.svelte';
	import { Button, Scroller, Toaster } from '$lib/components/common';
	import AssetModal from '$lib/components/AssetModal.svelte';
	import MiniPlayer from '$lib/components/player/MiniPlayer.svelte';
	import SettingsModal from '$lib/components/SettingsModal.svelte';
	import DropOverlay from '$lib/components/shell/DropOverlay.svelte';
	import { loadMotionPreference } from '$lib/shell/motion.svelte';
	import { keyboard } from '$lib/shell/keyboard.svelte';
	import MobileTabs from '$lib/components/shell/MobileTabs.svelte';
	import Rail from '$lib/components/shell/Rail.svelte';
	import TopBar from '$lib/components/shell/TopBar.svelte';
	import WindowBar from '$lib/components/shell/WindowBar.svelte';
	import FilterBar from '$lib/components/shell/FilterBar.svelte';
	import StaleBuildBanner from '$lib/components/shell/StaleBuildBanner.svelte';
	import UpdateBanner from '$lib/components/shell/UpdateBanner.svelte';
	import UnlockBanner from '$lib/components/shell/UnlockBanner.svelte';
	import LockTriggers from '$lib/components/vault/LockTriggers.svelte';
	import PinPrompt from '$lib/components/vault/PinPrompt.svelte';
	import SearchOverlay from '$lib/components/shell/SearchOverlay.svelte';
	import SwapDrawer from '$lib/swap/SwapDrawer.svelte';
	import SwapModeKeeper from '$lib/swap/SwapModeKeeper.svelte';
	import { rail } from '$lib/components/shell/rail-state.svelte';
	import { drawnWhileFilled, stage } from '$lib/components/shell/stage.svelte';
	import { matches } from '$lib/shell/shortcuts';
	import Icon from '$lib/components/Icon.svelte';
	import { api } from '$lib/api/client';
	import { bridge } from '$lib/bridge';
	import { session } from '$lib/shell/session.svelte';
	import { theme } from '$lib/theme/theme.svelte';
	import { vault, vaultPrompt } from '$lib/shell/vault.svelte';
	import { noteAddress } from '$lib/shell/navigation.svelte';
	import { noteWhere, watchVisits } from '$lib/shell/visits';
	import { pageScroll } from '$lib/components/shell/page-scroll';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { imports } from '$lib/library/imports.svelte';
	import {
		downloadChanges,
		jobChanges,
		settingChanges,
		whenChanged
	} from '$lib/library/changes.svelte';
	import { build } from '$lib/shell/build.svelte';
	import { dressTitleBar, markOverlaidWindow } from '$lib/shell/titlebar.svelte';
	import { live } from '$lib/shell/live.svelte';
	import {
		forgetAccountScopedPreferences,
		loadAccountScopedPreferences
	} from '$lib/shell/account-scoped';
	import { closeWhatTheVaultNowHides, forgetVaultScopedCaches } from '$lib/shell/vault-scoped';
	import { lockOnLaunch } from '$lib/shell/vault-preferences';
	import { lockSift } from '$lib/shell/lock-sift';
	import { leaveAssetPanel } from '$lib/player/asset-view';
	import { tellShellLogDetail } from '$lib/desktop/shell-log-detail';

	let { children } = $props();

	/* Closing a panel: back, or to the library when landed on cold (`leaveAssetPanel`). */
	const leave = leaveAssetPanel;

	// Paste a link or an image anywhere and it is added, except into a field, where the text is meant.
	function onPaste(event: ClipboardEvent) {
		const target = event.target as HTMLElement | null;
		if (
			target?.isContentEditable ||
			['INPUT', 'TEXTAREA', 'SELECT'].includes(target?.tagName ?? '')
		)
			return;
		if (!session.isAdmin) return; // a stray paste from someone who cannot add is silently ignored
		const data = event.clipboardData;
		if (!data) return;
		const hasUrl = (data.getData('text/uri-list') || data.getData('text/plain')).trim();
		if (!hasUrl && data.files.length === 0) return; // nothing to add; leave the paste be
		event.preventDefault();
		void capture.handlePaste(data);
	}

	// Ask the server who this is before drawing a shell that depends on the answer, so a guest never
	// sees an admin's navigation for a frame.
	const ready = $derived(session.viewer !== undefined);

	// The screens somebody not inside the app may see, drawn without the shell, whose every control
	// would answer 401 (or 423 on the lock screen, a real session the server is refusing).
	const AUTH_ROUTES = ['/login', '/setup', '/locked'];
	const onAuthScreen = $derived(AUTH_ROUTES.includes(page.url.pathname));

	/* Screens the DESKTOP SHELL draws before there is a server at all: real routes, so they use the
	 * same components and tokens rather than a second design system inside the shell. */
	const SHELL_ROUTES = ['/connect', '/start', '/library-location', '/opening'];
	const onShellScreen = $derived(SHELL_ROUTES.includes(page.url.pathname));

	// Not a 401, which the session handles: the server down or broken. Without this nothing renders.
	let unreachable = $state(false);

	$effect(() => {
		/* Not on a shell screen, which exists to choose which Sift to ask. */
		if (onShellScreen) return;
		session.load().catch(() => (unreachable = true));
	});

	/*
	 * THE LAUNCH LOCK LANDS BEFORE THE SCREEN IS DRAWN. A screen asks for its list as it mounts, and
	 * one that asked before "Hide whenever Sift starts" had shut Hidden would be answered with the
	 * hidden files in it. So the framed shell waits for `lockOnLaunch`, once per page load; it
	 * settles whatever the lock answered, so a server that will not answer cannot hold the window.
	 */
	let launched = $state(false);
	let launching = false;

	/* Whether the framed screen has drawn and sent its own reads: the shell's background reads (the
	 * queue's glance, the log detail) wait for it, so the screen's read is not queued behind them. */
	let screenAsked = $state(false);
	$effect(() => {
		if (launching || !ready || !session.isSignedIn) return;
		/* A locked session shut Hidden with it and refuses every vault request. */
		if (session.viewer?.locked === true) {
			vault.sessionLocked();
			return;
		}
		launching = true;
		void lockOnLaunch(() => vault.lock())
			.catch(() => undefined)
			.finally(() => (launched = true));
	});

	/*
	 * The look, from this browser's own memory, before anything is asked of anybody: `adopt` covers
	 * what the page's boot script could not, and needs no server, so the connect screen is coloured.
	 */
	$effect(() => {
		untrack(() => theme.adopt());
	});

	/*
	 * Everything held on behalf of whoever is signed in, read when that becomes known and again when
	 * it changes. Keyed on the account, not the page, because the desktop window never reloads, so a
	 * second account would otherwise keep the first one's preferences. Read the deciding value
	 * OUTSIDE `untrack`, do the work inside.
	 */
	let readFor: string | null | undefined = undefined;

	$effect(() => {
		const who = session.viewer?.id ?? null;
		/* `undefined` is "not answered yet", not "nobody", so nothing is loaded for it. */
		const known = session.viewer !== undefined;
		/* A locked session refuses every read of the account's; the PIN reloads the page. */
		const locked = session.viewer?.locked === true;
		untrack(() => {
			if (onShellScreen || !known || locked || who === readFor) return;
			/* Somebody else's answers, never on the first pass, where clearing the theme's mirror would
			   bring back the flash of the default. */
			if (readFor !== undefined) forgetAccountScopedPreferences();
			readFor = who;
			if (who === null) return;

			/* Every preference this account is drawn by, loaded here rather than by each screen, which
			   could forget; `account-scoped.ts` holds the one list. */
			loadAccountScopedPreferences();
		});
	});

	/* Where we are, noted so the next screen knows whether stepping back lands on the wall. An effect,
	 * not `afterNavigate`, which here stops SvelteKit intercepting links at all
	 * (`navigation.svelte.ts`). */
	$effect(() => {
		noteAddress(page.url);
	});

	/* The page in front, for Insights' visits and for every request; a file open over it pauses the
	   count (`visits.ts`). */
	$effect(() => {
		noteWhere({
			route: page.route.id,
			params: page.params,
			search: page.url.searchParams,
			settings: page.state.settings ?? null,
			covered: Boolean(page.state.asset)
		});
	});
	onMount(watchVisits);

	/*
	 * Where each screen was scrolled to, kept per history entry: SvelteKit reads it off the root
	 * layout, which is on every route; `PageFrame` says which box scrolls (`page-scroll.ts`).
	 */
	export const snapshot = pageScroll;

	/* Before anything animates; per machine, so it needs no request (`motion.svelte.ts`). */
	onMount(loadMotionPreference);
	/* The on-screen keyboard: the tab bar stands down under it (`keyboard.svelte.ts`). */
	onMount(() => keyboard.watch());

	/* Ask who this is again whenever the window comes back to the front: an account blocked or
	 * expired meanwhile is told so. On focus, not a timer, which would poll every hidden tab. */
	onMount(() => {
		const recheck = () => {
			if (document.visibilityState !== 'visible') return;
			session.load().catch(() => {});
		};
		window.addEventListener('focus', recheck);
		document.addEventListener('visibilitychange', recheck);
		return () => {
			window.removeEventListener('focus', recheck);
			document.removeEventListener('visibilitychange', recheck);
		};
	});

	/*
	 * The box that fills the window, and the key that sends its bar away. Handed over, so the stage
	 * can compare it with `document.fullscreenElement`, since something else may be fullscreen.
	 */
	let stageBox = $state<HTMLElement | null>(null);

	$effect(() => {
		stage.register(stageBox);
	});

	onMount(() => {
		const unwatch = stage.watch();
		const barKey = (event: KeyboardEvent) => {
			// Only while the bar fills the window; the key itself is the registry's answer.
			if (!stage.filling) return;
			if (!matches(event, 'stage.toggleBar')) return;
			event.preventDefault();
			stage.toggleBar();
		};
		window.addEventListener('keydown', barKey);
		return () => {
			unwatch();
			window.removeEventListener('keydown', barKey);
		};
	});

	/*
	 * The two shortcuts that have to work while somebody is not looking at the app: with Ctrl, so
	 * typing cannot fire them. Ctrl+L shadows the browser's address bar, the lesser harm.
	 */
	function onShortcut(event: KeyboardEvent) {
		/* Ctrl+H hides: the vault shuts, nothing else changes. Ctrl+L locks Sift: the session shuts
		 * and takes Hidden with it. Both are everybody's, since a guest has a Hidden too. */
		if (matches(event, 'app.shutHidden')) {
			event.preventDefault();
			void lockNow();
			return;
		}
		if (matches(event, 'app.lock')) {
			event.preventDefault();
			void lockSift();
		}
	}

	/* Lock, and say so: with nothing showing, a silent lock looks like a swallowed key. */
	async function lockNow() {
		const wasOpen = vault.unlocked;
		await vault.lock();
		toasts.show(wasOpen ? 'Hidden items are hidden again' : 'Hidden items are hidden', {
			tone: 'success'
		});
	}

	/* The vault opened or shut, so every scoped cache is wrong: emptied here, since the screens
	 * holding concealed rows are rarely the one in front; their own effects refetch. */
	$effect(() => {
		void vault.generation;
		untrack(() => {
			forgetVaultScopedCaches();
			// The one question rather than a sweep: whether the mini player's file is now concealed.
			void closeWhatTheVaultNowHides();
		});
	});

	$effect(() => {
		// Signed out on a screen that needs an account: to sign in.
		if (onShellScreen) return;
		if (ready && !session.isSignedIn && !onAuthScreen) {
			void goto('/login', { replaceState: true });
		}
	});

	/* Locked, acted on before anything renders: `me` answers a locked session, and drawing the
	 * library until another request was refused would be a lock read around. The drawing condition
	 * asks the same question, so no frame has both. */
	const shut = $derived(ready && session.isSignedIn && session.viewer?.locked === true);

	$effect(() => {
		if (shut && !onAuthScreen) void goto('/locked', { replaceState: true });
	});

	/* What the work queue is doing, for the busy indicator: it outlives any screen. An admin's only,
	 * since the queue refuses anybody else. */
	$effect(() => {
		// Keyed on who is signed in: a remembered refusal ends when somebody else signs in.
		const account = session.viewer?.id;
		if (!session.adminUnlocked || account === undefined || !screenAsked) return;
		imports.forgetRefusal();
		void imports.refresh();
		void imports.readDownloads();
		return () => imports.stop();
	});

	/* The desktop shell's log follows the Detail setting, told once an admin is signed in
	 * (`shell-log-detail.ts`). */
	$effect(() => {
		const account = session.viewer?.id;
		if (!session.adminUnlocked || account === undefined || !screenAsked) return;
		void tellShellLogDetail();
	});

	/* Re-read whenever the connection says the queue moved, never on a timer. Waiting for cookies is
	 * the download's job being parked, so the Downloads row's facts come too. */
	whenChanged(jobChanges, () => {
		if (!session.adminUnlocked) return;
		void imports.refresh();
		void imports.readDownloads();
		// While the keys are locked, whether they still are: another window may have unlocked them.
		if (session.secretsLocked) void session.recheck();
	});

	/* The Downloads row's facts, on a download moving and on a setting (pausing is one). */
	whenChanged(downloadChanges, () => {
		if (session.adminUnlocked) void imports.readDownloads();
	});
	whenChanged(settingChanges, () => {
		if (session.adminUnlocked) void imports.readDownloads();
	});

	/* The one connection the whole application holds: no rows, only which kind of thing moved.
	 * Everybody has one (a guest whose share is withdrawn most needs telling); the server drops
	 * admin-only subjects. Keyed on who is signed in and held while it is locked. */
	$effect(() => {
		if (!session.isSignedIn || session.viewer?.locked === true) return;
		live.start();
		return () => live.stop();
	});

	/*
	 * The window's own caption buttons, in the colours this page is drawing itself in: reading the
	 * theme's choices makes it re-run when they change. `markOverlaidWindow` first.
	 */
	$effect(() => {
		void theme.base;
		void theme.accent;
		markOverlaidWindow();
		void dressTitleBar();
	});

	/*
	 * Whether this window is still running the client the server would serve: asked when the live
	 * connection COMES UP, since a restart for an upgrade drops the socket. Never on a timer.
	 */
	$effect(() => {
		if (!session.isSignedIn || !live.live) return;
		void build.check();
	});

	/* Whether this session's saved keys are locked, asked at the same moment: a restart seals them
	 * while the window believes otherwise (`session.recheck`). Not on the page's first connection,
	 * which comes a moment after the page asked who this is. */
	let firstConnection = true;
	$effect(() => {
		if (!session.isSignedIn || !live.live) return;
		if (firstConnection) {
			firstConnection = false;
			return;
		}
		untrack(() => void session.recheck());
	});

	/* The rail as this account arranged it, keyed on the account, so a new sign-in takes up its own
	 * arrangement; the shell draws nothing before it knows who is signed in. */
	$effect(() => {
		const account = session.viewer?.id;
		if (account === undefined) {
			rail.release();
			return;
		}
		if (session.viewer?.locked === true) return;
		void rail.hydrate(account);
	});

	/*
	 * Screens that lay themselves out, from the top of the window to the bottom, decided by the
	 * route. Each draws a `PageFrame` (directly or through `AssetGrid`/`EntityGrid`), which owns the
	 * padding and the one scroll, so `main` adds neither. A `:global(main:has(...))` rule from a
	 * screen would outlive its page. `src/lib/design/frame.test.ts` holds this list to the frames.
	 */
	const FULL_BLEED_ROUTES = new Set([
		'/browse',
		'/collections',
		'/collections/[id]',
		'/collections/new',
		'/design',
		'/design/bar',
		'/design/charts',
		'/design/saved-filters',
		'/design/insights',
		'/downloads',
		'/favorites',
		'/hidden',
		'/insights',
		'/insights/stats',
		'/insights/recaps',
		'/insights/recaps/[id]',
		/* The phone's list of every kind of thing, and its list of Settings and the account. */
		'/library',
		'/more',
		'/swap',
		'/loops',
		'/organize',
		'/organize/[queue]',
		'/organize/[queue]/[id]',
		/* A may-be group's review draws its frame one component down, in `MayBeReview`. */
		'/organize/may-be/[person]/[pile]',
		'/people',
		'/people/new',
		'/people/[id]',
		'/photo-sets',
		'/photo-sets/new',
		'/photo-sets/[id]',
		'/sites',
		'/sites/new',
		'/sites/[id]',
		'/songs',
		'/songs/new',
		'/songs/[id]',
		'/recent',
		/* The phone's remote for what plays at the desk: a framed screen like Library and More. */
		'/remote',
		'/tags',
		'/tags/new',
		'/tags/[id]',
		/* Not framed: a flex column that fills the box, which needs the definite height this gives. */
		'/theater'
	]);
	const fullBleed = $derived(FULL_BLEED_ROUTES.has(page.route.id ?? ''));

	/*
	 * Whether the application's own frame (the rail and the top bar) is what is on screen: it picks
	 * the framed branch and whether the window needs its own drag strip, which must never disagree.
	 */
	const framed = $derived(
		!onShellScreen &&
			!unreachable &&
			!(ready && onAuthScreen) &&
			ready &&
			session.isSignedIn &&
			!shut &&
			launched
	);

	/*
	 * The desktop window's opening frame (`routes/opening`) goes once this page has drawn a screen of
	 * its own, a frame after it is on the page; the sign-in form, a question or a message is ready to
	 * use as drawn, and a framed screen once its first picture has loaded.
	 */
	const drawnScreen = $derived(onShellScreen || unreachable || (ready && onAuthScreen) || framed);
	let toldDrawn = false;
	$effect(() => {
		if (framed && !screenAsked) whenOnPage(null, () => (screenAsked = true));
	});
	$effect(() => {
		if (!drawnScreen || toldDrawn || page.url.pathname === '/opening') return;
		toldDrawn = true;
		const usable = !framed;
		/* The sign-in card waits on a question before it draws: the frame stays until it has. */
		whenOnPage(onAuthScreen ? 'form' : null, () => {
			bridge.tellDrawn('painted');
			if (usable) bridge.tellDrawn('usable');
		});
	});

	/* `then`, a frame after `selector` is on the page (two seconds at most), or after the next frame. */
	function whenOnPage(selector: string | null, then: () => void, frames = 120) {
		requestAnimationFrame(() => {
			if (selector !== null && frames > 0 && document.querySelector(selector) === null) {
				whenOnPage(selector, then, frames - 1);
				return;
			}
			requestAnimationFrame(then);
		});
	}
	onMount(() => {
		const firstPicture = (event: Event) => {
			if (!framed || !(event.target instanceof HTMLImageElement)) return;
			if (event.target.closest('main') === null) return;
			document.removeEventListener('load', firstPicture, true);
			requestAnimationFrame(() => bridge.tellDrawn('usable'));
		};
		document.addEventListener('load', firstPicture, true);
		return () => document.removeEventListener('load', firstPicture, true);
	});
	/* The look handed again when the theme changes, so the next start's frame matches the page. */
	$effect(() => {
		void theme.choice;
		if (!toldDrawn) return;
		requestAnimationFrame(() => bridge.tellDrawn('painted'));
	});
</script>

<svelte:window onpaste={onPaste} onkeydown={onShortcut} />

<svelte:head>
	<!-- The tab title is fixed: it shows in the tab strip, the window switcher and history, which
	     the vault cannot conceal. -->
	<title>Sift</title>
</svelte:head>

<!-- The desktop window's own title bar, on every screen and outside every branch (nothing in a
     browser); its arrows only while the app's frame is drawn. -->
<WindowBar arrows={framed} />
<!-- Outside every branch: swap mode must see the shell being taken down. -->
<SwapModeKeeper />

{#if onShellScreen}
	<!-- Before the not-answering branch: this screen has no server by design. -->
	{@render children()}
	<Toaster />
{:else if unreachable}
	<main class="broken">
		<h1>Sift isn't answering</h1>
		<p>The server didn't respond. It may still be starting up.</p>
		<Button onclick={() => location.reload()}>Try again</Button>
	</main>
{:else if ready && onAuthScreen}
	<!-- No shell: these screens belong to somebody who is not inside the application yet. -->
	{@render children()}
	<Toaster />
{:else if framed}
	<div class="shell">
		<Rail />
		<!-- Everything that is not the rail, in one box, so it is rounded and clipped as one. -->
		<div class="content">
			<TopBar />
			<!-- The bar and the screen in one box, which is what fills the window, so the controls
			     survive fullscreen; the top bar, the way off this screen, does not. -->
			<div class="screen-box" bind:this={stageBox}>
				<!--
					The controls that filter, order and describe whatever screen is on, drawn once as one
					row (at the top while the window is filled too). No scroll region around
					it: the panel it drops is absolutely positioned and an `overflow: hidden` ancestor
					would clip it to nothing; a tall panel scrolls inside itself.
				-->
				<div class="screenbar" class:sent={stage.filling && stage.barHidden}>
					<FilterBar />
				</div>
				<StaleBuildBanner />
				<UnlockBanner />
				<UpdateBanner />
				<!-- The shell's own scroll, for the screens that do NOT lay themselves out, through the
				     region the rest of the app uses. -->
				<main class:full-bleed={fullBleed}>
					{#if fullBleed}
						<!-- A screen that lays itself out scrolls inside its own frame
						     (`FULL_BLEED_ROUTES`). -->
						{@render children()}
					{:else}
						<Scroller viewportClass="main-scroll">
							<div class="main-inner">{@render children()}</div>
						</Scroller>
					{/if}
				</main>

				<!-- No handle for a bar that has gone quiet: it returns on the next thing anybody does,
				     as the player's bar does, and `B` brings it back from the keyboard. -->
			</div>

			<!-- The bigger search, INSIDE the page's column, so it centres on the field it stands in
			     for at every rail width rather than on the window. -->
			<SearchOverlay />
		</div>

		<!-- Swap mode's picks: open while the mode is on, on every screen, beside the page. -->
		<SwapDrawer />

		<!--
			Where an asset is drawn, and the only place it is: a tile pushes /asset/{id} without
			running that route, so the grid underneath keeps its scroll; landed on cold, the same panel
			goes up over an empty screen. Not keyed on the id, or Next would rebuild the element the
			browser is drawing fullscreen and leave fullscreen.
		-->
		{#if page.state.asset}
			<AssetModal
				id={page.state.asset}
				startAt={page.state.at ?? null}
				playUntil={page.state.until ?? null}
				onclose={leave}
			/>
		{/if}

		<!-- Settings, the same way (`$lib/settings-ui/settings-view`). -->
		{#if page.state.settings}
			<SettingsModal section={page.state.settings} onclose={leave} />
		{/if}

		<!-- The mini player, drawn here because screens come and go and it must not. -->
		<MiniPlayer />

		<!-- No first-run flow: an account and an installation method, the rest in Settings. -->
		{#if !keyboard.up}<MobileTabs />{/if}

		<!-- Mounted for every signed-in account, since a guest has their own Hidden: it renders
		     nothing and asks the server to close Hidden at a quiet spell, the tab leaving, or the
		     panic key. -->
		<LockTriggers />

		<!-- Asking for the PIN, once, for the whole app: a toast's action is a function and cannot
		     mount a dialog, so it is out here, driven by `vaultPrompt`. -->
		<PinPrompt bind:open={vaultPrompt.asking} reason={vaultPrompt.reason} />
	</div>
	<!-- Once, out here, for the whole app: the toaster is a place on the screen. Carried into the
	     filled box while a screen is filled (`drawnWhileFilled`), with `display: contents`. NOT the
	     last node of this branch: a branch is removed by walking siblings to its last node before
	     attachments are torn down, so a moved last node would let the walk run past the branch. -->
	<div class="toast-home" {@attach drawnWhileFilled}>
		<Toaster />
	</div>
	<DropOverlay />
{/if}

<style>
	.toast-home {
		display: contents;
	}

	.broken {
		display: grid;
		place-items: center;
		align-content: center;
		gap: var(--space-3);
		min-height: 100dvh;
		/* Under the window's title bar (zero in a browser, `--window-chrome`). Padding, as on
		   `.shell`; the shorthand first, so it does not overwrite the clearing. */
		padding: var(--space-6);
		padding-block-start: calc(var(--window-chrome) + var(--space-6));
		text-align: center;
		color: var(--sift-ink-2);
		font: var(--text-body);
	}

	.broken h1 {
		font: var(--text-h2);
		letter-spacing: var(--tracking-h2);
		color: var(--sift-ink);
		margin: 0;
	}

	/*
	 * The rail, and a panel floating beside it: the window's ground is the rail's colour and the
	 * content is a rounded card on it, the gap doing a border's job.
	 */
	.shell {
		display: grid;
		/* Below the window's title bar. PADDING, NOT A MARGIN: a top margin on a child of `body`
		   collapses through it and grows the document a scrollbar, cutting `WindowBar` short.
		   Zero in a browser. */
		padding-block-start: var(--window-chrome);
		height: 100dvh;
		/* Named, so a modal that stops being `position: fixed` is never handed a grid row. */
		grid-template-areas: 'rail content';
		grid-template-columns: auto minmax(0, 1fr);
		background: var(--sidebar);
	}

	.content {
		grid-area: content;
		/* The ground the search overlay is positioned against. See the note where it is rendered. */
		position: relative;
		display: grid;
		grid-template-areas:
			'topbar'
			'stage';
		grid-template-rows: auto minmax(0, 1fr);
		/* `minmax(0, ...)`: a `1fr` track will not shrink below its content, and the whole page
		   would scroll, top bar and all. */
		min-inline-size: 0;
		/* Inset at the top and the right and running off the bottom, square there, since a curve
		   against the window's edge rounds the window itself. */
		margin: var(--space-2) var(--space-2) 0 0;
		border: 1px solid var(--sift-line);
		border-block-end: 0;
		border-radius: var(--radius-xl) var(--radius-xl) 0 0;
		/* The page's ground, painted on the one box that does not scroll, so its light stays put. */
		background: var(--sift-page-fill);
		/* The clip that makes the radius mean something over the grid's own scroller. */
		overflow: hidden;
	}

	/* An entity page on its blurred cover: the flat canvas here too, so the bar meets the backdrop
	   with no step. */
	.content:has(:global(.frame.on-a-picture)) {
		background: var(--sift-bg);
	}

	/*
	 * The bar and the screen, which is what fills the window: `auto` rows take no height when
	 * empty, and `minmax(0, 1fr)` keeps the screen from pushing the card taller. `screen-box`, not
	 * `stage`, which is the media frame's class: one class on two elements answers
	 * `querySelector` with the wrong one.
	 */
	.screen-box {
		grid-area: stage;
		position: relative;
		display: grid;
		grid-template-areas:
			'screenbar'
			'banner'
			'main';
		grid-template-rows: auto auto minmax(0, 1fr);
		min-block-size: 0;
	}

	/*
	 * The bar and whatever panel it has dropped open, as one column, reversed while the window is
	 * filled so the panel comes out of the bar's top; neither knows which way up it is.
	 */
	.screenbar {
		grid-area: screenbar;
		display: flex;
		flex-direction: column;
		min-inline-size: 0;
	}

	/*
	 * The shell's own scroll, for the screens that do NOT lay themselves out: one `1fr` row so the
	 * region has a definite height. `main.full-bleed` turns this into their flex column.
	 */
	main {
		grid-area: main;
		display: grid;
		grid-template-rows: minmax(0, 1fr);
		min-block-size: 0;
	}

	/* The inset inside the scrolling box, around the content rather than the scrollbar. */
	.main-inner {
		padding: var(--space-6);
	}

	/* Clear of a drawer standing beside the page (swap mode's), which says how wide it is. */
	main {
		padding-inline-end: var(--drawer-beside, 0px);
	}

	/* Filling the window: fullscreen paints black behind the element, so Sift's ground is painted.
	   The bar keeps its row, so the screen shortens under it rather than hiding behind it. */
	.screen-box:fullscreen {
		background: var(--background);
	}

	/* Every pixel of the window goes to the screen: there is no card or rail to inset from. */
	.screen-box:fullscreen .main-inner {
		padding: 0;
	}

	/*
	 * The bar, at the top of a filled window, IN FLOW, sliding down: the feeds shrink rather than
	 * all being covered. Tall panels stop at half the window, so the wall never disappears.
	 */
	.screen-box:fullscreen .screenbar {
		z-index: 20;
		/* One fraction row, which eases against `.sent`'s, under the ceiling. */
		display: grid;
		grid-template-rows: minmax(0, 1fr);
		max-block-size: 50vh;
		/* Arriving on the wall's own token (`TheaterWall`), not `.sent`'s clock, which must not lag. */
		animation: descend var(--dur-ambient) var(--ease);
		transition:
			translate var(--dur-slow) var(--ease),
			opacity var(--dur-slow) var(--ease),
			grid-template-rows var(--dur-slow) var(--ease);
	}

	/* Gone quiet: slid back up and out of the tab order, its row given to the wall; `visibility`
	   holds until the slide is done. `0fr` alone would floor the row at its content. */
	.screen-box:fullscreen .screenbar.sent {
		grid-template-rows: minmax(0, 0fr);
		translate: 0 -100%;
		opacity: 0;
		visibility: hidden;
		transition:
			translate var(--dur-slow) var(--ease-in),
			opacity var(--dur-slow) var(--ease-in),
			grid-template-rows var(--dur-slow) var(--ease-in),
			visibility var(--dur-slow) var(--ease-in);
	}

	:global(:root[data-motion='reduce']) .screen-box:fullscreen .screenbar {
		animation-name: appear;
	}

	/* The self-scrolling screens (`fullBleed`): a COLUMN, so a strip above a grid takes what it
	   needs and the grid the rest (`flex: 1` in `AssetGrid`), reaching its own bottom. */
	main.full-bleed {
		display: flex;
		flex-direction: column;
		min-height: 0;
		padding: 0;
		/* Still clear of a drawer beside the page: the walls are full-bleed screens. */
		padding-inline-end: var(--drawer-beside, 0px);
		overflow: hidden;
	}

	/* Below the medium breakpoint: one column and a bar at the bottom. A media query, so the first
	   paint is right; each piece decides for itself whether it shows. */
	@media (max-width: 767px) {
		/* The tabs are the shell's child, so the card is the row above them. */
		.shell {
			grid-template-areas:
				'content'
				'tabs';
			grid-template-columns: minmax(0, 1fr);
			grid-template-rows: minmax(0, 1fr) auto;
			/* A phone turned sideways puts its notch at one side: the shell's own sides clear it. */
			padding-inline: var(--safe-left) var(--safe-right);
		}

		/* Flush and square: there is no rail to round away from. */
		.content {
			margin: 0;
			border: 0;
			border-radius: 0;
		}

		main {
			padding: var(--space-4);
		}
	}
</style>
