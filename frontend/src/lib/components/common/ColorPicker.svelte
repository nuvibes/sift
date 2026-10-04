<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'ColorPicker',
		category: 'control',
		role: 'a color chosen by eye: a square of one hue, a slider through the hues, and the hex it comes to',
		basis: 'composes:Slider,TextInput,Button',
		states: ['default', 'dragging', 'refused']
	} satisfies DesignEntry;

	/** A colour the chosen one turns into, drawn in the row under the box. See `swatches`. */
	export interface DerivedSwatch {
		/** What the role is called, in a word or two. */
		name: string;
		/** `#rrggbb`. */
		colour: string;
	}
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: the library has no colour control, and the parts that HAVE behaviour worth
	   borrowing are already primitives here: the hue is `Slider` (a real range input), the hex is
	   `TextInput`, the copy is `Button`. What is left is the square, which is two numbers read off a
	   pointer; there is no keyboard convention and no accessibility tree for it to get right that
	   this file is not already giving it. */

	/*
	 * Choosing a colour, in Sift's own chrome.
	 *
	 * Not `<input type="color">`: that opens a chooser in another typeface, focus ring and corner
	 * style, dropped into the middle of this interface. A square, a hue and a hex box are three
	 * primitives this app already has. The eyedropper is a separate API (`EyeDropper`) and can be
	 * added here without the operating system's furniture.
	 *
	 * Two reports. `oninput` fires on every movement and `onchange` when a movement ends, the same
	 * split a range input makes: the page follows the marker, and a drag is stored once rather than
	 * per pixel.
	 *
	 * The three numbers are held rather than derived on every render, because the hue cannot be
	 * recovered from a colour: grey has no hue and black has neither hue nor saturation, so
	 * re-reading the slider from the hex would swing it to red the moment somebody typed white. The
	 * guard in `take` and in the effect below is for that alone. The prop is otherwise read back
	 * freely: a colour this control reported comes back unchanged but for rounding to eight bits,
	 * under one step and self-correcting.
	 */
	import Button from './Button.svelte';
	import Tooltip from './Tooltip.svelte';
	import Slider from './Slider.svelte';
	import TextInput from './TextInput.svelte';
	import { copyText } from '$lib/shell/clipboard';
	import { hueColour, toHex, toHsv } from './hsv';

	interface Props {
		/** What the colour is for, read to a screen reader: "Accent colour". */
		label: string;
		/** The colour in force, `#rrggbb`. */
		value: string;
		/** Moved. Called on every step of a drag, with `#rrggbb`. For painting, never for storing. */
		oninput: (colour: string) => void;
		/** Settled: a drag let go of, an arrow key, a hex typed and left. This is the one to store. */
		onchange: (colour: string) => void;
		/**
		 * What this colour turns into, shown as a row under the box.
		 *
		 * A chosen colour is rarely used raw: Sift works an accent's fill, its text shade and its
		 * tint out of one, and each of those is what a person will actually see. Showing them here
		 * is the difference between choosing a colour and choosing what the screen will look like.
		 * Empty on a caller that uses the colour as it is, and then no row is drawn.
		 */
		swatches?: DerivedSwatch[];
	}

	let { label, value, oninput, onchange, swatches = [] }: Props = $props();

	/* How far one arrow key moves the marker, as a fraction of the square. A twentieth: fine enough
	   that a colour can be arrived at, coarse enough that crossing the square is not forty presses.
	   The hue's step is the slider's own, one degree, because the hue is a real range input. */
	const KEY_STEP = 0.05;

	/** How long the copy button shows a tick before going back to the clipboard glyph. The same two
	 *  seconds the Updates screen's copy uses, so one act looks the same in both places. */
	const COPIED_FOR = 2000;

	let hue = $state(0);
	let saturation = $state(0);
	let brightness = $state(0);

	/** The square, so a pointer's position can be measured against it. */
	let square = $state<HTMLElement | null>(null);

	/** What is in the hex box. Follows the colour in force until somebody types over it. */
	let typed = $state('');
	let refused = $state(false);
	let copied = $state(false);

	/** The error line's own id, so the box can point at it. Unique per instance: two pickers on one
	 *  screen with one id between them would have the second box describing the first one error. */
	const errorId = $props.id();

	/** The colour the three numbers come to. */
	const chosen = $derived(toHex({ hue, saturation, value: brightness }));

	/*
	 * Take a colour set somewhere else: the account's own on first load, a named accent being
	 * pressed, another window.
	 *
	 * The hue is kept when the arriving colour cannot name one, which is the whole reason the three
	 * numbers are held rather than derived. Everything else follows the value.
	 */
	$effect(() => {
		const parsed = toHsv(value);
		typed = value;
		refused = false;
		if (parsed === null) return;
		saturation = parsed.saturation;
		brightness = parsed.value;
		if (parsed.saturation > 0 && parsed.value > 0) hue = parsed.hue;
	});

	/** Say what the colour is now, and put it in the box: while a drag is running the prop is not
	 *  moving (the caller is painting, not storing), so the box has to be told by this. */
	function say(settled: boolean): void {
		const colour = toHex({ hue, saturation, value: brightness });
		typed = colour;
		refused = false;
		oninput(colour);
		if (settled) onchange(colour);
	}

	/** Where in the square a point is, as saturation across and brightness down. */
	function aimAt(event: PointerEvent): void {
		if (square === null) return;
		const box = square.getBoundingClientRect();
		if (box.width === 0 || box.height === 0) return;
		saturation = Math.min(1, Math.max(0, (event.clientX - box.left) / box.width));
		brightness = 1 - Math.min(1, Math.max(0, (event.clientY - box.top) / box.height));
	}

	/*
	 * The drag, with the pointer CAPTURED.
	 *
	 * Without capture, moving off the square mid-drag hands the events to whatever is underneath,
	 * and the popover this sits in closes on a press outside itself, so a drag that strayed one pixel
	 * over the edge would shut the picker. Capture keeps every move coming here until the button is let go,
	 * which is also what makes dragging past the edge mean "full saturation" rather than "stop".
	 */
	function grab(event: PointerEvent): void {
		square?.setPointerCapture(event.pointerId);
		aimAt(event);
		say(false);
	}

	function drag(event: PointerEvent): void {
		if (square === null || !square.hasPointerCapture(event.pointerId)) return;
		aimAt(event);
		say(false);
	}

	function letGo(event: PointerEvent): void {
		if (square === null || !square.hasPointerCapture(event.pointerId)) return;
		square.releasePointerCapture(event.pointerId);
		say(true);
	}

	/* The arrows, which are the whole keyboard story for the square: the hex box below is the
	   accessible entry and the hue is a real slider. Settled on every press rather than on release,
	   the way a range input reports both for one arrow: there is no "let go of" for a key. */
	function byKey(event: KeyboardEvent): void {
		const across = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
		const down = event.key === 'ArrowUp' ? 1 : event.key === 'ArrowDown' ? -1 : 0;
		if (across === 0 && down === 0) return;
		event.preventDefault();
		saturation = Math.min(1, Math.max(0, saturation + across * KEY_STEP));
		brightness = Math.min(1, Math.max(0, brightness + down * KEY_STEP));
		say(true);
	}

	function slideHue(next: number, settled: boolean): void {
		hue = next;
		say(settled);
	}

	/* What to do about it, rather than what went wrong: the wording rule the fields follow. The
	   colour in force stays in force, so nothing is lost by getting it wrong. */
	const ERROR = 'Type a color as a hash and six digits, using 0 to 9 and a to f.';

	/** A hex typed and left. Believed on `change` rather than on every keystroke: half of a colour is
	 *  not a colour, and refusing it while somebody is still typing is shouting at them for a word
	 *  they had not finished. */
	function take(text: string): void {
		const parsed = toHsv(text.trim().toLowerCase());
		if (parsed === null) {
			refused = true;
			return;
		}
		saturation = parsed.saturation;
		brightness = parsed.value;
		if (parsed.saturation > 0 && parsed.value > 0) hue = parsed.hue;
		say(true);
	}

	async function copy(): Promise<void> {
		// Through the helper: `navigator.clipboard` is absent on a plain-http address, which is how a
		// self-hosted Sift is normally reached.
		if (!(await copyText(chosen))) return;
		copied = true;
		setTimeout(() => (copied = false), COPIED_FOR);
	}
</script>

<div class="picker">
	<!--
		A button, because it is a thing you press and drag, and a real one is focusable, reachable by
		tab and announced as a control without any of that being rebuilt. Space and Enter do nothing
		here on purpose: there is no single action to fire, and the arrows are the keyboard answer.
	-->
	<button
		bind:this={square}
		type="button"
		class="square"
		style:--hue={hueColour(hue)}
		aria-label="{label}: saturation and brightness. Use the arrow keys, or type a color in the box below."
		onpointerdown={grab}
		onpointermove={drag}
		onpointerup={letGo}
		onkeydown={byKey}
	>
		<span
			class="marker"
			style:--across="{saturation * 100}%"
			style:--down="{(1 - brightness) * 100}%"
			style:background-color={chosen}
		></span>
	</button>

	<Slider
		label="{label}: hue"
		value={hue}
		max={359}
		ground="var(--hue-spectrum)"
		valueText="{Math.round(hue)} degrees"
		oninput={(next) => slideHue(next, false)}
		onchange={(next) => slideHue(next, true)}
	/>

	<div class="entry">
		<TextInput
			value={typed}
			invalid={refused}
			aria-label="{label}: the color as a hex code"
			describedBy={refused ? errorId : undefined}
			spellcheck={false}
			autocapitalize="off"
			autocomplete="off"
			oninput={(event) => (typed = (event.currentTarget as HTMLInputElement).value)}
			onchange={(event) => take((event.currentTarget as HTMLInputElement).value)}
		/>
		<!-- The tick replaces the glyph rather than joining it, so the row does not change width at
		     the moment somebody is looking at it. -->
		<Tooltip label={copied ? 'Copied' : 'Copy the color'} staysOnPress>
			<Button
				tone="ghost"
				icon={copied ? 'check' : 'content_copy'}
				aria-label={copied ? 'Copied' : 'Copy the color'}
				onclick={() => void copy()}
			/>
		</Tooltip>
	</div>

	{#if refused}
		<p class="refused" id={errorId} role="alert">{ERROR}</p>
	{/if}

	{#if swatches.length > 0}
		<!-- What the colour BECOMES. A row rather than a list of rows: three small samples read at a
		     glance, and reading them is the whole act. -->
		<ul class="derived">
			{#each swatches as swatch (swatch.name)}
				<li class="derived-one">
					<span class="derived-dot" style:background-color={swatch.colour}></span>
					<span class="derived-name">{swatch.name}</span>
				</li>
			{/each}
		</ul>
	{/if}
</div>

<style>
	.picker {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
	}

	/*
	 * The field. One hue across the whole square, washed white to the left and black to the bottom,
	 * so every point in it is a saturation and a brightness of that one hue.
	 *
	 * The two washes are a token: this file is not allowed to know what colour anything is, and a
	 * white and a black written here would be exactly that. `--hue` is the one part that cannot be a
	 * token, because it is the value being chosen: it arrives as a `style:` directive, which is the
	 * CSSOM rather than an inline style attribute.
	 */
	.square {
		position: relative;
		inline-size: 100%;
		block-size: var(--colour-square);
		padding: 0;
		border: 1px solid var(--sift-line);
		border-radius: var(--radius-md);
		background: var(--colour-square-wash), var(--hue);
		cursor: crosshair;
		/* The pointer is captured on the way down, and a touch that starts here must not also scroll
		   the panel it is in. */
		touch-action: none;
	}

	.square:focus-visible {
		outline: none;
		border-color: transparent;
		box-shadow: var(--focus-ring);
	}

	/*
	 * The marker: a ring with the chosen colour inside it, so what is under the finger is the thing
	 * being read rather than something covering it.
	 *
	 * TWO RINGS, and the second is not decoration. The square runs from white in one corner to black
	 * in another, so a single ring of either is invisible in one of them: a pale ring disappears
	 * into the pale corner and a dark one into the dark. The light ring is the border and the dark
	 * one is an `outline`, which draws just outside it: whichever corner the marker is in, one of
	 * the two is showing.
	 */
	.marker {
		position: absolute;
		/* Declared here as well as set by the markup, so a marker with no colour yet sits at the top
		   left rather than nowhere, and so the tokens test can see the two have a home. */
		--down: 0%;
		--across: 0%;
		inset-block-start: var(--down);
		inset-inline-start: var(--across);
		inline-size: var(--colour-marker);
		block-size: var(--colour-marker);
		border: 2px solid var(--sift-ink);
		border-radius: var(--radius-full);
		outline: 1px solid var(--sift-bg);
		transform: translate(-50%, -50%);
		pointer-events: none;
	}

	/*
	 * The box and the copy beside it. The button sits on the right of the row, where an action
	 * goes.
	 */
	.entry {
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}

	.refused {
		margin: 0;
		color: var(--sift-bad-text);
		font: var(--text-body-sm);
	}

	.derived {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-3);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.derived-one {
		display: inline-flex;
		align-items: center;
		gap: var(--space-1);
	}

	.derived-dot {
		inline-size: var(--space-3);
		block-size: var(--space-3);
		border: 1px solid var(--sift-line-strong);
		border-radius: var(--radius-full);
	}

	.derived-name {
		color: var(--sift-ink-3);
		font: var(--text-micro);
	}
</style>
