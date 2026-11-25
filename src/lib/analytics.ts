import type { Level } from "@/types";

// GA4 events for the value-signal interactions (#118).
//
// Only fixed event names and values derived from public page content are sent:
// no device ids, no free text, nothing read from cookies or storage.
//
// Delivery goes through the same queue the inline gtag snippet in
// src/app/layout.tsx uses. When gtag() is defined we call it, so production
// behaviour is exactly gtag('event', ...). When it is not (no GA_ID, or the
// afterInteractive snippet has not run yet) we push the same `arguments`-shaped
// entry onto window.dataLayer, which gtag.js drains once it loads and which is
// otherwise inert — nothing is sent anywhere without gtag.js.

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
    const w = window as GtagWindow;
    if (typeof w.gtag === "function") {
      w.gtag("event", event.name, event.params);
    } else {
      queueGtag("event", event.name, event.params);
    }
  } catch {
    // Analytics must never break the interaction it observes.
  }
}
