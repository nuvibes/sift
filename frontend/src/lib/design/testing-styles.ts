/* Support for the tests, and only for the tests.
 *
 * A component's own stylesheet, put into the document a test mounted it in.
 *
 * The unit environment loads no component CSS, so `getComputedStyle` there knows nothing a
 * `<style>` block says. A rule that lands on the wrong element, or loses to another component's
 * rule, is only visible with both sets of rules in place, so a test asking what an element looks
 * like compiles each stylesheet itself and adds it here.
 *
 * Nothing in the app imports this, and it is not in the bundle.
 */

import { compile } from 'svelte/compiler';

const compiled = new Map<string, string>();
const added: HTMLStyleElement[] = [];

/**
 * Add the stylesheet `source` compiles to, as the last one in the document.
 *
 * The scoping class the compiler writes comes from the file name it is given, which is not the name
 * the test run compiled the mounted component under. So `scope`, an element the mounted component
 * drew itself, names the class its scoped rules must carry to land on it. Without a `scope` the
 * scoped rules match nothing, which is what a test asking only where the GLOBAL rules land wants.
 */
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
