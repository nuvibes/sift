<script lang="ts">
	/* The look of the app (background, accent, lettering) and the sidebar.
	 *
	 * The sidebar is here rather than only on the sidebar itself for one reason: a row that has been
	 * put away is not on the sidebar to be pressed. Rearranging is direct manipulation and belongs
	 * where the rows are; putting one BACK has to live somewhere that lists them all, including the
	 * ones that are not showing.
	 */
	import { onMount, tick } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Button, SectionHeading, Select, Switch, Tooltip } from '$lib/components/common';
	import LabelledRow from '$lib/components/common/LabelledRow.svelte';
	import ActionRow from './ActionRow.svelte';
	import SettingRow from './SettingRow.svelte';
	import ThemeChoices from './ThemeChoices.svelte';
	import { SettingsPanel } from './panel.svelte';
	import { appearance, UNITS_KEY } from '$lib/theme/appearance.svelte';
	import { CLOCK_KEY, clock } from '$lib/shell/clock.svelte';
	import { unitSystem } from '$lib/shell/measure';
	import TileMarksPicture from './TileMarksPicture.svelte';
	import { TILE_MARKS } from './TileMarksPicture.search';
	import { tileMarks } from '$lib/grid/tile-marks.svelte';
	import { RATING_SCALE_KEY } from '$lib/library/rating.svelte';
	import { ACCENT_HEX_KEY, ACCENT_SWATCHES_KEY, theme } from '$lib/theme/theme.svelte';
	import SharingLegend from '$lib/settings-ui/SharingLegend.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import {
		DEFAULT_RAIL_ORDER,
		navItem,
		RAIL_DIVIDER,
		type NavItem
	} from '$lib/components/shell/nav';
	import { rail } from '$lib/components/shell/rail-state.svelte';
	import {
		measure,
		motion,
		setMotionPreference,
		slide,
		type MotionPreference
	} from '$lib/shell/motion.svelte';

	/* The three answers, and "Follow Windows" first because it is the right one for almost
	   everybody: the operating system already carries an accessibility preference and honouring it
	   is not optional. The other two are for overruling it in either direction on one machine. */
	/* Animating FIRST, because it is the default. Windows turns its reduced-motion preference on for
	   reasons that are often nothing to do with the person (a battery saver, a remote session),
	   and a library that arrived silent would look broken rather than considerate. Following the
	   system is one press away, for anybody who wants that behaviour. */
	/* In the words of movement rather than "animation", a word that reads as a kind of
	   file (a GIF, as every screen calls it). */
	const MOTION_OPTIONS = [
		{ value: 'full', label: 'Full motion' },
		{ value: 'system', label: 'Follow Windows' },
		{ value: 'reduce', label: 'Reduced motion' }
	];
	import { session } from '$lib/shell/session.svelte';
	import { explainAbsentRows } from '$lib/settings-ui/settings-anchor.svelte';

	/* The custom accent's colour has no row of its own: Custom, among the accents, opens its
	   picker. A search or a link naming it rings the accents and says so. */
	const CUSTOM_ACCENT_IS = 'A color of your own is chosen with Custom, among the accent colors.';
	/* The saved colors are listed only once there is one. */
	const NONE_SAVED = 'Colors you keep with Custom are listed here once you have kept one.';
	$effect(() =>
		explainAbsentRows((key) =>
			key === ACCENT_HEX_KEY
				? { because: CUSTOM_ACCENT_IS, near: 'appearance.theme_accent' }
				: key === ACCENT_SWATCHES_KEY
					? { because: NONE_SAVED, near: 'appearance.theme_accent' }
					: null
		)
	);

	/* Every destination, in the order the sidebar is in, with the ones this account is not shown left
	   out, and the rule among them, because it is a position in that order. A row can be dragged past
	   it here the same way it can on the sidebar, which is the only way to say from this screen which
	   half of the sidebar something belongs to. */
	const rows = $derived(
		rail.order
			.map((id) => (id === RAIL_DIVIDER ? RAIL_DIVIDER : navItem(id)))
			.filter((entry): entry is NavItem | typeof RAIL_DIVIDER => entry !== undefined)
			.filter((entry) => entry === RAIL_DIVIDER || !entry.admin || session.isAdmin)
	);

	const isItem = (entry: NavItem | typeof RAIL_DIVIDER): entry is NavItem => entry !== RAIL_DIVIDER;

	/* The declarations for the two ordinary settings on this pane. The theme pickers above are
	   deliberately NOT rows: you pick a theme by looking at a picture of it, which is the one place
	   in Settings where a swatch beats a word. That exception stops here and goes no further. */
	const declarations = new SettingsPanel();

	/* Read once here rather than with `{@const}` in the markup: a const tag has to be the immediate
	   child of a block, and these sit inside ordinary sections. */
	/* How many stars a rating is drawn as.
	 *
	 * On this pane because that is what it is: nothing about it reaches the server, which stores
	 * every rating out of ten whichever is chosen here. It is the same kind of thing as the accent:
	 * how the app looks to one person, remembered for them on whatever machine they open it on.
	 */
	const scaleEntry = $derived(declarations.entry(RATING_SCALE_KEY));

	/* It decides how much of a RECORD is drawn and changes nothing that is stored, so it is a
	   choice about what is on screen like every other one on this pane. That a stash-box is what
	   fills those fields in is a fact about where the data comes from, not about what the
	   setting does. */
	const RECORD_FIELDS_KEY = 'records.show_every_field';
	const recordFieldsEntry = $derived(declarations.entry(RECORD_FIELDS_KEY));

	/* Which system a measurement is READ in. Beside the other record setting, because that is
	   what it is: nothing about it reaches the server (a height is stored in whole centimetres
	   whichever answer is chosen), so it is a choice about what is on screen like every other
	   one on this pane. Written through `appearance` rather than through `declarations` for the
	   reason the strip above it is: the store is what the records on the screen behind this
	   sheet are reading, so a change lands there rather than at the next reload. */
	const unitsEntry = $derived(declarations.entry(UNITS_KEY));

	/* Which clock every time in the app is written on. Saved through the ordinary settings write,
	   which `clock` follows, so every time on the screen behind this sheet redraws at once. The
	   row shows `clock.hours` rather than the pane's copy, because that is what the times read. */
	const clockEntry = $derived(declarations.entry(CLOCK_KEY));

	onMount(() => {
		void appearance.load();
		void theme.load();
		void tileMarks.load();
		void declarations.load();
	});

	/* Through the store: the store holds it, the store writes it, and a refusal puts the control
	   back where it was. */
	async function changeUnits(next: unknown): Promise<void> {
		try {
			await appearance.setUnits(unitSystem(next));
		} catch {
			toasts.show("That couldn't be saved", { tone: 'error' });
		}
	}

	/*
	 * Rearranging from here as well as from the sidebar itself.
	 *
	 * Direct manipulation belongs where the rows are, and this screen already lists every one
	 * of them, including the ones put away, which are not on the sidebar to be dragged at all. So the
	 * order can be set in the one place that can see the whole of it.
	 *
	 * No mode here, unlike the sidebar. There a row is a link somebody presses dozens of times a day,
	 * so picking one up has to be asked for; a row on this screen is a row on a settings page, and
	 * nothing is lost if it is dragged.
	 */
	let listEl = $state<HTMLElement | null>(null);
	let carrying = $state<string | null>(null);
	let landed = $state<string | null>(null);

	async function rearrange(change: () => void): Promise<void> {
		const before = measure(listEl?.querySelectorAll('li') ?? []);
		change();
		await tick();
		slide(before);
	}

	function onDragStart(event: DragEvent, id: string): void {
		if (!event.dataTransfer) return;
		carrying = id;
		event.dataTransfer.effectAllowed = 'move';
		// Nothing is carried in it: the row being moved is already known, and putting an id on the
		// clipboard would offer it to every other page that accepts a drop.
		event.dataTransfer.setData('text/plain', '');
	}

	function onDragEnd(): void {
		carrying = null;
		landed = null;
	}

	/* The rows follow the pointer rather than waiting for the drop, and `landed` is what makes that
	   affordable: `dragover` fires many times a second over the same row, and each one would otherwise
	   re-run the move and start another animation over the top of the last. */
	function onDragOver(event: DragEvent, id: string): void {
		if (carrying === null) return;
		event.preventDefault();
		if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
		if (carrying === id) return;

		const box = (event.currentTarget as HTMLElement).getBoundingClientRect();
		const side = event.clientY > box.top + box.height / 2 ? 'after' : 'before';
		const at = `${id}:${side}`;
		if (landed === at) return;
		landed = at;

		const moving = carrying;
		void rearrange(() => rail.placeBy(moving, id, side));
	}

	/* The row is already where it is going. This ends the drag, and prevents the browser's own answer
	   to dropped content, which is to navigate. */
	function onDrop(event: DragEvent): void {
		if (carrying === null) return;
		event.preventDefault();
		carrying = null;
		landed = null;
	}

	/** The same thing without a pointer. Held with Alt so the arrows still scroll the page. */
	function onRowKeydown(event: KeyboardEvent, id: string): void {
		const direction = event.key === 'ArrowUp' ? -1 : event.key === 'ArrowDown' ? 1 : 0;
		if (direction === 0 || !(event.altKey || event.metaKey)) return;
		event.preventDefault();
		void rearrange(() => rail.nudge(id, direction)).then(() => {
			listEl?.querySelector<HTMLElement>(`[data-row="${id}"]`)?.focus();
		});
	}

	/** Whether there is anything for a reset to undo. */
	const arranged = $derived(
		rail.hidden.length > 0 || rail.order.join(',') !== DEFAULT_RAIL_ORDER.join(',')
	);
</script>

<section class="appearance">
	<p>
		Sift has four dark backgrounds and seven accent colors. Every combination below is checked for
		legibility.
	</p>

	<!-- The three pickers, drawn by the component the first run draws too, so a second screen
	     wanting them reuses one component rather than copying its markup and style. -->
	<ThemeChoices />

	<!--
		How many stars a rating is drawn as.

		Here rather than under anything to do with tags, because nothing about it reaches the
		server's idea of a rating: every one is stored out of ten whichever is chosen, and this says
		how many boxes to draw. Switching it back and forth writes nothing and loses nothing.

		Drawn through the ordinary row, from the setting's own declaration: the same two words,
		the same help, the same control the server said it was. This pane names its keys one by one
		rather than drawing a whole section, so a setting registered into a section nothing reads
		would save and be obeyed with no row anywhere to press. A gate refuses that.
	-->
	<section class="block">
		<SectionHeading>Ratings</SectionHeading>

		{#if scaleEntry}
			<SettingRow
				entry={scaleEntry}
				value={declarations.value(RATING_SCALE_KEY)}
				onchange={(next: unknown) => void declarations.save(RATING_SCALE_KEY, String(next))}
			/>
		{/if}

		<!-- What the choice looks like, in the thing being chosen. A number of stars is the one
		     setting on this pane whose effect can be shown in the row that sets it. -->
		<p class="note">
			Sift stores every rating out of 10, so switching loses nothing. Three out of five is six out
			of 10, and switching back shows three again.
		</p>
	</section>

	<!-- Every time Sift shows carries its seconds; this chooses the clock they are written on. -->
	<section class="block">
		<SectionHeading>Time</SectionHeading>

		{#if clockEntry}
			<SettingRow
				entry={clockEntry}
				value={clock.hours}
				onchange={(next: unknown) => void declarations.save(CLOCK_KEY, String(next))}
			/>
		{/if}
	</section>

	<!--
		Motion. Remembered in THIS BROWSER rather than on the server with the theme, and that is the
		point of it rather than a shortcut: a theme is a fact about a person and should follow them
		to another computer, while the reason to turn animation off is usually a fact about the
		machine in front of you. A weak laptop is no reason for the same account to stop moving on a
		desktop with a real graphics card. Without this switch on screen, the only way to stop Sift
		moving would be a system-wide setting.
	-->
	<section class="block">
		<SectionHeading>Motion</SectionHeading>
		<p class="note">
			How much Sift animates when things open, close and change. Saved in this browser only.
		</p>

		<!-- A settings row like every other control on this pane, not a stacked form field. -->
		<LabelledRow
			label="Movement"
			help={motion.reduced
				? 'Motion is reduced. Things still fade, but nothing slides.'
				: 'Things slide and fade as they open and close.'}
		>
			<Select
				label="Movement"
				value={motion.preference}
				options={MOTION_OPTIONS}
				onValueChange={(next: string) => setMotionPreference(next as MotionPreference)}
			/>
		</LabelledRow>
	</section>

	<!-- A group of choices: its two rows separate by space, with no line between them.
	     DRESSED BY: .choices (LabelledRow draws no line between the rows of a group of choices) -->
	<section class="block choices">
		<SectionHeading>People details</SectionHeading>
		<p class="note">
			These decide how a person's record is drawn, from how much of it you see to how heights read.
		</p>

		{#if recordFieldsEntry}
			<SettingRow
				entry={recordFieldsEntry}
				value={declarations.value(RECORD_FIELDS_KEY)}
				onchange={(next: unknown) => void declarations.save(RECORD_FIELDS_KEY, next === true)}
			/>
		{/if}

		{#if unitsEntry}
			<SettingRow
				entry={unitsEntry}
				value={appearance.units}
				onchange={(next: unknown) => void changeUnits(next)}
			/>
		{/if}
	</section>

	<!--
		WHAT A TILE HAS ON IT, drawn as a tile.

		Seven marks, each answerable: the badges along the top, the heart and the score, the clock,
		the sharing badge. A single "Mark shared files" switch would leave the other six decided
		once, in code, on behalf of everybody. What somebody chose for the sharing badge carries
		across as that mark's answer.
	-->
	<section class="block">
		<SectionHeading id="appearance.tile_marks">{TILE_MARKS.name}</SectionHeading>
		<p class="note">
			Select a mark on the picture to choose when it appears: always, only when you point at a tile,
			or never. The warning Sift shows when it can't reach a file always appears, because it means
			your library and your disk disagree.
		</p>

		<TileMarksPicture {declarations} />

		<p class="note">
			At the smallest tile size, Sift leaves off the marks along the top whatever you choose, so
			small tiles stay readable.
		</p>

		<!-- Admin-only, and a courtesy rather than a control: the server fills the sharing
		     marks for an admin and for nobody else, so this explains glyphs a guest never sees.
		     Shown whether or not the badge is turned on: somebody who has just turned it off
		     and wants to know what they were looking at is exactly the person who needs it. -->
		{#if session.isAdmin}
			<SharingLegend />
		{/if}
	</section>

	<section class="block">
		<SectionHeading>Sidebar</SectionHeading>
		<p class="note">
			Choose which pages the sidebar shows, and their order. Drag a row by its grip to move it; rows
			below the line sit at the bottom of the sidebar. You can also rearrange the sidebar itself:
			hold a row down, or right-click it and choose Rearrange.
		</p>
		<p class="note">Saved in this browser only, so each device can have its own arrangement.</p>

		<ul class="rows" bind:this={listEl}>
			{#each rows as entry (isItem(entry) ? entry.id : RAIL_DIVIDER)}
				{#if isItem(entry)}
					<li
						class:carrying={carrying === entry.id}
						ondragover={(event) => onDragOver(event, entry.id)}
						ondrop={onDrop}
					>
						<!--
							The handle is what is draggable, not the whole row.

							The row holds a switch, and a switch inside something draggable is a switch that
							sometimes gets dragged instead of pressed. It is a button so it is reachable by
							keyboard, and Alt with an arrow does from the keyboard what dragging it does with a
							pointer.
						-->
						<Tooltip label="Move {entry.label}">
							<Button
								tone="ghost"
								size="small"
								class="handle"
								icon="drag_indicator"
								aria-label="Move {entry.label}"
								data-row={entry.id}
								draggable="true"
								ondragstart={(event) => onDragStart(event, entry.id)}
								ondragend={onDragEnd}
								onkeydown={(event) => onRowKeydown(event, entry.id)}
							/>
						</Tooltip>

						<span class="what">
							<Icon name={entry.icon} size={18} />
							{entry.label}
						</span>
						<!-- Settings has no switch. It is where a hidden row is put back, so hiding it would
						     remove the way to undo hiding it. -->
						{#if entry.fixed}
							<span class="always">Always shown</span>
						{:else}
							<Switch
								checked={rail.shows(entry.id)}
								label="Show {entry.label} in the sidebar"
								onCheckedChange={(on) => (on ? rail.show(entry.id) : rail.hide(entry.id))}
							/>
						{/if}
					</li>
				{:else}
					<!-- The rule, as a row. It cannot be moved and has nothing to switch: it marks where the
					     bottom of the sidebar begins, so what it needs is to be a place a row can be dropped
					     on either side of. -->
					<li
						class="rule section-label"
						ondragover={(event) => onDragOver(event, RAIL_DIVIDER)}
						ondrop={onDrop}
					>
						<span>Bottom of the sidebar</span>
					</li>
				{/if}
			{/each}
		</ul>

		<ActionRow
			id="appearance.sidebar-reset"
			label="Reset the sidebar"
			help="Shows every page again, in the order Sift started with."
			action="Reset"
			actionLabel="Reset the sidebar"
			disabled={!arranged}
			onclick={() => rail.reset()}
		/>
	</section>

	<!-- "Open links in" and "Closing the window" are on General: what the application does on
	     this device when you press something is not how it looks. -->
</section>

<style>
	/* No width of its own. A pane carrying a pixel cap of its own would end its rows somewhere
		   different from every other section's. The shell caps the content once, for every pane. */
	.appearance {
		display: flex;
		flex-direction: column;
		gap: var(--space-4);
	}

	p {
		margin: 0;
		font: var(--text-body);
		color: var(--sift-ink-2);
	}

	.note {
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	.block {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
		margin-block-start: var(--space-4);
	}

	.rows {
		display: flex;
		flex-direction: column;
		margin: 0;
		padding: 0;
		list-style: none;
	}

	/* A hairline between rows and nothing around them: the list is a list, not a boxed table. */
	.rows li {
		display: flex;
		align-items: center;
		gap: var(--space-3);
		min-block-size: 44px;
		border-block-end: 1px solid var(--sift-line);
	}

	.rows li:last-child {
		border-block-end: 0;
	}

	/* The row being carried. Dimmed rather than taken out: the space it will come back to is what
	   makes the rest of the list readable while it moves. */
	.rows li.carrying {
		opacity: 0.4;
	}

	/* The grip on a sidebar row. Pulled back into the row's own padding so the glyph lines up with
	   the edge rather than sitting inside it, and it takes the grab cursor: everything else is the
	   shared button's. `:global` because the class is handed to it. */
	.rows :global(.handle) {
		flex: none;
		margin-inline-start: calc(var(--space-2) * -1);
		color: var(--sift-ink-3);
		cursor: grab;
	}

	/*
	 * The rule, as a row.
	 *
	 * A line with a word on it rather than a row with a control, because it is not a destination: it
	 * is the position that says which half of the sidebar everything after it is in. Quiet enough not
	 * to read as another switchable thing, and tall enough to be a drop target.
	 */
	.rows li.rule {
		min-block-size: 36px;
		border-block-end: 1px solid var(--sift-line-strong);
	}

	.what {
		display: flex;
		align-items: center;
		gap: var(--space-3);
		margin-inline-end: auto;
		font: var(--text-body);
		color: var(--sift-ink);
	}

	.what :global(.icon) {
		color: var(--sift-ink-3);
	}

	.always {
		font: var(--text-label);
		color: var(--sift-ink-3);
	}
</style>
