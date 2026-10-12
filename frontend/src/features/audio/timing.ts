export interface Mark {
  start_ms: number;
  end_ms: number;
  text_start: number;
  text_end: number;
}
export interface Segment {
  index: number;
  start_ms: number;
  duration_ms: number;
  text_start: number;
  text_end: number;
  alignment: string;
  timings: Mark[];
  url: string;
}
export function segmentAt(
  segments: Segment[],
  position: number,
): Segment | undefined {
  return (
    segments.find(
      (s) => position >= s.start_ms && position < s.start_ms + s.duration_ms,
    ) ?? segments.at(-1)
  );
}
export function activeMark(
  segment: Segment,
  currentTime: number,
): Mark | undefined {
  if (segment.alignment !== "words") return undefined;
  const ms = currentTime * 1000;
  return segment.timings.find((m) => m.start_ms <= ms && ms < m.end_ms);
}
export function unicodeSlice(
  text: string,
  start: number,
  end?: number,
): string {
  return Array.from(text).slice(start, end).join("");
}
export function browserBoundary(text: string, charIndex: number): number {
  return Array.from(text.slice(0, charIndex)).length;
}
