import { getThreads, getMembers } from "@/lib/data";
import { FeedView } from "@/components/feed/feed-view";
import { TrendPanel } from "@/components/sidebar/trend-panel";
import { FollowPanel } from "@/components/sidebar/follow-panel";

export default function Home() {
  const threads = getThreads();
  const members = getMembers();

  return (
    <div className="flex items-start gap-5">
      <div className="min-w-0 flex-1">
        <FeedView threads={threads} members={members} />
      </div>
      <div className="hidden w-64 shrink-0 md:block">
        <TrendPanel threads={threads} />
        <FollowPanel members={members} />
      </div>
    </div>
  );
}
