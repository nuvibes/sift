// SPDX-License-Identifier: AGPL-3.0-or-later
/* The shapes that cross the process boundary, declared once.
 *
 * The desktop shell and the page it serves are two separately compiled trees (two tsconfigs, two
 * targets, two package.jsons), and every verb the shell offers the page answers with a structure
 * that both sides have to agree about. Written out in both trees, that agreement is a convention:
 * a hand-written copy does not FAIL when the other side moves, it goes quietly wrong. The shell
 * renames a field, the page reads `undefined` where a boolean used to be, `undefined` is falsey,
 * and a screen says the opposite of what the shell answered.
 *
 * This file is the agreement instead. It belongs to NEITHER tree, which is what makes it usable by
 * both: the shell's build does not become a dependency of the page's, and the page still builds for
 * the plain browser, where there is no shell at all.
 *
 * ## Why a declaration file and not a generated one
 *
 * The HTTP boundary generates `schema.d.ts` from `openapi.json`, because the server already
 * produces that schema at runtime and generating it is free. There is no equivalent producer here:
 * an IPC channel's shape is only ever stated in TypeScript, so generating this would mean choosing
 * one tree as the source, making the other's typecheck wait on a build step, and adding an artefact
 * that can be stale. One declaration both sides read costs none of that.
 *
 * ## Three facts this relies on
 *
 * `rootDir: "src"` in the shell's tsconfig does not reject a declaration outside it: `rootDir`
 * constrains what is EMITTED, and a `.d.ts` is never emitted. Compiled with `--outDir` to a scratch
 * folder, the output is the same flat tree, with nothing added.
 *
 * `import type` is erased before the page is bundled, so nothing here reaches the browser at all.
 * There is no runtime module behind this path and there must never be one.
 *
 * And where a type could be either named or spelled out, the structure is what is written here,
 * because a name would drag its module across the boundary with it.
 */

/**
 * What came of answering one of the first-run questions.
 *
 * Three outcomes and not two, because "they closed the folder picker" is neither a failure nor an
 * answer. `ok` false with nothing to say leaves the screen exactly as it was; with a sentence it
 * shows it; `ok` true means the shell is already navigating and the screen is about to go.
 */
export interface Settled {
  ok: boolean;
  refusal: string | null;
}

/**
 * Where taking a library folder has got, pushed while it runs: the folder being checked, then Sift
 * being started there (the slow part on a first start, which sets up the database). Opening the
 * library is the window leaving the screen, which says itself.
 */
export type SetupStep = "checking" | "starting";

/**
 * One library this copy of Sift has opened. The database folder is its identity and what the
 * backend is given; the cache folder goes with it so switching back restores the pair rather
 * than half; the name is the folder holding both, which is what somebody named.
 */
export interface KnownLibrary {
  dataDir: string;
  cacheDir: string;
  name: string;
  /** When it was last opened, in milliseconds, so the list reads newest first. */
  lastOpened: number;
}

/** Every library this copy has opened, and which of them this window is looking at. */
export interface LibraryList {
  current: string | null;
  libraries: KnownLibrary[];
}

/**
 * An area of the page in its own CSS pixels, as `getBoundingClientRect` gives one: what the page
 * asks the shell to photograph, or null for the whole window.
 */
export interface CaptureArea {
  x: number;
  y: number;
  width: number;
  height: number;
}
