export function paragraphs(text: string, start: number) {
  let offset = start;
  return text.split('\n').map((value) => {
    const row = {
      text: value,
      start: offset,
      end: offset + Array.from(value).length,
    };
    offset = row.end + 1;
    return row;
  });
}
export function splitHighlight(
  text: string,
  offset: number,
  range: { start_offset: number; end_offset: number } | null,
) {
  const points = Array.from(text);
  if (!range) return { before: text, selected: '', after: '' };
  const start = Math.max(
    0,
    Math.min(points.length, range.start_offset - offset),
  );
  const end = Math.max(
    start,
    Math.min(points.length, range.end_offset - offset),
  );
  return {
    before: points.slice(0, start).join(''),
    selected: points.slice(start, end).join(''),
    after: points.slice(end).join(''),
  };
}
export function selectionRange(container: HTMLElement) {
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed || !selection.rangeCount) return null;
  const range = selection.getRangeAt(0);
  if (
    !container.contains(range.startContainer) ||
    !container.contains(range.endContainer)
  )
    return null;
  function point(node: Node, offset: number) {
    const element = (
      node instanceof Element ? node : node.parentElement
    )?.closest<HTMLElement>('[data-offset]');
    if (!element) return null;
    const prefix = document.createRange();
    prefix.selectNodeContents(element);
    prefix.setEnd(node, offset);
    return (
      Number(element.dataset.offset) + Array.from(prefix.toString()).length
    );
  }
  const start = point(range.startContainer, range.startOffset);
  const end = point(range.endContainer, range.endOffset);
  return start !== null && end !== null && start < end
    ? { start_offset: start, end_offset: end }
    : null;
}
