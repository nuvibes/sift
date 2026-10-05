<script lang="ts">
	/*
	 * The second question, and the last one before there is a backend: where does Sift keep its own
	 * two folders?
	 *
	 * ## Offered, not imposed
	 *
	 * The default is right for almost everybody, and the people for whom it is wrong (a small
	 * system drive, a library that belongs on a second disk) are exactly the people who will never
	 * find a setting they were not shown. So the folder is named on screen and there is a way to
	 * choose another beside it.
	 *
	 * ## The one sentence that matters most is about the OTHER files
	 *
	 * Somebody being asked where Sift will "keep your library" reasonably fears their videos are
	 * about to be moved. They are not, ever. That sentence is the difference between finishing setup
	 * and cancelling it, so it is a caution in its own right rather than a clause in a paragraph.
	 *
	 * ## Choosing another folder is still the machine's own dialog
	 *
	 * That part does not become a Sift screen and should not: a page cannot open it, drive it, read
	 * it or pre-fill it, which is what makes the folder that comes back one somebody physically
	 * pointed at. It is the same dialog a library root is granted through later.
	 */
	import { onMount } from 'svelte';

	import { BackButton, Button, DoorCard, Note, Problem } from '$lib/components/common';
	import { bridge, type SetupStep } from '$lib/bridge';

	const inTheApp = bridge.canSetUp();

	let suggested = $state<{ path: string; existing: boolean } | null>(null);
	/* Which press is being answered: the pressed button turns, and the page says what the press is
	 * waiting on from the moment it is made. */
	let pressed = $state<'keep' | 'pick' | 'back' | null>(null);
	/* The step the shell says it has reached, once it says one: the folder being checked, then
	 * Sift starting there. Nothing until then, which is the picker still open or a shell that sends
	 * no steps (an older one), and the press's own sentence stands in. */
	let step = $state<SetupStep | null>(null);
	const busy = $derived(pressed !== null);
	let problem = $state<string | null>(null);

	/*
	 * What the page says while a press is being answered.
	 *
	 * Taking a folder checks it, starts Sift there and opens the library, and the first start sets
	 * up the database, which is the slow part. The shell says each step as it starts, so the page
	 * says the one it is on. Opening the library is the window leaving this screen, which says
	 * itself.
	 */
	const waiting = $derived(
		pressed === null || pressed === 'back'
			? null
			: step === 'checking'
				? 'Checking the folder.'
				: step === 'starting'
					? suggested?.existing && pressed === 'keep'
						? 'Opening your library. This can take a little while.'
						: 'Starting Sift in that folder. The first start sets up the database, so this can take a little while.'
					: pressed === 'pick'
						? 'Choose a folder in the window that opened.'
						: 'Checking the folder.'
	);

	onMount(() => {
		void (async () => {
			suggested = await bridge.suggestedLibrary();
		})();
		return bridge.onSetupProgress((next) => (step = next));
	});

	async function goBack() {
		if (busy) return;
		pressed = 'back';
		problem = null;
		const settled = await bridge.setupBack();
		/* Nothing is put back on `ok`: the shell is already drawing the question before this one. */
		if (settled.ok) return;
		problem = settled.refusal;
		pressed = null;
	}

	async function settle(pick: boolean) {
		if (busy) return;
		pressed = pick ? 'pick' : 'keep';
		step = null;
		problem = null;
		const settled = await bridge.chooseLibrary(pick);
		/* Nothing is put back on `ok`: the shell is already starting the backend and loading the
		 * library, and re-enabling the buttons would offer a second press on a window about to go. */
		if (settled.ok) return;
		problem = settled.refusal;
		pressed = null;
		step = null;
	}
</script>

<svelte:head>
	<title>Sift</title>
</svelte:head>

{#if inTheApp}
	<DoorCard heading="Sift data">
		<Problem message={problem} />

		<!-- The same quiet aside the mode cards put after a name, saying the same thing about this
		     folder: it is the one offered, and it is not the only one allowed. A library an earlier
		     installation left is offered first, and says so: the choice then is to carry on with
		     it or to start another somewhere else. -->
		<p class="offered">{suggested?.existing ? '(Your existing library)' : '(Default)'}</p>

		<!-- The path itself, in the machine face the rest of the app uses for a path. It is the whole
		     substance of the question, so it is the thing the eye lands on rather than a clause. -->
		<p class="where data">{suggested?.path ?? 'Checking for a library from before\u2026'}</p>

		{#if suggested?.existing}
			<p class="explain">
				This folder already holds a Sift library. Keep it to continue where you left off, or choose
				a different folder to start a new one.
			</p>
		{:else}
			<p class="explain">
				This folder holds Sift's database, its settings, and the files Sift generates, such as
				thumbnails. If your library is somewhere else, choose that folder. A folder with no library
				in it starts a new, empty library, and your library stays where it is.
			</p>
		{/if}

		<Note>This doesn't affect your existing photos or videos.</Note>

		<!-- The pressed button keeps its words and turns (`busy`); the others only stop answering. -->
		<Button
			tone="primary"
			busy={pressed === 'keep'}
			disabled={busy || suggested === null}
			onclick={() => void settle(false)}
		>
			{suggested?.existing ? 'Keep this library' : 'Choose this folder'}
		</Button>
		<Button
			tone="ghost"
			busy={pressed === 'pick'}
			disabled={busy}
			onclick={() => void settle(true)}
		>
			Choose a different folder
		</Button>
		{#if waiting}
			<!-- `status`, so a screen reader hears what is being waited on as well as seeing it. -->
			<p class="explain" role="status">{waiting}</p>
		{/if}
		<!-- Back to the mode question, through `onback` rather than through history: the shell unsays
		     the answer that led here and draws the screen that asks it again, where a history step
		     would show a stale copy of a screen whose answer is still saved. -->
		<BackButton label="How Sift runs" onback={() => void goBack()} />
	</DoorCard>
{:else}
	<DoorCard
		heading="This screen belongs to the Sift app"
		explain="It's where the desktop app asks which folder Sift keeps its own database and cache in."
	>
		<Note>
			You're looking at a Sift that's already running, so this question has been answered. To see
			where its folders are, or to move them, search Settings for Sift data.
		</Note>
	</DoorCard>
{/if}

<style>
	/* The path, on the recessed ground so it reads as a value rather than as more prose. It wraps
	   rather than ellipsising: a folder chosen on a second disk can be long, and the part that says
	   WHICH disk is the beginning. */
	.where {
		margin: 0;
		padding: var(--space-2) var(--space-3);
		border-radius: var(--radius-md);
		background: var(--sift-surface-2);
		color: var(--sift-ink);
		overflow-wrap: anywhere;
	}

	/* The aside above the path, in the two tokens the mode cards' own "(most common)" is drawn from.
	   What is shared is that PAIRING, not a component. This is one word above a path, and wrapping
	   a span in a component to say it would be more machinery than the thing it says. */
	.offered {
		margin: 0;
		font: var(--text-label);
		font-weight: 400;
		color: var(--sift-ink-3);
	}

	.explain {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}
</style>
