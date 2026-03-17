import Link from "next/link";
import type { Member, Thread } from "@/types";
import { TrendPanel } from "@/components/sidebar/trend-panel";
import { FollowPanel } from "@/components/sidebar/follow-panel";

type RightSidebarProps = {
  threads: Thread[];
  members: Record<string, Member>;
};

export function RightSidebar({ threads, members }: RightSidebarProps) {
  return (
    <aside className="sticky top-0 hidden h-screen w-[350px] shrink-0 overflow-y-auto pl-7 pr-5 lg:block">
      {/* Search bar — links to search page */}
      <div className="sticky top-0 z-10 bg-x-bg pb-3 pt-2">
        <Link
          href="/search"
          className="flex h-[42px] items-center gap-3 rounded-full bg-x-search px-4 transition-colors hover:bg-x-search/80"
        >
          <span className="material-symbols-rounded text-x-secondary" style={{ fontSize: 20 }}>search</span>
          <span className="text-[15px] text-x-secondary">
            キーワードを検索
          </span>
        </Link>
      </div>

      <div className="space-y-4 pb-20">
        <TrendPanel threads={threads} />
        <FollowPanel members={members} />

        {/* Footer links — X style */}
        <div className="px-4 text-[13px] leading-6 text-x-secondary">
          <Link href="/about" className="mr-3 hover:underline">About</Link>
          <a href="https://github.com/wharfe/open-gikai" target="_blank" rel="noopener noreferrer" className="mr-3 hover:underline">GitHub</a>
          <span className="mr-3">© 2026 OpenGIK<span className="text-x-brand">AI</span></span>
        </div>
      </div>
    </aside>
  );
}
