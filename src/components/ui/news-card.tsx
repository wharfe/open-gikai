"use client";

import { useState } from "react";
import type { NewsArticle } from "@/types";

type NewsCardProps = {
  article: NewsArticle;
};

/**
 * X-style link preview card with OGP image.
 * Shows a large image with source/title overlay at the bottom.
 */
export function NewsCard({ article }: NewsCardProps) {
  const [imgError, setImgError] = useState(false);
  const domain = getDomain(article.url);

  return (
    <a
      href={article.url}
      target="_blank"
      rel="noopener noreferrer"
      className="group block overflow-hidden rounded-2xl border border-x-border transition-colors hover:bg-x-hover"
    >
      {/* Image */}
      {article.image && !imgError ? (
        <div className="relative aspect-[1.91/1] w-full overflow-hidden bg-x-surface">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={article.image}
            alt=""
            className="h-full w-full object-cover"
            loading="lazy"
            onError={() => setImgError(true)}
          />
        </div>
      ) : (
        <div className="flex aspect-[3/1] w-full items-center justify-center bg-x-surface">
          <span
            className="material-symbols-rounded text-x-secondary"
            style={{ fontSize: 32 }}
          >
            newspaper
          </span>
        </div>
      )}

      {/* Text overlay */}
      <div className="px-3 py-2">
        <div className="flex items-center gap-1 text-[13px] text-x-secondary">
          <span
            className="material-symbols-rounded"
            style={{ fontSize: 14 }}
          >
            language
          </span>
          {domain}
        </div>
        <div className="mt-0.5 line-clamp-2 text-[15px] leading-tight text-x-text">
          {article.title}
        </div>
      </div>
    </a>
  );
}

function getDomain(url: string): string {
  try {
    const hostname = new URL(url).hostname;
    return hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}
