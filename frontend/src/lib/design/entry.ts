/* What every shared component says about itself, in its own file: read by the gallery, the
 * barrel test and `check_design_entries.js`, so no second list of the primitives can drift. */

/** Where a component sits in the reference, in the order the reference shows them. */
export const CATEGORIES = ['primitive', 'control', 'surface', 'composition', 'shell'] as const;
export type DesignCategory = (typeof CATEGORIES)[number];

/** What a component is built on. One or more of these, separated by `; `. */

export interface DesignEntry {
	readonly name: string;
	readonly category: DesignCategory;
	readonly role: string;
	readonly basis: string;
	readonly states?: readonly string[];
}

/** The anchor a component's specimen wears on the gallery: its name, lower-cased. */
export const specimenId = (name: string): string => name.toLowerCase();

/** The one spelling of each basis part. Exported so the gate and the type agree. */
export const BASIS_PART =
	/^(bits-ui:[A-Z][A-Za-z]+|site:<[a-z]+(?: [^>]+)?>|composes:[A-Z][A-Za-z]+(?:,[A-Z][A-Za-z]+)*|own)$/;
