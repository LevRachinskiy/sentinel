import { test, expect } from "@playwright/test";

test("an operator can inspect, approve, and verify a live incident", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(page.getByText("Control plane connected")).toBeVisible();
  await page.getByLabel("Live sandbox").check();
  await page.getByRole("button", { name: "Run scenario" }).click();
  await expect(
    page.getByRole("button", { name: "Approve rollback" }),
  ).toBeVisible({ timeout: 15000 });
  const originalErrors = await page
    .locator(".observations .danger")
    .textContent();
  await page.screenshot({ path: "../docs/dashboard.png", fullPage: true });
  await page.getByRole("button", { name: "Approve rollback" }).click();
  await expect(
    page.getByText("Recovery confirmed", { exact: true }),
  ).toBeVisible({ timeout: 15000 });
  await expect(page.locator(".observations .danger")).toHaveText(
    originalErrors!,
  );
  await page.getByRole("button", { name: "Evidence", exact: false }).click();
  await page.getByRole("button", { name: "deployment", exact: true }).click();
  await expect(page.locator(".json").first()).toContainText("pool_size");
  await page.getByRole("button", { name: "Timeline", exact: true }).click();
  await expect(
    page.getByText("recovery / verified", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Audit trail", exact: true }).click();
  await expect(
    page.getByText("Hash chain is intact", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Replay evaluations", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Run baseline evaluation", exact: true })
    .click();
  await expect(
    page.getByText("100 synthetic cases", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Incidents", exact: false })
    .first()
    .click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page
    .getByRole("button", { name: "Investigation", exact: true })
    .click();
  await page.screenshot({
    path: "../docs/dashboard-mobile.png",
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.getByRole("button", { name: "Audit", exact: true }).click();
  await expect(
    page.getByText("Hash chain is intact", { exact: true }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});
