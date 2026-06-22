import { test, expect } from "@playwright/test";

test("landing renders hero and primary CTA", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText(/omnichannel content engine/i);
  await expect(page.getByRole("link", { name: /Start free/i })).toBeVisible();
});

test("sign-in route is reachable", async ({ page }) => {
  await page.goto("/sign-in");
  await expect(page).toHaveURL(/sign-in/);
});
