/* The scene the tests of `main.ts` set: a double of every module `main` composes, the state those
 * doubles close over, and the boot that imports `main` fresh against them.
 *
 * The state is kept on `globalThis` because every fresh import of `main` (after `vi.resetModules`)
 * imports this module fresh as well, and each copy has to read and write the same objects. A test
 * file names each double in its own `vi.mock`, which only a test file can do.
 */

import { vi } from "vitest";

import type { DataLocations } from "../src/paths";
import type { DesktopSettings } from "../src/settings";
import type { ShellHooks } from "../src/verbs";

type Original = <T>() => Promise<T>;

/** Every hook `main` hands the verbs, as present: `main` wires all of them, and a test that found
 *  one missing would fail at the call, by name. */
type Wired = { [K in keyof ShellHooks]-?: NonNullable<ShellHooks[K]> };

const makeScene = () => ({
  settings: null as unknown as DesktopSettings,
  saved: [] as DesktopSettings[],
  hooks: null as unknown as Wired,
  trust: null as unknown as (url: string) => "local" | "remote" | null,
  smoke: false,
  order: [] as string[],
  backends: [] as {
    locations: DataLocations;
    sharing: boolean;
    feed: string | null;
    held: boolean;
    gaveUp: (reason: string, crashed?: boolean, code?: number | null) => void;
    takeOver: () => boolean;
    stopped: number;
    listened: boolean[];
  }[],
  note: null as DataLocations | null,
  opening: null as DataLocations | null,
  notesTaken: [] as string[],
  startFailures: [] as Error[],
  listenAnswer: true,
  picked: null as DataLocations | null,
  offered: {
    locations: { dataDir: "C:\\Offered\\data", cacheDir: "C:\\Offered\\cache" },
    existing: false,
  },
  refusal: null as string | null,
  verdict: "current" as string,
  detail: "",
  backup: "C:\\Lib\\data\\backup.sqlite3" as string | null,
  fileVerdict: "current" as string,
  fileDetail: "",
  adopted: [] as [string, string][],
  adoptWorks: true,
  moved: [] as string[],
  moveOutcome: null as unknown,
  uninstaller: [] as (DataLocations | null)[],
  trays: [] as {
    destroyed: boolean;
    verbs: { open: () => void; quit: () => void };
  }[],
  openedIn: [] as [string, string][],
  openInWorks: true,
  logged: [] as string[],
  said: [] as [string, Record<string, unknown>][],
  link: null as unknown as import("../src/shelllink").ShellActs,
  storageRefusal: null as string | null,
  update: null as unknown as (hooks: {
    beforeLaunch?: (version: string) => Promise<void>;
  }) => Promise<import("../src/update").UpdateOutcome>,
  firewall: [] as unknown[][],
  bundled: [] as unknown[],
  libraryHere: true,
  logArchive: null as unknown as (name: string) => Promise<string | null>,
  bundleAnswer: { ok: true, file: "C:\\Downloads\\Sift log.zip" } as
    { ok: true; file: string } | { ok: false; reason: string },
});

type Scene = ReturnType<typeof makeScene>;
const held = globalThis as { siftMainScene?: Scene };
export const scene: Scene = (held.siftMainScene ??= makeScene());

export const doubles = {
  verbs: async (importOriginal: Original) => {
    const real = await importOriginal<typeof import("../src/verbs")>();
    return {
      ...real,
      networkAddress: () => "10.0.0.9",
      registerVerbs: (
        trust: (url: string) => "local" | "remote" | null,
        hooks: ShellHooks,
      ) => {
        scene.order.push("verbs");
        scene.trust = trust;
        scene.hooks = hooks as Wired;
      },
    };
  },
  backend: async (importOriginal: Original) => {
    const real = await importOriginal<typeof import("../src/backend")>();
    class Backend {
      private readonly record;
      constructor(
        locations: DataLocations,
        gaveUp: (
          reason: string,
          crashed?: boolean,
          code?: number | null,
        ) => void,
        sharing: boolean,
        takeOver: () => boolean = () => false,
        feed: string | null = null,
        _link: unknown = null,
        held = false,
      ) {
        this.record = {
          locations,
          sharing,
          feed,
          held,
          gaveUp,
          takeOver,
          stopped: 0,
          listened: [] as boolean[],
        };
        scene.backends.push(this.record);
      }
      async start(): Promise<void> {
        scene.order.push("backend.start");
        const failure = scene.startFailures.shift();
        if (failure !== undefined) throw failure;
      }
      async stop(): Promise<void> {
        scene.order.push("backend.stop");
        this.record.stopped += 1;
      }
      async listenOnNetwork(on: boolean): Promise<boolean> {
        this.record.listened.push(on);
        return scene.listenAnswer;
      }
    }
    return { ...real, Backend };
  },
  settings: async (importOriginal: Original) => {
    const real = await importOriginal<typeof import("../src/settings")>();
    return {
      ...real,
      load: () => scene.settings,
      save: (settings: DesktopSettings) => {
        scene.saved.push(settings);
      },
    };
  },
  firstrun: async (importOriginal: Original) => {
    const real = await importOriginal<typeof import("../src/firstrun")>();
    return {
      ...real,
      offeredDataLocation: async () => scene.offered,
      pickDataLocation: async () => scene.picked,
      refuseLocation: async () => scene.refusal,
    };
  },
  libraries: async (importOriginal: Original) => {
    const real = await importOriginal<typeof import("../src/libraries")>();
    return {
      ...real,
      inspectLibrary: async () => ({
        verdict: scene.verdict,
        detail: scene.detail,
      }),
      inspectDatabase: async () => ({
        verdict: scene.fileVerdict,
        detail: scene.fileDetail,
      }),
      backUpLibrary: async () => scene.backup,
      holdsLibrary: () => scene.libraryHere,
      openingAtStart: () => scene.opening,
      takeSwitchNote: (dataDir: string) => {
        scene.notesTaken.push(dataDir);
        const note = scene.note;
        scene.note = null;
        return note;
      },
      adoptDatabase: async (file: string, where: DataLocations) => {
        scene.order.push("adopt");
        scene.adopted.push([file, where.dataDir]);
        return scene.adoptWorks ? `${where.dataDir}\\sift.sqlite3` : null;
      },
    };
  },
  browsers: async (importOriginal: Original) => {
    const real = await importOriginal<typeof import("../src/browsers")>();
    return {
      ...real,
      openIn: (browser: string, url: string) => {
        scene.openedIn.push([browser, url]);
        return scene.openInWorks;
      },
    };
  },
  shelllink: async (importOriginal: Original) => {
    const real = await importOriginal<typeof import("../src/shelllink")>();
    return {
      ...real,
      /* The acts `main` hands the link, kept so a test can ask them as the backend would. */
      openShellLink: async (acts: import("../src/shelllink").ShellActs) => {
        scene.link = acts;
        return { url: "http://127.0.0.1:1", token: "t", close: async () => {} };
      },
    };
  },
  machine: async (importOriginal: Original) => {
    const real = await importOriginal<typeof import("../src/machine")>();
    return { ...real, machineName: () => "STUDY-PC" };
  },
  /* Windows is never asked from a test: the doubles record the port and the scope they were handed. */
  firewall: async (importOriginal: Original) => {
    const real = await importOriginal<typeof import("../src/firewall")>();
    return {
      ...real,
      firewallState: async (port: number) => {
        scene.firewall.push(["read", port]);
        return { state: "closed", networks: ["Private"], scope: null };
      },
      openFirewall: async (port: number, run: unknown, scope: string) => {
        scene.firewall.push(["open", port, run, scope]);
        return { state: "open", networks: ["Private"], scope };
      },
    };
  },
  update: async (importOriginal: Original) => {
    const real = await importOriginal<typeof import("../src/update")>();
    return {
      ...real,
      applyUpdate: async (
        _feed: string,
        _key: string | undefined,
        hooks: { beforeLaunch?: (version: string) => Promise<void> },
      ) => scene.update(hooks),
    };
  },
  storage: () => ({
    refuse: async () => scene.storageRefusal,
    StorageSizes: class {
      read(where: DataLocations) {
        return {
          ...where,
          dataBytes: 1,
          cacheBytes: 2,
          measuredAt: 0,
          measuring: false,
        };
      }
    },
    move: async (
      _from: DataLocations,
      target: string,
      report: (progress: { copied: number; total: number }) => void,
    ) => {
      scene.order.push("storage.move");
      scene.moved.push(target);
      report({ copied: 1, total: 1 });
      return scene.moveOutcome;
    },
  }),
  tray: async (importOriginal: Original) => {
    const real = await importOriginal<typeof import("../src/tray")>();
    return {
      ...real,
      showInTray: (
        _icon: string | null,
        verbs: { open: () => void; quit: () => void },
      ) => {
        const tray = {
          destroyed: false,
          verbs,
          destroy() {
            tray.destroyed = true;
          },
        };
        scene.trays.push(tray);
        return tray;
      },
    };
  },
  shellpage: async (importOriginal: Original) => {
    const real = await importOriginal<typeof import("../src/shellpage")>();
    return {
      ...real,
      declareShellScheme: () => {
        scene.order.push("scheme");
      },
      serveShellPages: () => {
        scene.order.push("pages");
      },
    };
  },
  smoke: () => ({
    answerSmokeRun: (
      _argv: readonly string[],
      write: (line: string) => void,
      stop: (code: number) => void,
    ) => {
      if (!scene.smoke) return false;
      /* An empty line, so the run's own output is not written into the test's. */
      write("");
      stop(0);
      return scene.smoke;
    },
  }),
  logbundle: () => ({
    bundleLogs: async (ask: unknown) => {
      scene.bundled.push(ask);
      return scene.bundleAnswer;
    },
    registerLogArchive: (
      _trust: unknown,
      archive: (name: string) => Promise<string | null>,
    ) => {
      scene.logArchive = archive;
    },
  }),
  uninstall: () => ({
    recordForUninstaller: (where: DataLocations | null) => {
      scene.uninstaller.push(where);
    },
  }),
  assets: () => ({
    clearFetchedFiles: () => {
      scene.order.push("clear");
    },
  }),
  paths: async (importOriginal: Original) => {
    const real = await importOriginal<typeof import("../src/paths")>();
    return {
      ...real,
      appIconFile: () => undefined,
      trayIconFile: () => "C:\\tray.ico",
    };
  },
  log: () => {
    const record = (event: string, fields: Record<string, unknown> = {}) => {
      scene.logged.push(event);
      scene.said.push([event, fields]);
    };
    return {
      log: { debug: record, info: record, warning: record, error: record },
      tail: (lines: number) => ({
        lines: [`${lines} lines`],
        path: "C:\\Sift\\shell.log",
        size: 1,
        present: true,
      }),
      linesAsked: (asked: unknown) => (typeof asked === "number" ? asked : 200),
    };
  },
};

export const LOCAL = "http://127.0.0.1:5171";
export const SHELL = "sift-shell://app";
export const SERVER = "http://192.168.1.20:5171";
export const LIBRARY: DataLocations = {
  dataDir: "C:\\Lib\\data",
  cacheDir: "C:\\Lib\\cache",
};
export const OTHER: DataLocations = {
  dataDir: "D:\\Other\\data",
  cacheDir: "D:\\Other\\cache",
};

export type Stub = typeof import("./electron-stub");

/** A fresh `main`, started against `settings`. Answers the stub instance `main` itself holds. */
export async function boot(
  settings: Partial<DesktopSettings> = {},
  before: (electron: Stub) => void = () => {},
): Promise<Stub> {
  vi.resetModules();
  /* Imported AFTER the reset, so it is the same fresh instance `main` is about to import through
	   the alias, and the stub's lists are what the test reads back. */
  const electron = await import("./electron-stub");
  const { fresh } = await import("../src/settings");
  electron.resetElectronStub();
  scene.settings = { ...fresh(), ...settings };
  before(electron);
  await import("../src/main");
  await settle();
  return electron;
}

/** Let every promise `main` started run to the end. */
export async function settle(): Promise<void> {
  for (let turn = 0; turn < 30; turn += 1)
    await new Promise((done) => setImmediate(done));
}

export function windowOf(electron: Stub) {
  const window = electron.BrowserWindow.instances[0];
  if (window === undefined) throw new Error("main made no window");
  return window;
}

export function lastSaved(): DesktopSettings {
  const saved = scene.saved.at(-1);
  if (saved === undefined) throw new Error("nothing was saved");
  return saved;
}

/** A /health answer from a Sift: the mark on it is what makes it one. */
export function aSift(electron: Stub, status = 200) {
  return electron.reply(
    { status: "ok" },
    { status, headers: { "x-sift": "1" } },
  );
}

/** Every double back at its starting state, before each test. */
export function resetScene(): void {
  scene.saved = [];
  scene.smoke = false;
  scene.order = [];
  scene.backends = [];
  scene.startFailures = [];
  scene.listenAnswer = true;
  scene.picked = null;
  scene.refusal = null;
  scene.verdict = "current";
  scene.detail = "";
  scene.backup = "C:\\Lib\\data\\backup.sqlite3";
  scene.fileVerdict = "current";
  scene.fileDetail = "";
  scene.adopted = [];
  scene.adoptWorks = true;
  scene.note = null;
  scene.notesTaken = [];
  scene.moved = [];
  scene.moveOutcome = { ok: true, locations: OTHER, renamed: false };
  scene.uninstaller = [];
  scene.opening = null;
  scene.trays = [];
  scene.openedIn = [];
  scene.openInWorks = true;
  scene.logged = [];
  scene.said = [];
  scene.storageRefusal = null;
  scene.update = async () => ({ ok: false, reason: "none" });
  scene.firewall = [];
  scene.bundled = [];
  scene.libraryHere = true;
  scene.bundleAnswer = { ok: true, file: "C:\\Downloads\\Sift log.zip" };
}
