"use client";

import { useState } from "react";

type ShareButtonProps = {
  text: string;
};

export function ShareButton({ text }: ShareButtonProps) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);

  const handleX = (e: React.MouseEvent) => {
    e.stopPropagation();
    window.open(
      `https://twitter.com/intent/tweet?text=${encodeURIComponent(text)}`,
      "_blank",
      "width=600,height=400"
    );
    setOpen(false);
  };

  const handleCopy = async (e: React.MouseEvent) => {
    e.stopPropagation();
    await navigator.clipboard.writeText(text).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
    setOpen(false);
  };

  return (
    <div className="relative inline-block">
      <button
        onClick={(e) => {
          e.stopPropagation();
          setOpen((o) => !o);
        }}
        className="cursor-pointer rounded-md border px-2 py-0.5 text-[11px] transition-all"
        style={{
          background: open ? "rgba(125,211,252,0.1)" : "none",
          borderColor: open ? "rgba(125,211,252,0.3)" : "#1e293b",
          color: open ? "#7dd3fc" : "#475569",
        }}
      >
        ↗ シェア
      </button>
      {open && (
        <>
          <div
            onClick={() => setOpen(false)}
            className="fixed inset-0 z-[199]"
          />
          <div
            onClick={(e) => e.stopPropagation()}
            className="absolute bottom-[calc(100%+6px)] right-0 z-[200] min-w-[160px] overflow-hidden rounded-[10px] border border-slate-800 bg-gikai-card shadow-[0_8px_24px_rgba(0,0,0,0.4)]"
          >
            <button
              onClick={handleX}
              className="flex w-full cursor-pointer items-center gap-2 border-none bg-transparent px-3.5 py-2.5 text-left text-[13px] text-slate-200 hover:bg-slate-800"
            >
              <span className="text-[15px]">𝕏</span> Xに投稿する
            </button>
            <div className="h-px bg-slate-800" />
            <button
              onClick={handleCopy}
              className="flex w-full cursor-pointer items-center gap-2 border-none bg-transparent px-3.5 py-2.5 text-left text-[13px] hover:bg-slate-800"
              style={{ color: copied ? "#34d399" : "#e2e8f0" }}
            >
              <span>{copied ? "✓" : "⎘"}</span>{" "}
              {copied ? "コピーしました" : "テキストをコピー"}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
