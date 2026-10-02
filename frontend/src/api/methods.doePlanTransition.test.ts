/**
 * 风险7：doePlanTransition 的 422 判断必须走 ApiError.status 结构化字段，
 * 不再正则扫描 message（message 含 "422" 字样会误判）。
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./http", async (importOriginal) => {
  const orig = await importOriginal<typeof import("./http")>();
  return { ...orig, post: vi.fn() };
});

import { ApiError, post } from "./http";
import { apiMethods } from "./methods";

const mockPost = vi.mocked(post);

beforeEach(() => {
  mockPost.mockReset();
});

describe("doePlanTransition 422 结构化判断", () => {
  it("ApiError status=422 → 重新抛出", async () => {
    mockPost.mockRejectedValueOnce(new ApiError("非法状态迁移", { status: 422 }));
    await expect(
      apiMethods.doePlanTransition("plan-1", "activate")
    ).rejects.toBeInstanceOf(ApiError);
  });

  it("message 含 422 字样但 status 非 422 → 不抛出（fail-open 吞掉）", async () => {
    // 旧正则实现会误判此类 message；新实现只看 status。
    mockPost.mockRejectedValueOnce(
      new ApiError("plan 422xxx 同步异常", { status: 500 })
    );
    await expect(
      apiMethods.doePlanTransition("plan-1", "activate")
    ).resolves.toBeNull();
  });

  it("网络错误（非 ApiError）→ 吞掉保流程", async () => {
    mockPost.mockRejectedValueOnce(new TypeError("fetch failed"));
    await expect(
      apiMethods.doePlanTransition("plan-1", "activate")
    ).resolves.toBeNull();
  });

  it("成功时返回后端结果", async () => {
    mockPost.mockResolvedValueOnce({ plan_id: "plan-1", status: "active" });
    await expect(
      apiMethods.doePlanTransition("plan-1", "activate")
    ).resolves.toEqual({ plan_id: "plan-1", status: "active" });
  });
});
