import type { Metadata } from "next";
import { Suspense } from "react";
import { MobileHeader } from "@/components/layout/header";
import { SearchView } from "@/components/search/search-view";
import { getSearchIndex } from "@/lib/data";

export const metadata: Metadata = {
  title: "検索",
  description: "国会審議スレッドをキーワード・委員会・議員名で検索。",
};

export default function SearchPage() {
  const entries = getSearchIndex();

  return (
    <>
      <main className="w-full min-w-0 md:border-r md:border-x-border md:max-w-[600px]">
        <MobileHeader />

        <div className="sticky top-[53px] z-40 flex h-[53px] items-center gap-5 bg-x-bg/65 px-4 backdrop-blur-xl md:top-0">
          <div className="text-[17px] font-bold">検索</div>
        </div>

        <Suspense>
          <SearchView entries={entries} />
        </Suspense>
      </main>
    </>
  );
}
