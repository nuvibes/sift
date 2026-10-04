/*
 * Putting text on the clipboard, on the connection Sift is actually reached over.
 *
 * `navigator.clipboard` exists only in a **secure context** (HTTPS, or localhost). Sift is a
 * self-hosted server on a home network and is reached over plain http at a LAN address by design,
 * which is not a secure context: the whole `clipboard` object is simply absent there, so without a
 * fallback every Copy in the application would fail for anybody not sitting at the machine itself.
 *
 * So there is a fallback, and it is the old one on purpose. `document.execCommand('copy')` is
 * deprecated and every browser still implements it, because the web has no other way to do this
 * without HTTPS. It needs a real element carrying the text and a real selection, which is what the
 * scaffolding below is; it is torn down immediately either way.
 */

import { bridge } from '$lib/bridge';
import { stampForAFileName } from '$lib/shell/when';

/**
 * Copy text. True if it landed on the clipboard.
 *
 * Never throws: the caller's job is to say whether it worked, not to handle two kinds of failure.
 *
 * The desktop bridge carries `readClipboard` and `canReadClipboard` and nothing that writes: no
 * channel in `desktop/src/verbs.ts`, no verb in `preload.ts`, no method on `$lib/bridge`. So
 * there is no shell path to prefer here. Electron's own write would need neither a secure context
 * nor the focus of the page, so a bridge verb for it would skip the textarea below entirely; the
 * note on that textarea says what the focus costs.
 */
export async function copyText(text: string): Promise<boolean> {
	if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
		try {
			await navigator.clipboard.writeText(text);
			return true;
		} catch {
			// Present but refused: a permission the person declined, or a document without focus.
			// The fallback below can still succeed, so fall through rather than give up.
		}
	}
	return legacyCopy(text);
}

function legacyCopy(text: string): boolean {
	if (typeof document === 'undefined') return false;
	const holder = document.createElement('textarea');
	holder.value = text;
	// Off-screen rather than hidden: `display: none` and `visibility: hidden` are both unselectable,
	// and a selection is what the command copies. Read-only so a mobile keyboard does not appear,
	// and `position: fixed` so adding it cannot scroll the page.
	holder.setAttribute('readonly', '');
	holder.style.position = 'fixed';
	holder.style.top = '-1000px';
	holder.style.opacity = '0';
	/*
	 * WHO HAD THE FOCUS, because this is about to take it and nobody asked it to.
	 *
	 * `select()` moves focus onto the textarea, which is the whole mechanism: a selection is what
	 * `execCommand('copy')` copies. What is not part of the mechanism is where focus is left
	 * AFTERWARDS: removing the holder drops it on `<body>`, so a control that copies something
	 * would end up having quietly deselected itself.
	 *
	 * That is not cosmetic. Pressing the file name in the popout would fire focusout on the name,
	 * focusin on this textarea, focusout on it, and finish with `activeElement` on `<body>`. The
	 * tooltip over the name hides on `focusout`, so the label would be torn down by the copy itself
	 * and the word "Copied" (the only thing that says the press worked) would never appear. The
	 * borrower gives it back.
	 *
	 * Read BEFORE the holder is added, or this reads the holder on a second call.
	 */
	const had = document.activeElement;
	document.body.appendChild(holder);
	try {
		holder.select();
		holder.setSelectionRange(0, text.length);
		return document.execCommand('copy');
	} catch {
		return false;
	} finally {
		holder.remove();
		// Only to something that can take it, and only where it is still on the page: a control
		// inside a menu that closed on the press is gone by now, and focusing a detached element
		// throws nothing and does nothing but leave `activeElement` on `<body>` anyway.
		if (had instanceof HTMLElement && had.isConnected) had.focus({ preventScroll: true });
	}
}

/* --- Reading it -------------------------------------------------------------------------- */

/**
 * Whether a "Paste" BUTTON can work here. Ctrl-V is a separate question and always works.
 *
 * The distinction matters and it is the whole reason this is capability-gated. A paste EVENT
 * carries its own data, so Ctrl-V needs no permission and no secure context: it works over plain
 * http on a LAN, which is how most people reach a self-hosted Sift. Reading the clipboard without
 * an event does not: `navigator.clipboard.read` exists only in a secure context, so in a browser on
 * a plain-http LAN there is nothing to put behind a button. The desktop client asks the operating
 * system directly and therefore always can.
 */
export function canReadClipboard(): boolean {
	if (bridge.canReadClipboard()) return true;
	return (
		typeof window !== 'undefined' &&
		window.isSecureContext === true &&
		typeof navigator !== 'undefined' &&
		typeof navigator.clipboard?.read === 'function'
	);
}

/** What was on the clipboard, in the shapes Sift can take in. Both may be empty. */
export interface Pasted {
	text: string;
	file: File | null;
}

/* A raw bitmap is the ONE case with no name to keep.
 *
 * Everything else that arrives (a dropped file, a copied file, an upload) carries the name it
 * had, and it keeps it. A screenshot on the clipboard is bytes and nothing else, so this is the
 * only place a name is invented, and it is a timestamp rather than a counter: two of them a day
 * apart sort in the order they were taken, which "Pasted (2)" does not.
 */
function bitmapName(when: Date): string {
	return `Pasted ${stampForAFileName(when)}.png`;
}

/** Read the clipboard the best way this shell has. Never throws; an empty answer is ordinary. */
export async function readClipboard(): Promise<Pasted> {
	const native = await bridge.readClipboard();
	if (native !== null) {
		return {
			text: native.text,
			file: native.image === null ? null : fileFromDataUrl(native.image)
		};
	}
	return browserClipboard();
}

async function browserClipboard(): Promise<Pasted> {
	if (!canReadClipboard()) return { text: '', file: null };
	try {
		const items = await navigator.clipboard.read();
		let text = '';
		for (const item of items) {
			const imageType = item.types.find((type) => type.startsWith('image/'));
			if (imageType !== undefined) {
				const blob = await item.getType(imageType);
				return { text: '', file: named(blob) };
			}
			if (item.types.includes('text/plain'))
				text = (await (await item.getType('text/plain')).text()).trim();
		}
		return { text, file: null };
	} catch {
		/* Present but refused: a permission somebody declined, or a document without focus. The
		 * caller says "nothing to add", which is what it looks like from the outside anyway. */
		return { text: '', file: null };
	}
}

/* Decoded here rather than fetched.
 *
 * `fetch(dataUrl)` is the obvious way to turn a data URL into bytes, and it cannot work in this
 * application: Sift's own Content-Security-Policy sets `default-src 'self'`, `connect-src`
 * inherits it, and a data URL is not `'self'`, so every fetch of one is refused before it
 * starts. `img-src` allows `data:`, which makes it look fine; nothing in the app draws this one.
 * The catch below would answer null, and the caller would read that as an empty clipboard.
 *
 * Decoding in place is not a workaround for the policy, it is the better call regardless: the
 * bytes are already here, so a round trip through the network stack buys nothing. Widening
 * `connect-src` to allow `data:` would fix one paste by loosening the rule that governs every
 * request the application makes.
 */
function fileFromDataUrl(dataUrl: string): File | null {
	try {
		const comma = dataUrl.indexOf(',');
		if (comma === -1) return null;
		const header = dataUrl.slice(0, comma);
		if (!header.endsWith(';base64')) return null;
		const type = header.slice('data:'.length, -';base64'.length) || 'image/png';
		const binary = atob(dataUrl.slice(comma + 1));
		const bytes = new Uint8Array(binary.length);
		for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
		return named(new Blob([bytes], { type }));
	} catch {
		// A truncated or malformed URL. The caller reads null as "no picture", which is right.
		return null;
	}
}

function named(blob: Blob): File {
	return new File([blob], bitmapName(new Date()), { type: blob.type || 'image/png' });
}
