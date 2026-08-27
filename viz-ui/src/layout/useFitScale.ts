import { useLayoutEffect, useState, type RefObject } from "react";

/** Blow-up cap: a two-node graph should read bigger, not become a billboard. */
const MAX_SCALE = 2;

/** Scale factor that grows a natural-size SVG to fill its container's width
 * and the viewport height below it. Never shrinks below 1 — oversized graphs
 * keep their readable natural size and scroll, exactly as before. */
export function useFitScale(
  ref: RefObject<HTMLElement | null>,
  naturalWidth: number,
  naturalHeight: number,
  enabled: boolean,
): number {
  const [scale, setScale] = useState(1);

  useLayoutEffect(() => {
    if (!enabled) return;
    const el = ref.current;
    if (!el || naturalWidth <= 0 || naturalHeight <= 0) return;
    const measure = () => {
      const availWidth = el.clientWidth;
      // Room from the graph's top edge to the bottom of the viewport, with a
      // floor so a graph far down the page still gets a usable canvas.
      const availHeight = Math.max(360, window.innerHeight - el.getBoundingClientRect().top - 28);
      const fit = Math.min(availWidth / naturalWidth, availHeight / naturalHeight, MAX_SCALE);
      setScale(Math.max(1, fit));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    window.addEventListener("resize", measure);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [ref, naturalWidth, naturalHeight, enabled]);

  return enabled ? scale : 1;
}
