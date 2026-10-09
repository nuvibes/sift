<script lang="ts" module>
	/* A kept colour's name is its hex, which is what it was kept as. */
	export function keptLabel(colour: string, worn: 'darker' | 'lighter' | 'softer' | null): string {
		if (worn === 'darker') return `${colour}, worn darker on this background to stay legible`;
		if (worn === 'lighter') return `${colour}, worn lighter on this background to stay legible`;
		if (worn === 'softer') return `${colour}, worn softer on this background to stay legible`;
		return colour;
	}
</script>

<script lang="ts">
	/* The pickers that decide what Sift looks like: a background, an accent, and the two
	 * typefaces. */
	import ChoiceCard from '$lib/components/common/ChoiceCard.svelte';
	import ChoiceGroup from '$lib/components/common/ChoiceGroup.svelte';
	import {
		Button,
		ColorPicker,
		ContextMenu,
		ContextMenuGroup,
		ContextMenuItem,
		MenuButton,
		Popover,
		Pressable,
		SectionHeading,
		Select,
		Tooltip
	} from '$lib/components/common';
	import type { DerivedSwatch } from '$lib/components/common';
	import LabelledRow from '$lib/components/common/LabelledRow.svelte';
	import { phoneWidth } from '$lib/components/common/phone-width.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import {
		BASES,
		BODY_FACES,
		CUSTOM_ACCENT,
		DEFAULT_CHOICE,
		DISPLAY_FACES,
		NAMED_ACCENTS,
		PAIRINGS,
		pairingKey,
		pairingOf,
		theme,
		wornAs,
		type Accent,
		type Base,
		type BodyFace,
		type DisplayFace,
		type Pairing
	} from '$lib/theme/theme.svelte';
	import { wornDifferently } from '$lib/theme/accent';

	/* THE HEADINGS ARE NOT OPTIONAL. */

	/* What each choice is called on screen. The keys are the stylesheet's, the words are a
	   person's. */
	const BASE_LABELS: Record<Base, string> = {
		obsidian: 'Obsidian',
		midnight: 'Midnight',
		graphite: 'Graphite',
		chrome: 'Chrome'
	};
	const BASE_NOTES: Record<Base, string> = {
		obsidian: 'True black, with every card a shade above it. The darkest of the four.',
		midnight: 'Near-black, with a trace of blue in it. What Sift has always looked like.',
		graphite: 'The same gray as Chrome, darker throughout. Neutral rather than blue.',
		chrome: 'A neutral gray with a cool cast, a few steps lighter. Reads as metal.'
	};
	const ACCENT_LABELS: Record<Accent, string> = {
		blue: 'Blue',
		magenta: 'Magenta',
		red: 'Red',
		gold: 'Gold',
		green: 'Green',
		cyan: 'Cyan',
		/* "Custom", not "Your own". */
		custom: 'Custom'
	};
	/* Every face by its own name, because that is what the two menus offer and what a pairing
	   card is called after: a pairing is one face of each role, so its name is the two names. */
	const MAIN_LABELS: Record<DisplayFace, string> = {
		archivo: 'Archivo',
		'space-grotesk': 'Space Grotesk',
		'geist-mono': 'Geist Mono',
		manrope: 'Manrope',
		'jetbrains-mono': 'JetBrains Mono'
	};
	const SECOND_LABELS: Record<BodyFace, string> = {
		'instrument-sans': 'Instrument Sans',
		inter: 'Inter',
		geist: 'Geist',
		'public-sans': 'Public Sans',
		sora: 'Sora',
		'dm-sans': 'DM Sans'
	};
	const MAIN_OPTIONS = DISPLAY_FACES.map((face) => ({ value: face, label: MAIN_LABELS[face] }));
	const SECOND_OPTIONS = BODY_FACES.map((face) => ({ value: face, label: SECOND_LABELS[face] }));

	/* One line under each pairing, keyed by the pairing: Space Grotesk heads two of them. */
	const PAIRING_NOTES: Record<string, string> = {
		'archivo+instrument-sans': 'Tight and editorial. The pairing Sift ships with.',
		'space-grotesk+inter': 'A distinctive display face over the most legible small text there is.',
		'geist-mono+geist': 'Headings set in a monospace. Reads as a machine room.',
		'manrope+public-sans': 'Rounded and geometric, over a plain workhorse.',
		'space-grotesk+dm-sans':
			'The same headings over a softer, rounder text. Numbers take the heading face.',
		'jetbrains-mono+sora': 'Square monospace headings over a wide, open sans. Reads as a terminal.'
	};
	const pairingName = (one: Pairing) =>
		`${MAIN_LABELS[one.display]} and ${SECOND_LABELS[one.body]}`;

	/* Which pairing is showing as chosen, and the empty string when none is: the two menus can
	   be set to faces no pairing puts together, and marking a card in that state would say the
	   pair came from it. */
	const chosenPairing = $derived(pairingOf(theme.faceDisplay, theme.faceBody));
	const pairing = $derived(chosenPairing ? pairingKey(chosenPairing) : '');

	/* Which of each set a fresh install is on, marked on the swatch itself. */
	const isDefault = (part: 'base' | 'accent' | 'faceDisplay' | 'faceBody', option: string) =>
		DEFAULT_CHOICE[part] === option;

	/* Three handlers rather than one taking a part and a value: the three choices have three
	   different sets of allowed values, and a single one would have to widen them all to string,
	   which is exactly the check that stops an accent name reaching the page misspelt. */
	async function saved(work: Promise<void>): Promise<void> {
		try {
			await work;
		} catch {
			toasts.show("That couldn't be saved", { tone: 'error' });
		}
	}
	const chooseBase = (value: Base) => void saved(theme.set('base', value));
	const chooseAccent = (value: Accent) => void saved(theme.set('accent', value));
	const chooseColour = (value: string) => void saved(theme.set('accentHex', value));

	/* Opening the picker IS choosing the custom accent. */
	function opened(open: boolean): void {
		if (open && theme.accent !== CUSTOM_ACCENT) chooseAccent(CUSTOM_ACCENT);
	}

	/* WHAT THE CHOSEN COLOUR BECOMES, READ OFF THE PAGE RATHER THAN WORKED OUT AGAIN. */
	const DERIVED: DerivedSwatch[] = [
		{ name: 'Fill', colour: 'var(--sift-accent)' },
		{ name: 'Text', colour: 'var(--sift-accent-text)' },
		{ name: 'Tint', colour: 'var(--sift-accent-bg)' }
	];
	/* THE COLOURS SOMEBODY KEPT, each shown as it will be WORN. */
	const keptColours = $derived.by(() => {
		void theme.base;
		return theme.swatches.map((colour) => {
			const worn = wornAs(colour)?.accent ?? colour;
			return { colour, worn, label: keptLabel(colour, wornDifferently(colour, worn)) };
		});
	});

	/* Whether the custom colour in force is one of the kept ones, which turns the picker's Save into
	   a Remove: the same press undone, and the way to take one out on a screen with no right button. */
	const keptNow = $derived(theme.swatches.includes(theme.accentHex));

	/* What the last press of Save came to, said under it. */
	let keepSaid = $state('');

	/* The sentence at the limit names the way to make room, rather than only saying no, and
	   names the act rather than a pointer: the menu is a right-click on a desk and the three
	   dots on a phone. */
	const FULL =
		"Ten colors are saved, the most there's room for. To make room, remove one from its menu.";

	async function keepIt(): Promise<void> {
		try {
			const came = await theme.keep();
			keepSaid = came === 'full' ? FULL : '';
		} catch {
			toasts.show("That couldn't be saved", { tone: 'error' });
		}
	}

	/* Taking a colour out, with Undo, the house way: the row changes immediately, and the toast
	   puts it back where it was. */
	async function unkeep(colour: string): Promise<void> {
		keepSaid = '';
		try {
			const at = await theme.unkeep(colour);
			if (at < 0) return;
			toasts.show(`Removed ${colour} from your saved colors`, {
				action: { label: 'Undo', run: () => void saved(theme.keepAt(colour, at)) }
			});
		} catch {
			toasts.show("That couldn't be saved", { tone: 'error' });
		}
	}

	const wear = (colour: string) => void saved(theme.wear(colour));
	const move = (colour: string, by: -1 | 1) => void saved(theme.move(colour, by));

	/* Delete or Backspace on a focused dot takes it out, the keyboard's answer beside the right-click. */
	function byKey(event: KeyboardEvent, colour: string): void {
		if (event.key !== 'Delete' && event.key !== 'Backspace') return;
		event.preventDefault();
		void unkeep(colour);
	}

	const chooseMain = (value: DisplayFace) => void saved(theme.set('faceDisplay', value));
	const chooseSecond = (value: BodyFace) => void saved(theme.set('faceBody', value));

	/* A pairing sets both halves, which is two saves. */
	async function choosePairing(key: string): Promise<void> {
		const chosen = PAIRINGS.find((one) => pairingKey(one) === key);
		if (!chosen) return;
		await saved(theme.set('faceDisplay', chosen.display));
		await saved(theme.set('faceBody', chosen.body));
	}

	/* NO PARAGRAPH UNDER EACH HEADING, on either screen. */
</script>

<!-- Busy until the store has heard what the account is wearing: until then every swatch shows the
     default, and a press on one is a press on a guess. The e2e helper waits on this too. -->
<section class="block" aria-busy={!theme.loaded || undefined}>
	<!-- The heading carries the setting's own key as its id, so a settings search result for it rings
	     this block rather than opening the pane and pointing at nothing. These three are chosen by
	     looking at a picture rather than from a row, which is why there is no row to carry the id. -->
	<SectionHeading id="appearance.theme_base">Background</SectionHeading>

	<ChoiceGroup label="Background" value={theme.base} onchange={(next) => chooseBase(next as Base)}>
		{#each BASES as option (option)}
			<ChoiceCard
				name={BASE_LABELS[option]}
				value={option}
				aside={isDefault('base', option) ? '(default)' : undefined}
				note={BASE_NOTES[option]}
			>
				{#snippet preview()}
					<!--
						A miniature of the thing being chosen: the canvas, a card raised off it, and
						the accent.
					-->
					<span class="sample" data-base={option}>
						<span class="sample-card"></span>
						<span class="sample-accent"></span>
					</span>
				{/snippet}
			</ChoiceCard>
		{/each}
	</ChoiceGroup>
</section>

<section class="block" aria-busy={!theme.loaded || undefined}>
	<SectionHeading id="appearance.theme_accent">Accent</SectionHeading>

	<div class="accents" role="radiogroup" aria-label="Accent color">
		{#each NAMED_ACCENTS as option (option)}
			<Pressable
				role="radio"
				class="accent"
				feedback="wash"
				radius="full"
				data-accent={option}
				aria-checked={theme.accent === option}
				onclick={() => chooseAccent(option)}
			>
				<span class="dot"></span>
				<span class="accent-name">{ACCENT_LABELS[option]}</span>
				{#if isDefault('accent', option)}<span class="default">(default)</span>{/if}
			</Pressable>
		{/each}

		<!-- THE SEVENTH, AND ITS DOT IS THE WHOLE CIRCLE OF HUES. -->
		<Popover onOpenChange={opened} side="bottom" align="start" label="Custom accent color">
			{#snippet trigger({ props })}
				<Pressable
					{...props}
					role="radio"
					class="accent"
					feedback="wash"
					radius="full"
					aria-checked={theme.accent === CUSTOM_ACCENT}
				>
					<span class="dot rainbow"></span>
					<span class="accent-name">{ACCENT_LABELS[CUSTOM_ACCENT]}</span>
				</Pressable>
			{/snippet}

			<!-- THE PICKER IS INSIDE THE PANEL. -->
			<ColorPicker
				label="Accent color"
				value={theme.accentHex}
				swatches={DERIVED}
				oninput={(colour) => theme.preview(colour)}
				onchange={chooseColour}
			/>

			<!-- Keeping the colour in force, at the foot of the picker it was chosen in. -->
			<div class="keep">
				{#if keptNow}
					<Button
						tone="ghost"
						size="small"
						icon="close"
						onclick={() => void unkeep(theme.accentHex)}
					>
						Remove from saved colors
					</Button>
				{:else}
					<Button size="small" icon="save" onclick={() => void keepIt()}>Save this color</Button>
				{/if}
				{#if keepSaid}<p class="keep-said" role="status">{keepSaid}</p>{/if}
			</div>
		</Popover>
	</div>

	<!-- THE KEPT COLOURS, a second row under the six, in the order they were arranged. -->
	{#if keptColours.length > 0}
		<div class="kept-line">
			<div
				id="appearance.theme_accent_swatches"
				class="accents kept"
				role="radiogroup"
				aria-label="Saved colors"
			>
				{#each keptColours as one, index (one.colour)}
					<ContextMenu label="Saved color {one.colour}" triggerClass="saved-dot-trigger">
						<Tooltip label={one.label}>
							<Pressable
								role="radio"
								class="accent"
								feedback="wash"
								radius="full"
								aria-label={one.colour}
								aria-checked={theme.custom && theme.accentHex === one.colour}
								onclick={() => wear(one.colour)}
								onkeydown={(event: KeyboardEvent) => byKey(event, one.colour)}
							>
								<span class="dot" style:background-color={one.worn}></span>
							</Pressable>
						</Tooltip>

						{#snippet items()}
							{@render keptRows(one.colour, index)}
						{/snippet}
					</ContextMenu>
				{/each}
			</div>
			{#if phoneWidth.yes && keptNow}
				<MenuButton label="Saved color {theme.accentHex}">
					{@render keptRows(theme.accentHex, theme.swatches.indexOf(theme.accentHex))}
				</MenuButton>
			{/if}
		</div>
	{/if}

	<!-- A kept colour's rows, the one set both doors open: the right-click and the phone's dots. -->
	{#snippet keptRows(colour: string, index: number)}
		<ContextMenuGroup>
			<ContextMenuItem
				label="Move left"
				icon="arrow_back"
				disabled={index === 0}
				onselect={() => move(colour, -1)}
			/>
			<ContextMenuItem
				label="Move right"
				icon="arrow_forward"
				disabled={index === keptColours.length - 1}
				onselect={() => move(colour, 1)}
			/>
		</ContextMenuGroup>
		<ContextMenuGroup>
			<ContextMenuItem
				label="Remove"
				icon="close"
				destructive
				onselect={() => void unkeep(colour)}
			/>
		</ContextMenuGroup>
	{/snippet}
</section>

<section class="block" aria-busy={!theme.loaded || undefined}>
	<SectionHeading id="appearance.theme_face_display">Font</SectionHeading>

	<!-- THE PAIRINGS FIRST, THEN THE TWO FACES SEPARATELY. -->
	<ChoiceGroup label="Font pairing" value={pairing} onchange={(next) => void choosePairing(next)}>
		{#each PAIRINGS as option (pairingKey(option))}
			<ChoiceCard
				name={pairingName(option)}
				value={pairingKey(option)}
				aside={isDefault('faceDisplay', option.display) && isDefault('faceBody', option.body)
					? '(default)'
					: undefined}
				note={PAIRING_NOTES[pairingKey(option)]}
			>
				{#snippet preview()}
					<!-- Set in the pairing it offers, so the choice is made by looking rather than by
					     recognising a name. The numbers are there because they are the reason the list
					     is short. Both attributes, because a pairing is one face of each role. -->
					<span class="face-sample" data-face-display={option.display} data-face-body={option.body}>
						<span class="face-display">Sift</span>
						<span class="face-data">01:23:45 – 1.4 GB</span>
					</span>
				{/snippet}
			</ChoiceCard>
		{/each}
	</ChoiceGroup>

	<LabelledRow
		label="Main"
		help="Headings, numbers and anything set large. Shown above in each pairing's word Sift."
	>
		<Select
			label="Main font"
			value={theme.faceDisplay}
			options={MAIN_OPTIONS}
			onValueChange={(next: string) => chooseMain(next as DisplayFace)}
		>
			{#snippet optionLabel(option)}
				<!-- The name of a face, set in that face. -->
				<span class="face-name main" data-face-display={option.value}>{option.label}</span>
			{/snippet}
		</Select>
	</LabelledRow>

	<!-- The setting's key as the row's id, so a search or a link naming it rings this row; the main
	     face's key is the heading's above. -->
	<LabelledRow
		id="appearance.theme_face_body"
		label="Secondary"
		help="Everything you read: rows, help, the words on a button."
	>
		<Select
			label="Secondary font"
			value={theme.faceBody}
			options={SECOND_OPTIONS}
			onValueChange={(next: string) => chooseSecond(next as BodyFace)}
		>
			{#snippet optionLabel(option)}
				<!-- The text face, by its own name, on the other attribute. -->
				<span class="face-name second" data-face-body={option.value}>{option.label}</span>
			{/snippet}
		</Select>
	</LabelledRow>
</section>

<style>
	.block {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
		margin-block-start: var(--space-4);
	}

	/* The card, its chosen state and the two lines of words under it are `ChoiceCard`'s. */
	.accents :global(.accent[aria-checked='true']) {
		border-color: var(--sift-accent-text);
		background: var(--sift-accent-bg);
	}

	/* Which one a fresh install is on. Quiet enough to read as a footnote to the name rather than as
	   a second name, and it never moves: the marker is on the swatch, not on whatever is selected. */
	.default {
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	/* The miniature. A canvas, a card raised off it, and a mark in the accent: the three steps
	   that actually differ between the bases, at the size of a thumbnail. */
	.sample {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		block-size: 44px;
		margin-block-end: var(--space-2);
		padding: var(--space-2);
		border-radius: var(--radius-sm);
		/* The page's ground and a card with their light, as the base paints them at full size. */
		background: var(--sift-page-fill);
	}

	.sample-card {
		flex: 1;
		block-size: 100%;
		border: 1px solid transparent;
		border-radius: var(--radius-sm);
		background: var(--sift-card);
	}

	.sample-accent {
		flex: none;
		inline-size: 28px;
		block-size: 100%;
		border-radius: var(--radius-sm);
		background: var(--sift-accent);
	}

	.accents {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-2);
	}

	/* A swatch and its name, as one pill you press. */
	.accents :global(.accent) {
		display: inline-flex;
		align-items: center;
		gap: var(--space-2);
		min-block-size: 36px;
		padding-inline: var(--space-3);
		border: 1px solid var(--sift-line-strong);
		background: var(--sift-surface-2);
		font: var(--text-label);
		color: var(--sift-ink);
	}

	/* The ContextMenu's own wrapper around each kept dot, laid out as the dot it holds rather
	   than as a block on a line of its own. */
	.kept :global(.saved-dot-trigger) {
		display: inline-flex;
	}

	/* The kept row and, on a phone, its three dots after it on the same line. */
	.kept-line {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2);
	}

	.keep {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: var(--space-2);
		margin-block-start: var(--space-3);
	}

	.keep-said {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}

	.dot {
		flex: none;
		inline-size: 14px;
		block-size: 14px;
		border-radius: var(--radius-full);
		background: var(--sift-accent);
	}

	/* The seventh chip's dot: every hue together, which is what the chip stands for. */
	.dot.rainbow {
		background: var(--accent-rainbow);
	}

	/* The colour's name, beside its swatch, with a rule of its own: taking whatever the pill
	   gives it, it could not be told from the "(default)" note after it: two runs of text at the
	   same weight saying two different kinds of thing. */
	.accent-name {
		color: var(--sift-ink);
	}

	/* A FACE'S NAME, SET IN THAT FACE, on both menus and on the triggers that open them. */
	/* The row around it already clips and ellipsises (`.ui-select-item-label`), so all this owes
	   is that a name in a wide face does not wrap onto a second line inside it. */
	.face-name {
		white-space: nowrap;
	}

	.face-name.main {
		font-family: var(--font-display);
	}

	.face-name.second {
		font-family: var(--font-sans);
	}

	.face-sample {
		display: flex;
		align-items: baseline;
		gap: var(--space-3);
		margin-block-end: var(--space-2);
	}

	.face-display {
		font: var(--text-display);
		letter-spacing: var(--tracking-display);
		color: var(--sift-ink);
	}

	/* The reason the list is short, shown: tabular figures, in the face being offered. */
	.face-data {
		font: var(--text-data);
		font-variant-numeric: tabular-nums;
		color: var(--sift-ink-2);
	}
</style>
