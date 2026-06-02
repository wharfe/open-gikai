export type Ministry = { slug: string; name: string };
export const MINISTRIES: ReadonlyArray<Ministry>;
export function getMemberMinistry(member: {
  id: string;
  role?: string | null;
}): Ministry | null;
