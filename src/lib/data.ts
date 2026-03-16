import type { Member, Thread } from "@/types";
import { THREADS } from "@/data/threads";
import { MEMBERS } from "@/data/members";

export function getThreads(): Thread[] {
  return THREADS;
}

export function getThread(id: string): Thread | undefined {
  return THREADS.find((t) => t.id === id);
}

export function getMembers(): Record<string, Member> {
  return MEMBERS;
}

export function getMember(id: string): Member | undefined {
  return MEMBERS[id];
}

export function getAllThreadIds(): string[] {
  return THREADS.map((t) => t.id);
}

export function getAllMemberIds(): string[] {
  return Object.keys(MEMBERS);
}
