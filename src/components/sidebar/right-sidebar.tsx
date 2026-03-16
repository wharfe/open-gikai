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
      {/* Search bar — X style */}
      <div className="sticky top-0 z-10 bg-x-bg pb-3 pt-2">
        <div className="flex h-[42px] items-center gap-3 rounded-full bg-x-search px-4">
          <span className="text-x-secondary">🔍</span>
          <span className="text-[15px] text-x-secondary">
            キーワードを検索
          </span>
        </div>
      </div>

      <div className="space-y-4 pb-20">
        <TrendPanel threads={threads} />
        <FollowPanel members={members} />

        {/* Footer links — X style */}
        <div className="px-4 text-[13px] leading-6 text-x-secondary">
          <Link href="/about" className="mr-3 hover:underline">について</Link>
          <a href="https://github.com/wharfe/open-gikai" target="_blank" rel="noopener noreferrer" className="mr-3 hover:underline">GitHub</a>
          <span className="mr-3">© 2025 OpenGIK<span className="text-emerald-400">AI</span></span>
        </div>
      </div>
    </aside>
  );
}
