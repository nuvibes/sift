/*
 * What every shared component says about itself, in its own file.
 *
 * ## Why a declaration and not a list
 *
 * A hand-typed index of the gallery's sections beside the sections themselves drifts with nothing
 * to say so, and so do a barrel (`common/index.ts`) and a folder each naming their own set: three
 * lists of "the primitives", each edited while looking at something other than the primitive.
 *
 * So the primitive says it itself. One typed object, exported from the component's module script,
 * read by three things: the gallery (which draws the index and each specimen's header from it), the
 * barrel test (which refuses an entry the barrel does not export), and `check_design_entries.js`
 * (which refuses a shared component without one, two components claiming one role, and an entry
 * the gallery does not draw). A judgement written where the component is, not in a registry:
 * the same reason `WHY NOT BITS-UI:` is a line in the file.
 *
 * ## The fields
 *
 * `name` is the code name, as imported. `category` is where it sits in the reference. `role` is
 * one sentence saying what it is FOR, and it is the duplicate detector: two components with the
 * same role are one component written twice. `basis` says what it is built on (the library, the
 * site element, other Sift primitives, or its own shape and colour), in a spelling a gate can
 * check against the file's imports. `states` are the looks the gallery must draw.
 */

/** Where a component sits in the reference, in the order the reference shows them. */
export const CATEGORIES = ['primitive', 'control', 'surface', 'composition', 'shell'] as const;
export type DesignCategory = (typeof CATEGORIES)[number];

/**
 * What a component is built on.
 *
 * One or more of these, separated by `; `. `bits-ui:Name` names a namespace the library exports
 * and requires the file to import it; `site:<tag>` names the element the browser owns;
 * `composes:A,B` names Sift primitives one layer down; `own` is shape and colour with no behaviour
 * to borrow. `BASIS_PART` below is the spelling, and the gate imports it rather than copying it.
 */

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
