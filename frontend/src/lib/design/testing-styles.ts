/* Support for the tests, and only for the tests. */

import { compile } from 'svelte/compiler';

const compiled = new Map<string, string>();
const added: HTMLStyleElement[] = [];

/** Add the stylesheet `source` compiles to, as the last one in the document. */
export function applyStyles(source: string, scope?: Element | null): void {
	let css = compiled.get(source);
	if (css === undefined) {
		css = compile(source, { filename: 'Styled.svelte', css: 'external' }).css?.code ?? '';
		compiled.set(source, css);
	}
	if (!css) throw new Error('the component compiled to no stylesheet');

	let text = css;
	if (scope !== undefined) {
		const mounted = [...(scope?.classList ?? [])].find((name) => /^svelte-[a-z0-9]+$/.test(name));
		if (!mounted) {
			const named = scope ? `<${scope.localName} class="${scope.className}">` : 'nothing';
			throw new Error(`the scope given carries no scoping class: ${named}`);
		}
		const written = css.match(/svelte-[a-z0-9]+/)?.[0];
		if (written) text = css.replaceAll(written, mounted);
	}

	const sheet = document.createElement('style');
	sheet.textContent = text;
	document.head.append(sheet);
	added.push(sheet);
}

/** Take every stylesheet `applyStyles` added out of the document again. */
export function removeStyles(): void {
	for (const sheet of added.splice(0)) sheet.remove();
}
