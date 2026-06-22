import { describe, it, expect } from "vitest";
import { cn, formatNumber, formatCurrency } from "@/lib/utils";

describe("cn", () => {
  it("merges classes", () => {
    expect(cn("a", "b")).toBe("a b");
    expect(cn("a", false && "b", "c")).toBe("a c");
  });
  it("dedupes tailwind classes", () => {
    expect(cn("px-2", "px-4")).toBe("px-4");
  });
});

describe("formatNumber", () => {
  it("formats small numbers", () => { expect(formatNumber(42)).toBe("42"); });
  it("formats k", () => { expect(formatNumber(1500)).toBe("1.5k"); });
  it("formats M", () => { expect(formatNumber(1_500_000)).toBe("1.5M"); });
  it("handles null", () => { expect(formatNumber(null)).toBe("—"); });
});

describe("formatCurrency", () => {
  it("formats USD", () => { expect(formatCurrency(12.5)).toBe("$12.50"); });
  it("handles null", () => { expect(formatCurrency(null)).toBe("—"); });
});
