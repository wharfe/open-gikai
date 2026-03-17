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
        className="flex cursor-pointer items-center rounded-full border-none bg-transparent p-1.5 text-x-secondary transition-colors hover:bg-x-accent/10 hover:text-x-accent"
        aria-label="シェア"
      >
        <span className="material-symbols-rounded" style={{ fontSize: 18 }}>share</span>
      </button>
      {open && (
        <>
          <div
            onClick={() => setOpen(false)}
            className="fixed inset-0 z-[199]"
          />
          <div
            onClick={(e) => e.stopPropagation()}
            className="absolute bottom-[calc(100%+4px)] right-0 z-[200] min-w-[200px] overflow-hidden rounded-xl bg-x-bg shadow-[0_0_15px_rgba(255,255,255,0.2),0_1px_3px_rgba(0,0,0,0.3)]"
          >
            <button
              onClick={handleX}
              className="flex w-full cursor-pointer items-center gap-3 border-none bg-transparent px-4 py-3 text-left text-[15px] font-bold text-x-text hover:bg-x-hover"
            >
              <span className="text-lg">𝕏</span> Xに投稿する
            </button>
            <button
              onClick={handleCopy}
              className="flex w-full cursor-pointer items-center gap-3 border-none bg-transparent px-4 py-3 text-left text-[15px] font-bold hover:bg-x-hover"
              style={{ color: copied ? "#00ba7c" : "#e7e9ea" }}
            >
              <span className="material-symbols-rounded" style={{ fontSize: 18 }}>{copied ? "check" : "content_copy"}</span>
              {copied ? "コピーしました" : "テキストをコピー"}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
