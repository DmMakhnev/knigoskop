import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  act,
} from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { BrowserSpeech, MediaPlayer } from "./AudioPanel";
import type { Manifest } from "./AudioPanel";
import { activeMark, browserBoundary, segmentAt, unicodeSlice } from "./timing";

const manifest: Manifest = {
  id: "asset",
  text: "😀 One Two",
  voice: "demo",
  duration_ms: 1200,
  demo: true,
  progress: { position_ms: 0, playback_rate: 1, revision: 0 },
  segments: [
    {
      index: 0,
      start_ms: 0,
      duration_ms: 800,
      text_start: 0,
      text_end: 6,
      alignment: "words",
      url: "/one.wav",
      timings: [
        { start_ms: 0, end_ms: 400, text_start: 0, text_end: 1 },
        { start_ms: 400, end_ms: 800, text_start: 2, text_end: 5 },
      ],
    },
    {
      index: 1,
      start_ms: 800,
      duration_ms: 400,
      text_start: 6,
      text_end: 9,
      alignment: "words",
      url: "/two.wav",
      timings: [{ start_ms: 0, end_ms: 400, text_start: 0, text_end: 3 }],
    },
  ],
};
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
function stubProgress() {
  let revision = 0;
  const fetch = vi.fn(async (_url: string, init?: RequestInit) => ({
    ok: true,
    status: 200,
    json: async () => (init?.method === "PUT" ? { revision: ++revision } : {}),
  }));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

test("timings use media time and Unicode offsets at segment boundaries", () => {
  expect(activeMark(manifest.segments[0], 0.45)?.text_start).toBe(2);
  expect(segmentAt(manifest.segments, 800)?.index).toBe(1);
  expect(segmentAt(manifest.segments, 1200)?.index).toBe(1);
  expect(unicodeSlice(manifest.text, 0, 1)).toBe("😀");
  expect(browserBoundary("😀 One", 3)).toBe(2);
});

test("player highlights at 1x and 1.5x and seeks across segments", async () => {
  stubProgress();
  vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
  render(<MediaPlayer manifest={manifest} />);
  const audio = document.querySelector("audio")!;
  fireEvent.loadedMetadata(audio);
  audio.currentTime = 0.45;
  fireEvent.timeUpdate(audio);
  expect(screen.getByRole("button", { name: "One" })).toHaveAttribute(
    "aria-current",
    "true",
  );
  fireEvent.change(screen.getByLabelText("Скорость"), {
    target: { value: "1.5" },
  });
  expect(audio.playbackRate).toBe(1.5);
  fireEvent.timeUpdate(audio);
  expect(screen.getByRole("button", { name: "One" })).toHaveAttribute(
    "aria-current",
    "true",
  );
  fireEvent.click(screen.getByRole("button", { name: "Two" }));
  await waitFor(() => expect(audio.getAttribute("src")).toBe("/two.wav"));
  fireEvent.loadedMetadata(audio);
  expect(audio.currentTime).toBe(0);
  fireEvent.seeked(audio);
  expect(screen.getByRole("button", { name: "Two" })).toHaveAttribute(
    "aria-current",
    "true",
  );
});

test("saved audio position and speed are restored", () => {
  stubProgress();
  render(
    <MediaPlayer
      manifest={{
        ...manifest,
        progress: { position_ms: 900, playback_rate: 1.5, revision: 3 },
      }}
    />,
  );
  const audio = document.querySelector("audio")!;
  expect(audio.getAttribute("src")).toBe("/two.wav");
  fireEvent.loadedMetadata(audio);
  expect(audio.currentTime).toBeCloseTo(0.1);
  expect(audio.playbackRate).toBe(1.5);
});

test("unaligned audio never fabricates word highlighting", () => {
  stubProgress();
  render(
    <MediaPlayer
      manifest={{
        ...manifest,
        segments: manifest.segments.map((s) => ({
          ...s,
          alignment: "none",
          timings: [],
        })),
      }}
    />,
  );
  expect(screen.getByText(/Точные таймкоды недоступны/)).toBeInTheDocument();
  expect(document.querySelector(".karaoke-active")).toBeNull();
  expect(screen.queryByRole("button", { name: "One" })).toBeNull();
});

test("Web Speech requires consent and uses actual browser boundary events", () => {
  const synth = {
    getVoices: () => [],
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    cancel: vi.fn(),
    speak: vi.fn(),
    pause: vi.fn(),
    resume: vi.fn(),
  };
  class Utterance {
    text: string;
    onboundary?: (e: { charIndex: number }) => void;
    constructor(text: string) {
      this.text = text;
    }
  }
  vi.stubGlobal("speechSynthesis", synth);
  vi.stubGlobal("SpeechSynthesisUtterance", Utterance);
  const view = render(
    <BrowserSpeech text="One Two" consent={false} complete />,
  );
  expect(
    screen.getByRole("button", { name: "Озвучить в браузере" }),
  ).toBeDisabled();
  view.rerender(<BrowserSpeech text="One Two" consent complete />);
  fireEvent.click(screen.getByRole("button", { name: "Озвучить в браузере" }));
  expect(synth.speak).toHaveBeenCalledOnce();
  expect(document.querySelector(".karaoke-active")).toBeNull();
  const utterance = synth.speak.mock.calls[0][0] as Utterance;
  act(() => utterance.onboundary?.({ charIndex: 4 }));
  expect(screen.getByRole("button", { name: "Two" })).toHaveClass(
    "karaoke-active",
  );
  fireEvent.click(screen.getByRole("button", { name: "Two" }));
  expect((synth.speak.mock.calls[1][0] as Utterance).text).toBe("Two");
});

test("speed changes during a pending progress save are serialized", async () => {
  let resolveFirst: ((value: unknown) => void) | undefined;
  const bodies: { revision: number; playback_rate: number }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method !== "PUT")
        return { ok: true, status: 200, json: async () => ({}) };
      bodies.push(JSON.parse(init.body as string));
      if (bodies.length === 1)
        return new Promise((resolve) => {
          resolveFirst = resolve;
        });
      return { ok: true, status: 200, json: async () => ({ revision: 2 }) };
    }),
  );
  render(<MediaPlayer manifest={manifest} />);
  const audio = document.querySelector("audio")!;
  audio.currentTime = 0.3;
  fireEvent.timeUpdate(audio);
  await waitFor(() => expect(bodies).toHaveLength(1));
  fireEvent.change(screen.getByLabelText("Скорость"), {
    target: { value: "1.5" },
  });
  await act(async () =>
    resolveFirst?.({
      ok: true,
      status: 200,
      json: async () => ({ revision: 1 }),
    }),
  );
  await waitFor(() => expect(bodies).toHaveLength(2));
  expect(bodies[1]).toMatchObject({ revision: 1, playback_rate: 1.5 });
});
