/* No way out of the dialog after Sift has stopped touches a library: every button, then every
 * answer of the two setup questions, over real folders holding stand-in libraries, with the real
 * folder checks and library functions. */

import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { boot, resetScene, scene, settle, type Stub } from "../test/main-scene";

vi.mock("./verbs", async (original) =>
  (await import("../test/main-scene")).doubles.verbs(original),
);
vi.mock("./backend", async (original) =>
  (await import("../test/main-scene")).doubles.backend(original),
);
vi.mock("./settings", async (original) =>
  (await import("../test/main-scene")).doubles.settings(original),
);
vi.mock("./browsers", async (original) =>
  (await import("../test/main-scene")).doubles.browsers(original),
);
vi.mock("./shelllink", async (original) =>
  (await import("../test/main-scene")).doubles.shelllink(original),
);
vi.mock("./machine", async (original) =>
  (await import("../test/main-scene")).doubles.machine(original),
);
vi.mock("./firewall", async (original) =>
  (await import("../test/main-scene")).doubles.firewall(original),
);
vi.mock("./update", async (original) =>
  (await import("../test/main-scene")).doubles.update(original),
);
vi.mock("./storage", async () =>
  (await import("../test/main-scene")).doubles.storage(),
);
vi.mock("./tray", async (original) =>
  (await import("../test/main-scene")).doubles.tray(original),
);
vi.mock("./shellpage", async (original) =>
  (await import("../test/main-scene")).doubles.shellpage(original),
);
vi.mock("./smoke", async () =>
  (await import("../test/main-scene")).doubles.smoke(),
);
vi.mock("./logbundle", async () =>
  (await import("../test/main-scene")).doubles.logbundle(),
);
vi.mock("./assets", async () =>
  (await import("../test/main-scene")).doubles.assets(),
);
vi.mock("./paths", async (original) =>
  (await import("../test/main-scene")).doubles.paths(original),
);
vi.mock("./log", async () =>
  (await import("../test/main-scene")).doubles.log(),
);
vi.mock("./uninstall", () => ({
  recordForUninstaller: () => {},
  rememberedDataLocation: async () => null,
}));
vi.mock("./drives", async (original) => ({
  ...(await original<typeof import("./drives")>()),
  isRemoteDrive: async () => false,
}));

let root = "";
let set = { dataDir: "", cacheDir: "" };

/** Every file under the two libraries, with its bytes. */
function snapshot(): Record<string, string> {
  const found: Record<string, string> = {};
  const walk = (dir: string): void => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const at = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(at);
      else found[path.relative(root, at)] = fs.readFileSync(at, "utf8");
    }
  };
  walk(path.join(root, "A"));
  walk(path.join(root, "Local"));
  return found;
}

function library(dataDir: string, name: string): void {
  fs.mkdirSync(dataDir, { recursive: true });
  fs.writeFileSync(path.join(dataDir, "sift.sqlite3"), `${name} database`);
  fs.writeFileSync(
    path.join(dataDir, "sift.sqlite3-wal"),
    `${name} write-ahead log`,
  );
  fs.mkdirSync(path.join(dataDir, "..", "cache"), { recursive: true });
  fs.writeFileSync(
    path.join(dataDir, "..", "cache", "thumb.webp"),
    `${name} thumbnail`,
  );
}

async function started(where: {
  dataDir: string;
  cacheDir: string;
}): Promise<Stub> {
  /* Opened before by this copy, so a folder without its database is a missing library. */
  const libraries = [{ ...where, name: "Library", lastOpened: 1 }];
  return boot({ mode: "standalone", ...where, libraries }, (stub) => {
    stub.paths["appData"] = path.join(root, "Roaming");
  });
}

beforeEach(() => {
  resetScene();
  root = fs.mkdtempSync(path.join(os.tmpdir(), "sift-wayback-libraries-"));
  set = {
    dataDir: path.join(root, "A", "data"),
    cacheDir: path.join(root, "A", "cache"),
  };
  library(set.dataDir, "the set-up library");
  /* The default folder holds a library too, which the folder question offers as existing. */
  library(path.join(root, "Local", "Sift", "data"), "the default library");
});

afterEach(() => {
  fs.rmSync(root, { recursive: true, force: true });
});

describe("the dialog after a library that is set up kept stopping", () => {
  it.each([
    ["Start without them", [0]],
    ["Download log, then Quit", [1, 2]],
    ["Quit", [2]],
  ])("leaves every library as it was: %s", async (_press, answers) => {
    const before = snapshot();
    const electron = await started(set);
    electron.dialogAnswers.push(...answers);

    scene.backends[0]?.gaveUp("it stopped four times", true, 0xc0000005);
    await settle();

    expect(electron.dialogCalls[0]?.buttons).toEqual([
      "Start without them",
      "Download log",
      "Quit",
    ]);
    expect(snapshot()).toEqual(before);
  });
});

describe("the dialog when the library isn't where it was set up", () => {
  const gone = () => ({
    dataDir: path.join(root, "Gone", "data"),
    cacheDir: path.join(root, "Gone", "cache"),
  });

  it.each([
    ["Start again, then Quit", [0, 3]],
    ["Download log, then Quit", [2, 3]],
    ["Quit", [3]],
  ])("leaves every library as it was: %s", async (_press, answers) => {
    const before = snapshot();
    const electron = await started(gone());
    electron.dialogAnswers.push(...answers);

    scene.backends[0]?.gaveUp("it stopped", true, 1);
    await settle();

    expect(electron.dialogCalls[0]?.buttons?.[1]).toBe("Choose again");
    expect(snapshot()).toEqual(before);
  });

  it("leaves every library as it was through Choose again and every answer to both questions", async () => {
    const before = snapshot();
    const electron = await started(gone());
    electron.dialogAnswers.push(1);
    scene.backends[0]?.gaveUp("it stopped", true, 1);
    await settle();

    const setup = scene.hooks.setup;
    /* Closed without choosing, a new empty folder, the folder offered, then Back and both modes. */
    electron.folderAnswers.push([]);
    await setup.library(true, () => {});
    electron.folderAnswers.push([path.join(root, "New")]);
    await setup.library(true, () => {});
    await setup.library(false, () => {});
    await setup.back();
    await setup.mode("client");
    await setup.back();
    await setup.mode("standalone");
    await settle();

    expect(snapshot()).toEqual(before);
    expect(fs.readdirSync(path.join(root, "New", "data"))).toEqual([]);
  });
});
