/* The libraries this copy has opened, and what is asked of a folder before it is opened. */

import { beforeEach, describe, expect, it, vi } from "vitest";

/* The runner of last resort really starts a Python. */
const ran: {
  file: string;
  args: string[];
  options: Record<string, unknown>;
}[] = [];
const answer = { error: null as Error | null, stdout: "" };
vi.mock("node:child_process", () => ({
  execFile: (
    file: string,
    args: string[],
    options: Record<string, unknown>,
    done: (error: Error | null, stdout: string) => void,
  ) => {
    ran.push({ file, args, options });
    done(answer.error, answer.stdout);
  },
}));

import { resetElectronStub } from "../test/electron-stub";
import { INTERPRETER_ARGS } from "./backend";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

import {
  ADOPT_FLAG,
  BACK_UP_FLAG,
  DATABASE_FILENAME,
  INSPECT_FLAG,
  adoptDatabase,
  backUpLibrary,
  find,
  forgotten,
  inspectDatabase,
  inspectLibrary,
  locationsUnder,
  LIBRARIES_FOLDER,
  LIBRARIES_MARK,
  nameFor,
  OPENS_AT_START,
  openingAtStart,
  planForDatabase,
  remembered,
  sameFolder,
  SWITCH_NOTE,
  takeSwitchNote,
  unmakeEmpty,
  type Runner,
} from "./libraries";
import type { KnownLibrary } from "./settings";

beforeEach(() => {
  resetElectronStub();
});

const HERE = {
  dataDir: "C:\\Libraries\\Everyday\\data",
  cacheDir: "C:\\Libraries\\Everyday\\cache",
};
const THERE = { dataDir: "D:\\Archive\\data", cacheDir: "D:\\Archive\\cache" };

function answering(answer: Record<string, unknown> | null): {
  run: Runner;
  asked: string[][];
} {
  const asked: string[][] = [];
  return {
    asked,
    run: async (args: string[]) => {
      asked.push(args);
      return answer;
    },
  };
}

describe("the list of libraries", () => {
  it("puts the one just opened at the front", () => {
    const first = remembered([], HERE, 1);
    const second = remembered(first, THERE, 2);
    expect(second.map((one) => one.dataDir)).toEqual([
      THERE.dataDir,
      HERE.dataDir,
    ]);
  });

  it("keeps one entry per folder rather than one per opening", () => {
    const list = remembered(
      remembered(remembered([], HERE, 1), THERE, 2),
      HERE,
      3,
    );
    expect(list).toHaveLength(2);
    expect(list[0]?.lastOpened).toBe(3);
  });

  /* Windows is case-insensitive, so a comparison that said otherwise would put one library in the
   * list twice and then offer to switch to the one already open. */
  it("treats a differently spelled path as the same folder", () => {
    const list = remembered(
      remembered([], HERE, 1),
      { ...HERE, dataDir: "c:\\LIBRARIES\\everyday\\DATA" },
      2,
    );
    expect(list).toHaveLength(1);
    expect(sameFolder("C:\\a\\b\\", "c:/A/B")).toBe(true);
    expect(sameFolder("C:\\a\\b", "C:\\a\\c")).toBe(false);
  });

  it("remembers the cache folder beside the data one, so switching back restores the pair", () => {
    expect(remembered([], THERE, 1)[0]?.cacheDir).toBe(THERE.cacheDir);
  });

  it("calls a library after the folder holding it, never after data", () => {
    expect(nameFor("C:\\Libraries\\Everyday\\data")).toBe("Everyday");
    expect(remembered([], HERE, 1)[0]?.name).toBe("Everyday");
  });

  it("keeps the name it already had, so a later opening cannot rename it", () => {
    const named: KnownLibrary[] = [
      { ...HERE, name: "The one with the birds in", lastOpened: 1 },
    ];
    expect(remembered(named, HERE, 2)[0]?.name).toBe(
      "The one with the birds in",
    );
  });

  it("takes one off the list and leaves the rest", () => {
    const list = remembered(remembered([], HERE, 1), THERE, 2);
    expect(forgotten(list, HERE.dataDir).map((one) => one.dataDir)).toEqual([
      THERE.dataDir,
    ]);
  });

  it("finds an entry by its folder, and answers null for one it has never opened", () => {
    const list = remembered([], HERE, 1);
    expect(find(list, HERE.dataDir)?.cacheDir).toBe(HERE.cacheDir);
    expect(find(list, THERE.dataDir)).toBeNull();
  });

  it("makes the same pair of folders under a chosen root that first run makes", () => {
    expect(locationsUnder("D:\\Archive")).toEqual(THERE);
  });
});

describe("asking what is in a folder", () => {
  it("asks the backend about the data folder, by the flag the backend declares", async () => {
    const { run, asked } = answering({ verdict: "current", detail: "" });
    await inspectLibrary(HERE, run);
    expect(asked).toEqual([[INSPECT_FLAG, HERE.dataDir]]);
  });

  it("passes each verdict through as the backend said it", async () => {
    for (const verdict of [
      "empty",
      "current",
      "older",
      "newer",
      "unreadable",
    ]) {
      const { run } = answering({ verdict, detail: "something" });
      expect((await inspectLibrary(HERE, run)).verdict).toBe(verdict);
    }
  });

  /* A THIRD OUTCOME, not folded into `unreadable`, and the difference is the whole point:
   * `unreadable` is a folder that is not a library, and `unknown` is Sift failing to look. */
  it("answers unknown when the question could not be put at all", async () => {
    const { run } = answering(null);
    expect((await inspectLibrary(HERE, run)).verdict).toBe("unknown");
  });

  it("answers unknown for a word this shell has never heard of", async () => {
    const { run } = answering({ verdict: "positively baroque" });
    expect((await inspectLibrary(HERE, run)).verdict).toBe("unknown");
  });

  it("carries the sentence the backend wrote, and an empty one where there is none", async () => {
    expect(
      (
        await inspectLibrary(
          HERE,
          answering({ verdict: "newer", detail: "go on then" }).run,
        )
      ).detail,
    ).toBe("go on then");
    expect(
      (await inspectLibrary(HERE, answering({ verdict: "newer" }).run)).detail,
    ).toBe("");
  });
});

describe("the copy made before an upgrade", () => {
  it("asks by the other flag, and answers where the copy went", async () => {
    const { run, asked } = answering({
      ok: true,
      copy: "D:\\Archive\\data\\sift-before-upgrade.sqlite3",
    });
    expect(await backUpLibrary(THERE, run)).toBe(
      "D:\\Archive\\data\\sift-before-upgrade.sqlite3",
    );
    expect(asked).toEqual([[BACK_UP_FLAG, THERE.dataDir]]);
  });

  /* Null is a REFUSAL to the caller, not a detail. */
  it("answers null when the copy could not be made, however the failure arrived", async () => {
    expect(await backUpLibrary(THERE, answering(null).run)).toBeNull();
    expect(
      await backUpLibrary(
        THERE,
        answering({ ok: false, detail: "no room" }).run,
      ),
    ).toBeNull();
    expect(await backUpLibrary(THERE, answering({ ok: true }).run)).toBeNull();
  });
});

/* How the question is actually put, when nobody hands in a runner. */
describe("the runner of last resort", () => {
  beforeEach(() => {
    ran.length = 0;
    answer.error = null;
    answer.stdout = "";
  });

  it("runs the same interpreter the backend is started with, isolated and unbuffered", async () => {
    answer.stdout = '{"verdict": "current", "detail": ""}';

    await inspectLibrary(HERE);

    expect(ran[0]?.args.slice(0, INTERPRETER_ARGS.length)).toEqual([
      ...INTERPRETER_ARGS,
    ]);
    expect(ran[0]?.args.slice(-4)).toEqual([
      "-m",
      "sift.main",
      INSPECT_FLAG,
      HERE.dataDir,
    ]);
  });

  /* No window, and a timeout: a folder on a network share that has gone away must not leave
     somebody looking at a dialog that never arrives. */
  it("hides the console window and gives up rather than waiting for ever", async () => {
    answer.stdout = '{"verdict": "empty", "detail": ""}';

    await inspectLibrary(HERE);

    expect(ran[0]?.options.windowsHide).toBe(true);
    expect(ran[0]?.options.timeout).toBeGreaterThan(0);
  });

  it("reads the last line, so anything printed before the answer does not spoil it", async () => {
    answer.stdout =
      "DeprecationWarning: something in a dependency\n\n" +
      '{"verdict": "older", "detail": "one step behind"}\n';

    expect(await inspectLibrary(HERE)).toEqual({
      verdict: "older",
      detail: "one step behind",
    });
  });

  /* "I could not look" rather than "that is not a library". */
  it("answers unknown when the interpreter could not be run at all", async () => {
    answer.error = new Error("no such file");
    answer.stdout = "";

    expect(await inspectLibrary(HERE)).toEqual({
      verdict: "unknown",
      detail: "",
    });
  });

  it("answers unknown when nothing was printed at all", async () => {
    expect(await inspectLibrary(HERE)).toEqual({
      verdict: "unknown",
      detail: "",
    });
  });

  it("answers unknown when what came back is not the JSON it promised", async () => {
    answer.stdout = "Traceback (most recent call last):\n  it fell over\n";

    expect(await inspectLibrary(HERE)).toEqual({
      verdict: "unknown",
      detail: "",
    });
  });

  /* Valid JSON that is not an object. `null` and a bare number both parse, and reading a verdict
     off either would be reading a property of something that has none. */
  it("answers unknown for JSON that is not an answer", async () => {
    answer.stdout = "5";

    expect(await inspectLibrary(HERE)).toEqual({
      verdict: "unknown",
      detail: "",
    });
  });

  it("puts the backup question the same way, and answers where the copy went", async () => {
    answer.stdout =
      '{"ok": true, "copy": "C:\\\\Libraries\\\\Everyday\\\\data\\\\backup.db"}';

    expect(await backUpLibrary(HERE)).toBe(
      "C:\\Libraries\\Everyday\\data\\backup.db",
    );
    expect(ran[0]?.args.slice(-2)).toEqual([BACK_UP_FLAG, HERE.dataDir]);
  });
});

describe("what choosing a database file means", () => {
  it("opens a library's own database where it is, with the cache Sift keeps beside it", () => {
    expect(planForDatabase("D:\\Archive\\data\\sift.sqlite3", [])).toEqual({
      kind: "library",
      locations: THERE,
    });
    /* Windows names are not case-sensitive, so neither is this. */
    expect(planForDatabase("D:\\Archive\\data\\SIFT.SQLITE3", []).kind).toBe(
      "library",
    );
  });

  it("keeps the cache this copy used before, for a library it has opened", () => {
    const known = remembered(
      [],
      { dataDir: "E:\\Kept", cacheDir: "F:\\Previews" },
      1,
    );
    expect(planForDatabase("E:\\Kept\\sift.sqlite3", known)).toEqual({
      kind: "library",
      locations: { dataDir: "E:\\Kept", cacheDir: "F:\\Previews" },
    });
  });

  /* A folder not called `data` is one somebody chose, and the folder above it could be a whole
   * drive. The cache goes beside the folder, named after it, never loose in the drive's root. */
  it("never spills a cache into the folder above one somebody chose", () => {
    expect(planForDatabase("E:\\Kept\\sift.sqlite3", [])).toEqual({
      kind: "library",
      locations: { dataDir: "E:\\Kept", cacheDir: "E:\\Kept-cache" },
    });
  });

  /* A backup copy is the file somebody kept to go back to, so it is never opened
   * (and so never migrated) in place: a library folder named after it is made beside it. */
  it("makes a library folder beside any other database, named after the file", () => {
    const file = "D:\\Backups\\sift-before-upgrade-20260901-120000.sqlite3";
    expect(planForDatabase(file, [])).toEqual({
      kind: "adopt",
      source: file,
      stem: "sift-before-upgrade-20260901-120000",
      root: "D:\\Backups\\sift-before-upgrade-20260901-120000",
      locations: {
        dataDir: "D:\\Backups\\sift-before-upgrade-20260901-120000\\data",
        cacheDir: "D:\\Backups\\sift-before-upgrade-20260901-120000\\cache",
      },
    });
  });

  it("refuses a file that is not a database, whatever the picker was typed into", () => {
    const plan = planForDatabase("D:\\Backups\\holiday.jpg", []);
    expect(plan.kind).toBe("refused");
  });
});

describe("asking about a file and copying it in", () => {
  it("asks about the file itself, by the same flag as a folder", async () => {
    const { run, asked } = answering({ verdict: "older", detail: "" });
    expect(
      (await inspectDatabase("D:\\Backups\\given.sqlite3", run)).verdict,
    ).toBe("older");
    expect(asked).toEqual([[INSPECT_FLAG, "D:\\Backups\\given.sqlite3"]]);
  });

  it("copies by the adopt flag, naming the file and the new data folder", async () => {
    const { run, asked } = answering({
      ok: true,
      database: "D:\\Archive\\data\\sift.sqlite3",
    });
    expect(await adoptDatabase("D:\\given.sqlite3", THERE, run)).toBe(
      "D:\\Archive\\data\\sift.sqlite3",
    );
    expect(asked).toEqual([[ADOPT_FLAG, "D:\\given.sqlite3", THERE.dataDir]]);
  });

  it("answers null when the copy was not made, however the failure arrived", async () => {
    expect(
      await adoptDatabase("D:\\given.sqlite3", THERE, answering(null).run),
    ).toBeNull();
    expect(
      await adoptDatabase(
        "D:\\given.sqlite3",
        THERE,
        answering({ ok: false }).run,
      ),
    ).toBeNull();
    expect(
      await adoptDatabase(
        "D:\\given.sqlite3",
        THERE,
        answering({ ok: true }).run,
      ),
    ).toBeNull();
  });
});

describe("taking back the folders a failed copy made", () => {
  it("removes them when empty and leaves one that holds anything", () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "sift-adopt-"));
    const where = locationsUnder(root);
    fs.mkdirSync(where.dataDir);
    fs.mkdirSync(where.cacheDir);

    fs.writeFileSync(path.join(where.dataDir, "somebody.txt"), "kept");
    unmakeEmpty(root, where);
    expect(fs.existsSync(path.join(where.dataDir, "somebody.txt"))).toBe(true);
    expect(fs.existsSync(where.cacheDir)).toBe(false);

    fs.rmSync(path.join(where.dataDir, "somebody.txt"));
    unmakeEmpty(root, where);
    expect(fs.existsSync(root)).toBe(false);
  });
});

/* The note a backend leaves when a switch was asked for on the page. */
describe("the switch note", () => {
  function folderWith(note: string | null): string {
    const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "sift-note-"));
    if (note !== null)
      fs.writeFileSync(path.join(dataDir, SWITCH_NOTE), note, "utf8");
    return dataDir;
  }

  it("names the library to start on, and is gone once read", () => {
    const target = path.join(os.tmpdir(), "libraries", "Work");
    const dataDir = folderWith(
      JSON.stringify({
        data_dir: path.join(target, "data"),
        cache_dir: path.join(target, "cache"),
      }),
    );
    expect(takeSwitchNote(dataDir)).toEqual({
      dataDir: path.join(target, "data"),
      cacheDir: path.join(target, "cache"),
    });
    expect(fs.existsSync(path.join(dataDir, SWITCH_NOTE))).toBe(false);
    /* Taken once: the next restart of this library is an ordinary one. */
    expect(takeSwitchNote(dataDir)).toBeNull();
  });

  it("is no switch when there is no note", () => {
    expect(takeSwitchNote(folderWith(null))).toBeNull();
  });

  it.each([
    ["not JSON", "switch to Work"],
    [
      "a relative folder",
      JSON.stringify({ data_dir: "Work\\data", cache_dir: "Work\\cache" }),
    ],
    [
      "a missing folder",
      JSON.stringify({ data_dir: path.join(os.tmpdir(), "x") }),
    ],
  ])("is no switch when it is %s, and is still taken away", (_what, note) => {
    const dataDir = folderWith(note);
    expect(takeSwitchNote(dataDir)).toBeNull();
    expect(fs.existsSync(path.join(dataDir, SWITCH_NOTE))).toBe(false);
  });
});

/* THE PIN the comment beside the flags promises. */
describe("the words the backend answers to", () => {
  const REPO = path.resolve(__dirname, "..", "..");
  const main = fs.readFileSync(
    path.join(REPO, "src", "sift", "main.py"),
    "utf8",
  );
  const db = fs.readFileSync(
    path.join(REPO, "src", "sift", "kernel", "db_base.py"),
    "utf8",
  );

  it.each([
    ["INSPECT_LIBRARY", INSPECT_FLAG],
    ["BACK_UP_LIBRARY", BACK_UP_FLAG],
    ["ADOPT_LIBRARY", ADOPT_FLAG],
  ])("%s is %s in sift/main.py", (name, flag) => {
    expect(main).toContain(`\n${name} = "${flag}"\n`);
  });

  it("names the switch note as the backup slice does", () => {
    const libraries = fs.readFileSync(
      path.join(REPO, "src", "sift", "slices", "backup", "libraries.py"),
      "utf8",
    );
    expect(libraries).toContain(`\nHANDOFF_FILENAME = "${SWITCH_NOTE}"\n`);
  });

  it("names the database file as db_base.py does", () => {
    expect(db).toContain(`\nDATABASE_FILENAME = "${DATABASE_FILENAME}"\n`);
  });

  it("names the libraries folder, its mark and the choice made at start as the backend does", () => {
    const config = fs.readFileSync(
      path.join(REPO, "src", "sift", "kernel", "config.py"),
      "utf8",
    );
    const libraries = fs.readFileSync(
      path.join(REPO, "src", "sift", "slices", "backup", "libraries.py"),
      "utf8",
    );
    expect(config).toContain(`\nLIBRARIES_MARK = "${LIBRARIES_MARK}"\n`);
    expect(config).toContain(`\nLIBRARIES_FOLDER = "${LIBRARIES_FOLDER}"\n`);
    expect(libraries).toContain(`\nOPENS_AT_START = "${OPENS_AT_START}"\n`);
  });
});

/* The library chosen on the page to open when Sift starts, read from a real libraries folder. */
describe("the library that opens at start", () => {
  function aFolder(
    chosen: unknown,
    withDatabase = true,
  ): { first: string; chosenData: string } {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "sift-opening-"));
    const first = path.join(root, "data");
    const folder = path.join(root, LIBRARIES_FOLDER);
    const chosenData = path.join(folder, "Holiday", "data");
    fs.mkdirSync(chosenData, { recursive: true });
    if (withDatabase)
      fs.writeFileSync(path.join(chosenData, DATABASE_FILENAME), "");
    fs.writeFileSync(
      path.join(folder, LIBRARIES_MARK),
      JSON.stringify({
        format: 1,
        elsewhere: [],
        ...(chosen === undefined ? {} : { [OPENS_AT_START]: chosen }),
      }),
      "utf8",
    );
    return { first, chosenData };
  }

  it("names the library chosen, found from the first library and from inside the folder", () => {
    const { first, chosenData } = aFolder(undefined);
    const cache = path.join(path.dirname(chosenData), "cache");
    const mark = path.join(
      path.dirname(first),
      LIBRARIES_FOLDER,
      LIBRARIES_MARK,
    );
    fs.writeFileSync(
      mark,
      JSON.stringify({
        format: 1,
        elsewhere: [],
        [OPENS_AT_START]: { data_dir: chosenData, cache_dir: cache },
      }),
      "utf8",
    );
    expect(openingAtStart(first)).toEqual({
      dataDir: chosenData,
      cacheDir: cache,
    });
    expect(openingAtStart(chosenData)).toEqual({
      dataDir: chosenData,
      cacheDir: cache,
    });
  });

  it("is nothing when nothing was chosen, or the chosen library has gone", () => {
    expect(openingAtStart(aFolder(undefined).first)).toBeNull();
    /* A library that has never had a libraries folder at all: the mark is not there to read. */
    expect(
      openingAtStart(
        path.join(os.tmpdir(), `sift-no-mark-${process.pid}-${Date.now()}`),
      ),
    ).toBeNull();
    const gone = fs.mkdtempSync(path.join(os.tmpdir(), "sift-gone-"));
    const { first } = aFolder({
      data_dir: path.join(gone, "data"),
      cache_dir: path.join(gone, "cache"),
    });
    expect(openingAtStart(first)).toBeNull();
  });
});
