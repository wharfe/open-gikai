import type { Level } from "@/types";

// GA4 events for the value-signal interactions (#118).
//
// Only fixed event names and values derived from public page content are sent:
// no device ids, no free text, nothing read from cookies or storage.
//
// Three cases, decided at call time:
// 1. window.gtag exists (production, after the inline snippet in
//    src/app/layout.tsx ran): call gtag('event', ...) — exactly what a
//    hand-written gtag call would do.
// 2. GA is configured for this build but the afterInteractive snippet has not
//    run yet: drop the event. Pushing it onto dataLayer now would put it ahead
//    of the snippet's 'js' / 'config' commands, where gtag.js does not promise
//    to deliver it. The window is the gap between hydration and that snippet;
//    holding events across it (an earlier version did) cost ordering and
//    retry machinery for clicks that almost never land there.
// 3. GA is not configured (local builds, e2e): push the same Arguments-shaped
//    entry gtag() would have pushed onto window.dataLayer. Without gtag.js
//    nothing reads it, so it sends nothing; the e2e test reads it back.

const GA_CONFIGURED = Boolean(process.env.NEXT_PUBLIC_GA_ID);

type GtagWindow = Window & {
  dataLayer?: unknown[];
  gtag?: (...args: unknown[]) => void;
};

export type AnalyticsEvent =
  | { name: "view_raw_transcript"; params: { thread_id: string; level: Level } }
  | { name: "change_reading_level"; params: { level: Level; previous_level: Level } };

// The parameters are read through `arguments` below, not by name.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
function queueGtag(..._args: unknown[]): void {
  const w = window as GtagWindow;
  w.dataLayer = w.dataLayer || [];
  // gtag.js only processes Arguments objects, not plain arrays, so push
  // `arguments` exactly like the gtag() stub in layout.tsx does.
  // eslint-disable-next-line prefer-rest-params
  w.dataLayer.push(arguments);
}

export function trackEvent(event: AnalyticsEvent): void {
  if (typeof window === "undefined") return;
  try {
    const gtag = (window as GtagWindow).gtag;
    if (typeof gtag === "function") {
      gtag("event", event.name, event.params);
    } else if (!GA_CONFIGURED) {
      queueGtag("event", event.name, event.params);
    }
  } catch {
    // Analytics must never break the interaction it observes.
  }
}
