/*
 * Putting text on the clipboard over plain http, where `navigator.clipboard` does not exist: the
 * fallback is `execCommand('copy')` on a temporary textarea, torn down immediately.
 */

import { bridge } from '$lib/bridge';
import { stampForAFileName } from '$lib/shell/when';

/** True if it landed; never throws. The desktop bridge has no clipboard write to prefer. */
export async function copyText(text: string): Promise<boolean> {
	if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
		try {
			await navigator.clipboard.writeText(text);
			return true;
		} catch {
			// Present but refused; the fallback can still succeed.
		}
	}
	return legacyCopy(text);
}

function legacyCopy(text: string): boolean {
	if (typeof document === 'undefined') return false;
	const holder = document.createElement('textarea');
	holder.value = text;
	// Off-screen, not hidden, so it can be selected; read-only so no mobile keyboard appears.
	holder.setAttribute('readonly', '');
	holder.style.position = 'fixed';
	holder.style.top = '-1000px';
	holder.style.opacity = '0';
	/*
	 * WHO HAD THE FOCUS, given back afterwards: removing the holder would leave focus on `<body>`
	 * and tear down the tooltip that says "Copied". Read BEFORE the holder is added.
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
		// Only to something still on the page.
		if (had instanceof HTMLElement && had.isConnected) had.focus({ preventScroll: true });
	}
}

/* --- Reading it */

/**
 * Whether a Paste BUTTON can work here: reading without a paste event needs a secure context, or
 * the desktop client. Ctrl-V always works.
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

export interface Pasted {
	text: string;
	file: File | null;
}

/* A raw bitmap is the one case with no name, so a timestamp, which sorts by when it was taken. */
function bitmapName(when: Date): string {
	return `Pasted ${stampForAFileName(when)}.png`;
}

/** Never throws; an empty answer is ordinary. */
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
		return { text: '', file: null };
	}
}

/*
 * Decoded here: the content policy refuses `fetch` of a data URL, and the bytes are already here.
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
		return null;
	}
}

function named(blob: Blob): File {
	return new File([blob], bitmapName(new Date()), { type: blob.type || 'image/png' });
}
