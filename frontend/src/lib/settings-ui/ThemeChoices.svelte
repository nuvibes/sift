<script lang="ts" module>
	/* A kept colour's name is its hex, which is what it was kept as. Where it is worn differently on
	   this background, the label says how, and why in the accent's own help's words: to stay
	   legible. */
	export function keptLabel(colour: string, worn: 'darker' | 'lighter' | 'softer' | null): string {
		if (worn === 'darker') return `${colour}, worn darker on this background to stay legible`;
		if (worn === 'lighter') return `${colour}, worn lighter on this background to stay legible`;
		if (worn === 'softer') return `${colour}, worn softer on this background to stay legible`;
		return colour;
	}
</script>

<script lang="ts">
	/*
	 * The pickers that decide what Sift looks like: a background, an accent, and the two typefaces.
	 *
	 * Three questions, five answers. The accent is six named colours and a seventh worked out from
	 * one somebody chose; the lettering is six pairings offered first and two menus under them, so
	 * that any main face can sit over any secondary face.
	 *
	 * ## Why it is a component and not part of the Appearance pane
	 *
	 * Because two screens ask the question. The settings pane asks it whenever somebody wants
	 * to change their mind; the first run asks it once, on the way in, because the previews are
	 * miniatures of Sift's own surfaces and choosing is the first thing anybody learns about the
	 * app. Copied into the second screen, this would be a hundred and fifty lines of markup and
	 * style in two places, and the copy that drifts is the one nobody opens, which would be the
	 * one a stranger sees first.
	 *
	 * ## Every picker shows the thing rather than naming it
	 *
	 * The background samples are painted in the base they offer, the six accent dots in the accent,
	 * and the lettering samples set in the pairing, through the same theme attributes the page
	 * itself takes, scoped to one swatch. There is no second copy of a colour or a font name
	 * anywhere in here, so a sample cannot drift from what choosing it actually does.
	 *
	 * The colours somebody kept are the one thing painted from a value rather than from an
	 * attribute, and they have to be: a kept colour is not in the stylesheet, so there is no rule
	 * for a dot to re-resolve from. Each is painted as the fill it will be WORN as on the base in
	 * force, derived the way the custom accent is, so the dot is still the thing it offers.
	 *
	 * ## It writes as you press, and it is the only thing that does
	 *
	 * `theme.set` puts the choice on the page, in the browser's mirror and on the server at once, so
	 * the answer to "what does this look like" is the app changing under you. A caller that wants
	 * the choices RECORDED as answered (the first run does, so it never asks again), writes the
	 * keys itself; that is a different question from what the value is.
	 */
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

	/*
	 * THE HEADINGS ARE NOT OPTIONAL.
	 *
	 * Without them, even where the step above has already said what it is for, the screen is three
	 * pickers running into each other with nothing anywhere saying which is which: a row of
	 * coloured pills between two grids of cards, and no word "Accent" on the screen at all. An
	 * `aria-label` on each group would tell a screen reader and nobody else, which is the worse
	 * half of the same fault.
	 */

	/* What each choice is called on screen. The keys are the stylesheet's, the words are a person's.
	   The accent names are what the colours read as rather than a spectrum: the six are spaced
	   evenly around the circle from Sift's blue, which puts a magenta and a cyan in the set and no
	   orange. */
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
		/* "Custom", not "Your own". The six above are named for the colour they are; the seventh
		   is named for the fact that it is yours to set, and the word a person is looking for
		   when they want that is the one every other application uses. */
		custom: 'Custom'
	};
	/* Every face by its own name, because that is what the two menus offer and what a pairing card
	   is called after: a pairing is one face of each role, so its name is the two names. */
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

	/* Which pairing is showing as chosen, and the empty string when none is: the two menus can be
	   set to faces no pairing puts together, and marking a card in that state would say the pair
	   came from it. `ChoiceGroup` reads an empty value as nothing chosen. */
	const chosenPairing = $derived(pairingOf(theme.faceDisplay, theme.faceBody));
	const pairing = $derived(chosenPairing ? pairingKey(chosenPairing) : '');

	/* Which of each set a fresh install is on, marked on the swatch itself.
	   Read from the same constant the app starts from rather than written out again here, so the
	   three markers cannot end up on the wrong swatches after a default is changed. */
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

	/*
	 * Opening the picker IS choosing the custom accent.
	 *
	 * Done on the popover opening rather than in the chip's own `onclick`, and that is not a
	 * preference: the trigger's handler comes from the popover, spread onto the chip, and a second
	 * `onclick` written beside it replaces one or the other depending on which is written last. One
	 * of the two would silently stop working. The popover tells us when it opened, which is the same
	 * moment and cannot be lost.
	 *
	 * It has to be chosen before the picker can preview anything: the five values a colour turns into
	 * are solved against the background in force, and the store refuses to paint a preview while one
	 * of the six named accents is on, which is right, and would otherwise be a picker that changed
	 * nothing at all.
	 */
	function opened(open: boolean): void {
		if (open && theme.accent !== CUSTOM_ACCENT) chooseAccent(CUSTOM_ACCENT);
	}

	/*
	 * WHAT THE CHOSEN COLOUR BECOMES, READ OFF THE PAGE RATHER THAN WORKED OUT AGAIN.
	 *
	 * Calling `accentFamily` here to derive the fill, the text shade and the tint for the row of
	 * samples would be a second call of the same arithmetic on the same colour, and a second one is
	 * a copy that can disagree: it would have to read the three grounds off the page for itself,
	 * in a second place that knows which three they are.
	 *
	 * It is unnecessary as well as risky. The store paints the derived family onto the document as
	 * the very properties these three semantic names read, and it repaints them on every step of a
	 * drag, so a swatch drawn in `--sift-accent` IS the fill that was derived, a frame after it
	 * was derived, with nothing in between to get wrong.
	 */
	const DERIVED: DerivedSwatch[] = [
		{ name: 'Fill', colour: 'var(--sift-accent)' },
		{ name: 'Text', colour: 'var(--sift-accent-text)' },
		{ name: 'Tint', colour: 'var(--sift-accent-bg)' }
	];
	/*
	 * THE COLOURS SOMEBODY KEPT, each shown as it will be WORN.
	 *
	 * The miniature rule: a dot shows what pressing it puts on the page, and on this base that is
	 * the fill the derivation makes of the colour, not the colour as it was kept. A pale yellow kept
	 * on a dark base is worn as a deeper gold, because white words have to read on it, and a dot in
	 * the pale yellow would promise a colour the page will never wear. So each dot is the derived
	 * fill, through `wornAs`, which reads the grounds off the page the same way the colour in force
	 * is painted; and where the two differ enough to see, the label says so and in which direction.
	 *
	 * `theme.base` is read here for what it does: a different background is a different derivation,
	 * and the dots have to follow it.
	 */
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

	/* What the last press of Save came to, said under it. Empty when there is nothing to say: a colour
	   saved shows up as a dot, which is its own answer. */
	let keepSaid = $state('');

	/* The sentence at the limit names the way to make room, rather than only saying no, and names
	   the act rather than a pointer: the menu is a right-click on a desk and the three dots on a
	   phone. Ten is `MAX_SWATCHES` in a word; the store's test holds the number at ten. */
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

	/* Taking a colour out, with Undo, the house way: the row changes at once, and the toast puts it
	   back where it was. */
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

	/* A pairing sets both halves, which is two saves. Sent one after the other rather than together
	   because the store writes one key at a time and rolls that one key back when the server refuses
	   it: a pair sent as one batch would have to invent a second rollback rule for the half that
	   did land. */
	async function choosePairing(key: string): Promise<void> {
		const chosen = PAIRINGS.find((one) => pairingKey(one) === key);
		if (!chosen) return;
		await saved(theme.set('faceDisplay', chosen.display));
		await saved(theme.set('faceBody', chosen.body));
	}

	/*
	 * NO PARAGRAPH UNDER EACH HEADING, on either screen.
	 *
	 * Each would describe what its previews already show: that the backgrounds are dark, that the
	 * accents are the same brightness, that the fonts differ in shape and not in size. Every one of
	 * those is visible in the swatch beside the sentence, and reading about a colour you are
	 * looking at is slower than looking at it. Somebody in Settings came to change a colour, not to
	 * be told what a colour is, so one picker with no paragraph serves both screens.
	 */
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
					<!-- A miniature of the thing being chosen: the canvas, a card raised off it, and
					     the accent. A word alone asks somebody to imagine a colour they have never
					     seen. It carries the same attribute the page takes, so the sample cannot
					     drift from what choosing it does. -->
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

		<!--
			THE SEVENTH, AND ITS DOT IS THE WHOLE CIRCLE OF HUES.

			It carries no `data-accent` for a reason the six do not have to think about: there is no
			rule in the stylesheet to re-resolve from, because the colour is not in the stylesheet.

			Not the colour that was CHOSEN. The six chips beside it each stand for one colour, and
			this one does not: it stands for "whichever you like". Wearing one colour would make
			it read as a seventh named accent, and on a fresh account the colour it wore would be
			the default blue, which is one of the six. A rainbow says what the chip is for before it
			is pressed. The colour actually in force is on the page, which is where a theme is
			judged anyway.

			Pressing it opens the picker. The popover owns the press. See `opened`.
		-->
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

			<!--
				THE PICKER IS INSIDE THE PANEL.

				Not a swatch that opens the operating system's colour chooser: a window in another
				typeface with another focus ring, which is the one thing every other control in this
				app refuses. The chip opens this, and the square, the hue and the hex are all in it.

				No paragraph under the box describing the five derived values: the row of samples at
				the foot of the picker SHOWS three of them changing as the marker moves. The promise
				such a paragraph would make (that every one of them is held to a legibility floor
				against the background in force) is a measured property of `theme/accent.ts`
				rather than something a settings pane has to say out loud.
			-->
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

	<!--
		THE KEPT COLOURS, a second row under the six, in the order they were arranged.

		Each is a radio like the six: pressing one wears it, and the one in force is marked, beside
		Custom being marked above, which is the truth: the accent is the custom one, and it is this
		colour. Named by its hex for a screen reader, the colour it was kept as. Right-click for
		Remove and for moving it along the row; Delete does the same from the keyboard.

		A phone has no right button, and a hold is a selection there, never a menu (`ContextMenu`). Its
		door to a menu is the three dots acting on what is picked, as a tile's is on the selection
		bar: pressing a dot wears it, which is this row's picking, and the dots after the row open
		the same rows for the colour in force, as the sheet every menu is at a phone's width.
	-->
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

	<!--
		THE PAIRINGS FIRST, THEN THE TWO FACES SEPARATELY.

		The pairings are not a shortcut to the menus below: each is a display face and a text face
		chosen on purpose to sit together, and they are still the answer almost everybody wants. The
		menus are every other combination, and they sit underneath because choosing one of them is
		choosing to take the pairing apart.

		Pressing a pairing sets both menus. Moving either menu to a pair no card offers leaves no
		pairing marked, which is honest: saying otherwise would name a card that did not produce it.
	-->
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
				<!-- The name of a face, set in that face. `data-face-display` re-declares
				     `--font-display` on this one span, the same way the pairing samples above take
				     both attributes, so the word is drawn in the family it names and nothing else on
				     the row moves. -->
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

	/* The card, its chosen state and the two lines of words under it are `ChoiceCard`'s. What is
	   left here is the accent swatch, which is a different shape: a dot and a name, with no preview
	   of a whole screen to draw. */
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

	/* The miniature. A canvas, a card raised off it, and a mark in the accent: the three steps that
	   actually differ between the bases, at the size of a thumbnail. */
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

	/* A swatch and its name, as one pill you press. `Pressable` rather than the shared button,
	   because it carries a colour sample rather than a label, and it is a radio, which the button
	   is not. `:global` because the class is handed to a component. */
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

	/* The ContextMenu's own wrapper around each kept dot, laid out as the dot it holds rather than as
	   a block on a line of its own. Scoped under the row so the class reaches nothing else. */
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

	/* The seventh chip's dot: every hue at once, which is what the chip stands for. The gradient is
	   a token: a rainbow written here would be a colour outside the one file allowed to name one,
	   and this one is not a theme decision but the colour space itself. */
	.dot.rainbow {
		background: var(--accent-rainbow);
	}

	/* The colour's name, beside its swatch, with a rule of its own: taking whatever the pill
	   gives it, it could not be told from the "(default)" note after it: two runs of text at
	   the same weight saying two different kinds of thing. */
	.accent-name {
		color: var(--sift-ink);
	}

	/*
	 * A FACE'S NAME, SET IN THAT FACE, on both menus and on the triggers that open them.
	 *
	 * Names in one typeface would say what each family is CALLED, which a reader already has, and
	 * not what any of them looks like, which is the whole of what the choice is about. The word is
	 * the sample.
	 *
	 * Two classes rather than one rule keyed on the attribute, and the difference is a rule of this
	 * repository rather than a style: only `app.css` may write a selector on a theme attribute, so
	 * the attribute goes on the element to re-declare the family and a plain class is what reads it.
	 * Nothing else about the row moves: the size, the weight and the ink are the list's.
	 */
	/* The row around it already clips and ellipsises (`.ui-select-item-label`), so all this owes is
	   that a name in a wide face does not wrap onto a second line inside it. */
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
