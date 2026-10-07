/* SPDX-License-Identifier: AGPL-3.0-or-later */
/*
 * Whether this browser draws a phone's HEIC, asked once per tab: of `ImageDecoder` where there is
 * one, else by drawing an eight-pixel sample held here (plain http withholds the decoder), so no
 * request is spent. A no draws the copy immediately; with no answer the original is tried first.
 */

/** `undefined` until asked, `null` where the browser cannot say. */
let known: boolean | null | undefined;
let asking: Promise<boolean | null> | null = null;

/** The answer so far, without asking. */
export function drawsHeicNow(): boolean | null | undefined {
	return known;
}

/** Ask, once; every caller after the first shares the one answer. */
export function drawsHeic(): Promise<boolean | null> {
	asking ??= (async () => {
		const decoder = globalThis.ImageDecoder;
		let answer: boolean | null = null;
		if (decoder !== undefined && typeof decoder.isTypeSupported === 'function') {
			try {
				answer = await decoder.isTypeSupported('image/heic');
			} catch {
				answer = null;
			}
		}
		answer ??= await drawsSample();
		known = answer;
		return answer;
	})();
	return asking;
}

/** An eight-pixel HEIC, so a browser can be asked by drawing one. */
const SAMPLE = [
	'AAAAHGZ0eXBoZWljAAAAAG1pZjFoZWljbWlhZgAAAXxtZXRhAAAAAAAAACFoZGxyAAAAAAAAAABwaWN0AAAAAAAA',
	'AAAAAAAAAAAAACJpbG9jAAAAAERAAAEAAQAAAAABoAABAAAAAAAAACYAAAAjaWluZgAAAAAAAQAAABVpbmZlAgAA',
	'AAABAABodmMxAAAAAA5waXRtAAAAAAABAAAA/GlwcnAAAADcaXBjbwAAAHVodmNDAQNwAAAAAAAAAAAAHvAA/P34',
	'+AAADwNgAAEAGEABDAH//wNwAAADAJAAAAMAAAMAHroCQGEAAQApQgEBA3AAAAMAkAAAAwAAAwAeoCCBBZbqrprm',
	'4CGgwIAAAAyAAAADAIRiAAEABkQBwXPBiQAAABNjb2xybmNseAABAA0ABoAAAAAUaXNwZQAAAAAAAABAAAAAQAAA',
	'AChjbGFwAAAACAAAAAEAAAAIAAAAAf///8gAAAAC////yAAAAAIAAAAQcGl4aQAAAAADCAgIAAAAGGlwbWEAAAAA',
	'AAAAAQABBYECAwWEAAAALm1kYXQAAAAiKAGvBBITTOD46cJoP9Kn+SZV668n7asr40mfd7EYkqn/oA=='
].join('');

const SAMPLE_WAIT_MS = 2000;

/** True drawn, false refused, null no answer in time. */
function drawsSample(): Promise<boolean | null> {
	if (typeof Image === 'undefined') return Promise.resolve(null);
	return new Promise((settle) => {
		const picture = new Image();
		const waited = setTimeout(() => settle(null), SAMPLE_WAIT_MS);
		const end = (answer: boolean) => {
			clearTimeout(waited);
			settle(answer);
		};
		picture.onload = () => end(picture.naturalWidth > 0);
		picture.onerror = () => end(false);
		picture.src = `data:image/heic;base64,${SAMPLE}`;
	});
}

/** Forget the answer, for a test that stands in another browser. */
export function forgetHeic(): void {
	known = undefined;
	asking = null;
}
