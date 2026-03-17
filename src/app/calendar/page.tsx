import type { Metadata } from "next";
import { MobileHeader } from "@/components/layout/header";
import { CalendarView } from "@/components/calendar/calendar-view";
import { getCalendarData } from "@/lib/data";

export const metadata: Metadata = {
  title: "カレンダー",
  description: "国会審議の日別カレンダー。日付から各委員会のスレッドを閲覧できます。",
};

export default function CalendarPage() {
  const days = getCalendarData();

  return (
    <>
      <main className="w-full min-w-0 md:border-r md:border-x-border md:max-w-[600px]">
        <MobileHeader />

        <div className="sticky top-[53px] z-40 flex h-[53px] items-center gap-5 bg-x-bg/65 px-4 backdrop-blur-xl md:top-0">
          <div className="text-[17px] font-bold">カレンダー</div>
        </div>

        <CalendarView days={days} />
      </main>
    </>
  );
}
