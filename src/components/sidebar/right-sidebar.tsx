import type { Member, Thread } from "@/types";
import { TrendPanel } from "@/components/sidebar/trend-panel";
import { FollowPanel } from "@/components/sidebar/follow-panel";

type RightSidebarProps = {
  threads: Thread[];
  members: Record<string, Member>;
};

export function RightSidebar({ threads, members }: RightSidebarProps) {
  return (
    <aside className="sticky top-0 hidden h-screen w-[350px] shrink-0 overflow-y-auto lg:block">
      {/* Search bar — X style */}
      <div className="sticky top-0 z-10 bg-x-bg pb-3 pt-1.5">
        <div className="flex h-[42px] items-center gap-3 rounded-full bg-x-search px-4">
          <span className="text-x-secondary">🔍</span>
          <span className="text-[15px] text-x-secondary">
            キーワードを検索
          </span>
        </div>
      </div>

      <div className="space-y-4 pb-20 pr-6">
        <TrendPanel threads={threads} />
        <FollowPanel members={members} />

        {/* Footer links — X style */}
        <div className="px-4 text-[13px] leading-6 text-x-secondary">
          <span className="mr-3 cursor-pointer hover:underline">利用規約</span>
          <span className="mr-3 cursor-pointer hover:underline">プライバシー</span>
          <span className="mr-3 cursor-pointer hover:underline">GitHub</span>
          <span className="mr-3">© 2025 OpenGIKAI</span>
        </div>
      </div>
    </aside>
  );
}
