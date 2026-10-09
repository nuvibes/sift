/* What this computer is, for a window whose library lives on another one. */

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import * as os from "node:os";

import {
  type Ask,
  cardFrom,
  forgetMemory,
  graphicsCards,
  installedMemory,
  machineName,
  thisMachine,
} from "./machine";

beforeEach(() => {
  forgetMemory();
});

afterEach(() => {
  forgetMemory();
});

/** A machine that answers a question, and counts how often it was asked. */
function answering(said: string | null) {
  const asked: string[][] = [];
  const ask = async (file: string, args: string[]): Promise<string | null> => {
    asked.push([file, ...args]);
    return said;
  };
  return Object.assign(ask, { asked });
}

describe("the memory in this computer", () => {
  it("takes what the firmware says is installed", async () => {
    const ask = answering("68719476736\r\n");

    expect(await installedMemory(ask)).toBe(68_719_476_736);
  });

  /* Asked once. It cannot change while the machine is running, and the query starts a process,
     so a settings screen opened five times would start five of them for one known answer. */
  it("asks the machine once and remembers the answer", async () => {
    const ask = answering("68719476736");

    await installedMemory(ask);
    await installedMemory(ask);
    await installedMemory(ask);

    expect(ask.asked).toHaveLength(1);
  });

  /* A number slightly under what is installed is a better answer than no row at all. */
  it("falls back to what the operating system knows", async () => {
    expect(await installedMemory(answering(null))).toBe(os.totalmem());
  });

  it("falls back for an answer that is not a number", async () => {
    expect(
      await installedMemory(answering("Get-CimInstance : Access denied")),
    ).toBe(os.totalmem());
  });

  /* Zero is not an answer about memory; it is the absence of one, and it must not become a row
     saying "0 GB". */
  it("does not take a zero as an answer", async () => {
    expect(await installedMemory(answering("0"))).toBe(os.totalmem());
  });
});

describe("this computer", () => {
  it("answers the facts the screen draws", async () => {
    const machine = await thisMachine(answering("68719476736"));

    expect(machine.thread_count).toBe(os.cpus().length);
    expect(machine.thread_count).toBeGreaterThan(0);
    expect(machine.installed_ram_bytes).toBe(68_719_476_736);
    // Named, or honestly not named. Never an empty string, which draws as a blank row.
    expect(machine.cpu_model === null || machine.cpu_model.length > 0).toBe(
      true,
    );
  });

  /* Threads, not cores. A twelve-core chip with two threads each answers 24, and a row labelled
     "Cores" over that number is wrong. */
  it("counts logical processors, which is what Node reports", async () => {
    const machine = await thisMachine(answering(null));

    expect(machine.thread_count).toBe(os.cpus().length);
  });
});

describe("what this computer is called", () => {
  it("answers the name the computer gives its network, trimmed", () => {
    expect(machineName(() => "  DESK-UPSTAIRS \n")).toBe("DESK-UPSTAIRS");
  });

  it("holds a name to one network label's length", () => {
    expect(machineName(() => "A".repeat(200))).toBe("A".repeat(63));
  });

  it("answers null rather than an empty name, which would read as a blank label", () => {
    expect(machineName(() => "   ")).toBeNull();
  });
});

/* The graphics adapters in THIS computer. */
/* Both platform branches on every platform, so the shell's coverage counts the same lines on
 * Windows and on the Linux runner: without this, one platform skipped a branch the other ran and
 * the ratchet read the difference as a move. */
function onPlatform<T>(name: NodeJS.Platform, run: () => T): T {
  const real = Object.getOwnPropertyDescriptor(process, "platform");
  Object.defineProperty(process, "platform", { value: name, configurable: true });
  try {
    return run();
  } finally {
    if (real) Object.defineProperty(process, "platform", real);
  }
}

describe("the platform each question is asked on", () => {
  it("asks the firmware for memory only on Windows, and remembers what the system says elsewhere", async () => {
    forgetMemory();
    const asked: string[] = [];
    const ask: Ask = async (file) => {
      asked.push(file);
      return "68719476736";
    };
    const off = await onPlatform("linux", () => installedMemory(ask));
    expect(asked).toEqual([]);
    expect(off).toBe(os.totalmem());
    forgetMemory();
    const on = await onPlatform("win32", () => installedMemory(ask));
    expect(asked).toEqual(["powershell.exe"]);
    expect(on).toBe(68_719_476_736);
  });

  it("lists no graphics adapter off Windows without asking anything", async () => {
    const asked: string[] = [];
    const ask: Ask = async (file) => {
      asked.push(file);
      return "";
    };
    expect(await onPlatform("linux", () => graphicsCards(ask))).toEqual([]);
    expect(asked).toEqual([]);
    expect(await onPlatform("win32", () => graphicsCards(ask))).toEqual([]);
    expect(asked).toEqual(["powershell.exe"]);
  });
});

describe("the graphics adapters in this computer", () => {
  /* THE ONE THIS EXISTS FOR. Read from `AdapterRAM`, a 16 GB card would have no memory beside it
     at all, because that field is 32 bits and stops at 4293918720. */
  it("takes what the driver recorded over the field that stops counting", () => {
    expect(
      cardFrom("ACME GPU X16|30.0.1|17179869184|4293918720"),
    ).toEqual({
      name: "ACME GPU X16",
      vram_bytes: 17179869184,
    });
  });

  it("falls back to the capped field for a driver that recorded nothing", () => {
    expect(cardFrom("An old card|1.0||2147483648")?.vram_bytes).toBe(
      2147483648,
    );
  });

  /* At the cap it means "four gigabytes or more" and nothing else, which is not a figure to put
     on a screen. */
  it("does not believe that fallback at the cap", () => {
    expect(cardFrom("A big card|1.0||4293918720")?.vram_bytes).toBeNull();
  });

  it("leaves out the adapters that are not hardware", () => {
    expect(
      cardFrom("Microsoft Remote Display Adapter|10.0.1||"),
    ).toBeNull();
    expect(cardFrom("Microsoft Basic Display Adapter|10.0.1||")).toBeNull();
  });

  it("keeps a card that will not say how much memory it has", () => {
    expect(cardFrom("ACME GPU X8|30.0.2||")).toEqual({
      name: "ACME GPU X8",
      vram_bytes: null,
    });
  });

  it("answers nothing for a line with no name in it", () => {
    expect(cardFrom("")).toBeNull();
    expect(cardFrom("|1.0|123|")).toBeNull();
  });
});
