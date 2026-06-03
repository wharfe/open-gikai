import { test } from "node:test";
import assert from "node:assert/strict";
import { getMemberMinistry, MINISTRIES } from "../../src/lib/ministry.mjs";

test("bureaucrat with ministry-prefixed role maps to that ministry", () => {
  assert.equal(
    getMemberMinistry({ id: "m_f107fb47", role: "総務省自治税務局長" })?.slug,
    "soumu",
  );
  assert.equal(
    getMemberMinistry({ id: "m_8f446114", role: "海上保安庁次長" })?.slug,
    "jcg",
  );
});

test("内閣官房 and 内閣府 are distinguished", () => {
  assert.equal(
    getMemberMinistry({ id: "m_x", role: "内閣官房内閣審議官" })?.slug,
    "cas",
  );
  assert.equal(
    getMemberMinistry({ id: "m_x", role: "内閣府大臣官房審議官" })?.slug,
    "cao",
  );
});

test("slug-ID politicians are excluded even when role matches a ministry", () => {
  // Real data: 林芳正(内閣官房長官) has a slug ID, not m_
  assert.equal(
    getMemberMinistry({ id: "hayashiyoshimasa", role: "内閣官房長官" }),
    null,
  );
});

test("political titles are excluded even with an m_ id (defense layer)", () => {
  assert.equal(
    getMemberMinistry({ id: "m_evil1", role: "内閣官房長官" }),
    null,
  );
  assert.equal(
    getMemberMinistry({
      id: "m_evil2",
      role: "内閣府特命担当大臣（経済財政政策）",
    }),
    null,
  );
});

test("agency chiefs map regardless of their (noisy) rank field", () => {
  // Real data: 気象庁長官 is rank:"minister" in members.json — rank must NOT
  // be used for filtering (it would drop genuine bureaucrats)
  assert.equal(
    getMemberMinistry({ id: "m_40667609", role: "気象庁長官" })?.slug,
    "jma",
  );
});

test("unmatched or empty roles return null", () => {
  assert.equal(getMemberMinistry({ id: "m_a", role: "中央大学文学部教授" }), null);
  assert.equal(getMemberMinistry({ id: "m_b", role: "" }), null);
  assert.equal(getMemberMinistry({ id: "m_c", role: "委員" }), null);
});

test("every ministry has a unique non-empty slug and name", () => {
  const slugs = new Set(MINISTRIES.map((m) => m.slug));
  const names = new Set(MINISTRIES.map((m) => m.name));
  assert.equal(slugs.size, MINISTRIES.length);
  assert.equal(names.size, MINISTRIES.length);
  for (const m of MINISTRIES) {
    assert.ok(m.slug.length > 0);
    assert.ok(m.name.length > 0);
  }
});
