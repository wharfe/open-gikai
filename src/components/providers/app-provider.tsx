"use client";

import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  type ReactNode,
} from "react";
import type { Level } from "@/types";

type AppContextType = {
  level: Level;
  setLevel: (level: Level) => void;
  follows: Set<string>;
  toggleFollow: (memberId: string) => void;
};

const AppContext = createContext<AppContextType | null>(null);

function loadFollows(): Set<string> {
  try {
    const saved = localStorage.getItem("gikai-follows");
    if (saved) {
      return new Set(JSON.parse(saved));
    }
  } catch {
    // Ignore localStorage errors
  }
  return new Set();
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [level, setLevel] = useState<Level>("adult");
  const [follows, setFollows] = useState<Set<string>>(new Set());
  const [hydrated, setHydrated] = useState(false);

  // Restore follows from localStorage after hydration
  useEffect(() => {
    const restored = loadFollows();
    // eslint-disable-next-line react-hooks/set-state-in-effect -- Intentional one-time hydration from localStorage
    setFollows(restored);
    setHydrated(true);
  }, []);

  // Persist follows to localStorage
  useEffect(() => {
    if (!hydrated) return;
    try {
      localStorage.setItem(
        "gikai-follows",
        JSON.stringify([...follows])
      );
    } catch {
      // Ignore localStorage errors
    }
  }, [follows, hydrated]);

  const toggleFollow = useCallback((memberId: string) => {
    setFollows((prev) => {
      const next = new Set(prev);
      if (next.has(memberId)) {
        next.delete(memberId);
      } else {
        next.add(memberId);
      }
      return next;
    });
  }, []);

  return (
    <AppContext.Provider value={{ level, setLevel, follows, toggleFollow }}>
      {children}
    </AppContext.Provider>
  );
}

export function useAppContext(): AppContextType {
  const ctx = useContext(AppContext);
  if (!ctx) {
    throw new Error("useAppContext must be used within AppProvider");
  }
  return ctx;
}
