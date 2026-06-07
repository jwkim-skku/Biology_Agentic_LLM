import { chromium } from "@playwright/test";

const FRONTEND_URL = process.env.FRONTEND_URL ?? "http://127.0.0.1:3000";
const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000/api/v1";
const CHROME_PATHS = [
  process.env.PLAYWRIGHT_CHROME_EXECUTABLE,
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
  "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
].filter(Boolean);

async function main() {
  const executablePath = await firstExistingPath(CHROME_PATHS);
  const launchOptions = executablePath ? { executablePath } : {};
  const browser = await chromium.launch({ headless: true, ...launchOptions });
  const page = await browser.newPage();
  const consoleErrors = [];
  const apiResponses = [];

  page.on("console", (message) => {
    if (message.type() === "error" && !message.text().includes("404")) {
      consoleErrors.push(message.text());
    }
  });
  page.on("response", (response) => {
    if (response.url().startsWith(API_BASE)) {
      apiResponses.push({ url: response.url(), status: response.status() });
    }
  });

  try {
    const metricsReady = waitForApi(page, "/metrics");
    const deploymentReady = waitForApi(page, "/deployment/readiness");
    const productionAuditReady = waitForApi(page, "/deployment/audit", 180_000);
    const productionAuditVerifyReady = waitForApi(page, "/deployment/audit/verify", 180_000);
    const artifactsReady = waitForApi(page, "/artifacts?limit=6");
    const structuredManifestReady = waitForApi(page, "/structured/manifest");
    const dataCoverageReady = waitForApi(page, "/data/coverage");
    const qcBundleSemanticsReady = waitForApi(page, "/artifacts/qc-bundles/semantic-summary?limit=6&verify_files=false");
    const structuredImportSemanticsReady = waitForApi(page, "/artifacts/structured-imports/semantic-summary?limit=6&verify_files=false");
    const ragEvaluationSemanticsReady = waitForApi(page, "/artifacts/rag-evaluations/semantic-summary?limit=6&verify_files=false");
    const optimizerBenchmarkSemanticsReady = waitForApi(page, "/artifacts/optimizer-benchmarks/semantic-summary?limit=6&verify_files=false");
    await page.goto(FRONTEND_URL, { waitUntil: "domcontentloaded", timeout: 60_000 });
    await Promise.all([
      metricsReady,
      deploymentReady,
      productionAuditReady,
      productionAuditVerifyReady,
      artifactsReady,
      structuredManifestReady,
      dataCoverageReady,
      qcBundleSemanticsReady,
      structuredImportSemanticsReady,
      ragEvaluationSemanticsReady,
      optimizerBenchmarkSemanticsReady
    ]);
    await page.waitForTimeout(500);
    await expectSection(page, "Structured data status", /Structured records/);
    const structuredText = await sectionText(page, "Structured data status");
    assert(/Source snapshots\s+\d+%/.test(structuredText), `External source snapshot coverage was not rendered: ${structuredText}`);
    assert(/Payload hashes\s+\d+%/.test(structuredText), `External payload hash coverage was not rendered: ${structuredText}`);
    assert(/Coverage\s+(pass|warning|fail|n\/a)\s+\/\s+live\s+(\d+%|n\/a)\s+\/\s+seed\s+(\d+%|n\/a)/.test(structuredText), `Structured coverage matrix was not rendered: ${structuredText}`);
    assert(/Quality\s+(pass|warning|fail|n\/a)\s+\/\s+block\s+(\d+|n\/a)\s+\/\s+warn\s+(\d+|n\/a)/.test(structuredText), `Structured quality gate was not rendered: ${structuredText}`);
    assert(/Live\s+(\d+%|n\/a)\s+\/\s+release\s+(\d+%|n\/a)/.test(structuredText), `Structured quality live/release fractions were not rendered: ${structuredText}`);
    assert(/Targets\s+G(\d+|n\/a)\s+\/\s+R(\d+|n\/a)\s+\/\s+C(\d+|n\/a)/.test(structuredText), `Structured target counts were not rendered: ${structuredText}`);
    const structuredSection = page.locator('section[aria-label="Structured data status"]');
    const previewReady = waitForApi(page, "/structured/import/preview");
    await structuredSection.getByTitle("Preview structured import impact").click();
    await previewReady;
    const previewText = await sectionText(page, "Structured data status");
    assert(/Import preview\s+(pass|warning|fail|n\/a)/.test(previewText), `Structured import preview was not rendered: ${previewText}`);
    assert(/Projected E(\d+|n\/a)\s+\/\s+W(\d+|n\/a)/.test(previewText), `Structured import projected validation was not rendered: ${previewText}`);
    const refreshValidationReady = waitForApi(page, "/data/refresh/validate");
    await structuredSection.getByTitle("Validate reference refresh plan").click();
    await refreshValidationReady;
    const validatedDataText = await sectionText(page, "Structured data status");
    assert(/Refresh validation\s+(pass|warning|fail)\s+\/\s+ops\s+\d+/.test(validatedDataText), `Reference refresh validation was not rendered: ${validatedDataText}`);
    assert(/Sources\s+(GTEx|Allen Brain Cell Atlas)/.test(validatedDataText), `Reference refresh validation sources were not rendered: ${validatedDataText}`);
    await expectSection(page, "Quality gates", /Quality Gates/);
    const qualityText = await sectionText(page, "Quality gates");
    assert(/Diag\s+(pass|warning|fail|n\/a)/.test(qualityText), `Optimizer diagnostics were not rendered: ${qualityText}`);
    assert(/Constraints\s+(pass|warning|fail|n\/a)/.test(qualityText), `Optimizer constraint band was not rendered: ${qualityText}`);
    assert(/Alg\s+(seeded_nsga2|n\/a)/.test(qualityText), `Optimizer algorithm was not rendered: ${qualityText}`);
    assert(/Seed\s+(deterministic-tradeoff-seeds-v1|n\/a)/.test(qualityText), `Optimizer seed strategy was not rendered in quality gates: ${qualityText}`);
    assert(/Variants\s+(\d+|n\/a)/.test(qualityText), `Optimizer seed variant count was not rendered: ${qualityText}`);
    assert(/Repair\s+(\d+x|off)/.test(qualityText), `Optimizer repair policy was not rendered: ${qualityText}`);
    assert(/Stress\s+(pass|warning|fail|n\/a)/.test(qualityText), `Optimizer stress gate was not rendered: ${qualityText}`);
    await expectSection(page, "RAG inspector", /RAG Inspector/);
    const ragText = await sectionText(page, "RAG inspector");
    assert(/Diagnostics\s+(pass|warning|fail|n\/a)/.test(ragText), `RAG diagnostics were not rendered: ${ragText}`);
    assert(/Regression\s+(pass|warning|fail|n\/a)/.test(ragText), `RAG regression diagnostics were not rendered: ${ragText}`);
    assert(/Vector\s+\w+\s+\/\s+\w+\s+->\s+\w+\s+\/\s+(pass|warning|fail|n\/a)/.test(ragText), `RAG vector readiness was not rendered: ${ragText}`);
    assert(/Payload missing\s+(\d+|n\/a)\s+\/\s+facets\s+(\d+%|n\/a)/.test(ragText), `RAG vector payload readiness was not rendered: ${ragText}`);
    assert(/Runtime\s+(pass|warning|fail|n\/a)\s+\/\s+fallback\s+(yes|no)/.test(ragText), `RAG vector runtime status was not rendered: ${ragText}`);
    assert(/Import plan\s+\w+\s+\/\s+(\d+|n\/a)\s+rows/.test(ragText), `RAG vector import plan was not rendered: ${ragText}`);
    assert(/Vector parity\s+(pass|warning|fail|n\/a)\s+\/\s+hash\s+(match|n\/a)/.test(ragText), `RAG vector parity was not rendered: ${ragText}`);
    assert(/Policy\s+(lexical-window-v2|n\/a)/.test(ragText), `RAG chunking policy was not rendered: ${ragText}`);
    const qualitySection = page.locator('section[aria-label="Quality gates"]');
    const optimizerBundleVerifyReady = waitForApi(page, "/optimizer/benchmark/export/verify");
    await qualitySection.getByTitle("Verify optimizer benchmark audit bundle").click();
    await optimizerBundleVerifyReady;
    const verifiedQualityText = await sectionText(page, "Quality gates");
    assert(/Bundle\s+(pass|warning|fail)/.test(verifiedQualityText), `Optimizer benchmark bundle semantic status was not rendered: ${verifiedQualityText}`);
    assert(/Hash\s+([a-f0-9]{10}|n\/a)/.test(verifiedQualityText), `Optimizer benchmark cases hash was not rendered: ${verifiedQualityText}`);
    assert(/Strategy\s+pass/.test(verifiedQualityText), `Optimizer benchmark search strategy semantic check was not rendered: ${verifiedQualityText}`);
    const ragSection = page.locator('section[aria-label="RAG inspector"]');
    const ragEvaluationReady = waitForApi(page, "/rag/evaluate");
    await ragSection.getByTitle("Run RAG evaluation").click();
    await ragEvaluationReady;
    const evaluatedRagText = await sectionText(page, "RAG inspector");
    assert(/Trace\s+([a-f0-9]{10}|n\/a)/.test(evaluatedRagText), `RAG query fingerprint was not rendered: ${evaluatedRagText}`);
    assert(/Policy\s+(hybrid-score-policy-v2|lexical-window-v2|n\/a)/.test(evaluatedRagText), `RAG ranking policy was not rendered: ${evaluatedRagText}`);
    assert(/Query terms\s+(0\.\d+|1\.00|n\/a)\s+\/\s+missing/.test(evaluatedRagText), `RAG query term coverage was not rendered: ${evaluatedRagText}`);
    assert(/Suff\s+(pass|warning|fail|n\/a)\s+\/\s+src\s+(\d+|n\/a)\s+\/\s+high\s+(\d+|n\/a)/.test(evaluatedRagText), `RAG evidence sufficiency was not rendered: ${evaluatedRagText}`);
    assert(/Facet gaps\s+/.test(evaluatedRagText), `RAG facet gap analysis was not rendered: ${evaluatedRagText}`);
    assert(/hybrid score/.test(evaluatedRagText), `RAG result rationale was not rendered: ${evaluatedRagText}`);
    const ragBundleVerifyReady = waitForApi(page, "/rag/evaluate/export/verify");
    await ragSection.getByTitle("Verify RAG evaluation audit bundle").click();
    await ragBundleVerifyReady;
    const verifiedRagText = await sectionText(page, "RAG inspector");
    assert(/Bundle\s+(pass|warning|fail)/.test(verifiedRagText), `RAG evaluation bundle semantic status was not rendered: ${verifiedRagText}`);
    assert(/Bundle hash\s+([a-f0-9]{10}|n\/a)/.test(verifiedRagText), `RAG evaluation bundle fingerprint was not rendered: ${verifiedRagText}`);
    assert(/Suff check\s+pass/.test(verifiedRagText), `RAG evidence sufficiency semantic check was not rendered: ${verifiedRagText}`);
    const designControls = page.locator('aside[aria-label="Design controls"]');
    await designControls.getByTitle("Use CDS input mode").click();
    await page.getByLabel("Population size").fill("8");
    await page.getByLabel("Generations").fill("1");
    await page.getByLabel("Candidate count").fill("2");
    await page.getByLabel("CDS sequence").fill("ATGGCTGACGAGTTCGCCAAGGGTTACTAA");
    const optimizeReady = waitForApi(page, "/optimize");
    await designControls.getByTitle("Run design").click();
    await optimizeReady;
    await page.waitForTimeout(500);
    const designText = await sectionText(page, "Design results");
    assert(/Candidate Ranking/.test(designText), `Candidate ranking was not rendered after CDS design: ${designText}`);
    assert(/Risk\s+(pass|warning|fail|n\/a)/.test(designText), `Candidate constraint risk was not rendered: ${designText}`);
    assert(/Structure proxy\s*(\d+\.\d+|n\/a)/.test(designText), `Secondary-structure proxy score was not rendered: ${designText}`);
    assert(/rank\s+\d+\s+with composite|selection trace unavailable/.test(designText), `Candidate selection trace was not rendered: ${designText}`);
    assert(/QC Report/.test(designText), `QC report was not rendered after CDS design: ${designText}`);
    assert(/Target data\s+(pass|warning|missing|n\/a)/.test(designText), `Target structured evidence was not rendered: ${designText}`);
    assert(/Seed strategy\s+deterministic-tradeoff-seeds-v1/.test(designText), `Optimizer seed strategy was not rendered: ${designText}`);
    assert(/Best objectives\s*\d+/.test(designText), `Recommendation audit best-objective count was not rendered: ${designText}`);
    assert(/Max regret\s*(\d+\.\d+|n\/a)/.test(designText), `Recommendation audit regret was not rendered: ${designText}`);
    await expectSection(page, "Security controls", /Security/);
    await expectSection(page, "Deployment readiness", /Deployment/);
    await expectSection(page, "Production audit", /Production audit/);
    await expectSection(page, "Storage readiness", /Storage/);
    await expectSection(page, "Governance attestation", /Governance/);
    const agentMemoryText = await sectionText(page, "Agent memory");
    assert(/Agent memory/.test(agentMemoryText), `Agent memory panel was not rendered: ${agentMemoryText}`);
    assert(/Mem\s+\d+/.test(agentMemoryText), `Agent memory count was not rendered: ${agentMemoryText}`);
    assert(/Best objectives\s+(\d+|n\/a)/.test(agentMemoryText), `Agent memory recommendation audit was not rendered: ${agentMemoryText}`);
    assert(/Memory hash\s+([a-f0-9]{12}|n\/a)/.test(agentMemoryText), `Agent memory hash was not rendered: ${agentMemoryText}`);
    await expectSection(page, "Immutable artifact archive", /Artifact archive/);

    const metricsText = await sectionText(page, "Operational metrics");
    assert(/Req\s+\d+/.test(metricsText), `Operational metrics request count was not rendered: ${metricsText}`);
    assert(/RAG\s+\d+\s+chunks/.test(metricsText), `RAG chunk count was not rendered: ${metricsText}`);
    assert(/Data\s+\d+\s+records/.test(metricsText), `Structured data count was not rendered: ${metricsText}`);
    assert(/Memory\s+(\d+|n\/a)\s+\/\s+genes\s+(\d+|n\/a)/.test(metricsText), `Agent memory metrics were not rendered: ${metricsText}`);
    assert(/calls\s*\/\s*avg/.test(metricsText), `Request latency list was not rendered: ${metricsText}`);

    const metricsResponse = apiResponses.find((item) => item.url === `${API_BASE}/metrics`);
    assert(metricsResponse?.status === 200, "Frontend did not load /metrics successfully.");
    assert(
        apiResponses.some((item) => item.url.includes("/agent-memory") && item.status === 200),
        "Frontend did not load agent memory endpoints successfully."
    );
    const deploymentText = await sectionText(page, "Deployment readiness");
    assert(/Status\s+(pass|warning|fail)/.test(deploymentText), `Deployment readiness status was not rendered: ${deploymentText}`);
    assert(/Deploy\s+(ready|hold)/.test(deploymentText), `Deployment readiness deploy flag was not rendered: ${deploymentText}`);
    assert(/QC archive\s+(pass|warning|fail|n\/a)/.test(deploymentText), `Deployment readiness QC archive gate was not rendered: ${deploymentText}`);
    assert(/Import audit\s+(pass|warning|fail|n\/a)/.test(deploymentText), `Deployment readiness import audit gate was not rendered: ${deploymentText}`);
    assert(/tRNA prior\s+(\d+|n\/a)/.test(deploymentText), `Deployment readiness tRNA prior caveat count was not rendered: ${deploymentText}`);
    const deploymentResponse = apiResponses.find((item) => item.url === `${API_BASE}/deployment/readiness`);
    assert(deploymentResponse?.status === 200, "Frontend did not load /deployment/readiness successfully.");
    const productionAuditText = await sectionText(page, "Production audit");
    assert(/Status\s+(pass|warning|fail|n\/a)/.test(productionAuditText), `Production audit status was not rendered: ${productionAuditText}`);
    assert(/Verify\s+(pass|warning|fail|n\/a)/.test(productionAuditText), `Production audit verification was not rendered: ${productionAuditText}`);
    assert(/QC archive\s+(pass|warning|fail|n\/a)/.test(productionAuditText), `Production audit QC archive status was not rendered: ${productionAuditText}`);
    assert(/Import audit\s+(pass|warning|fail|n\/a)/.test(productionAuditText), `Production audit import archive status was not rendered: ${productionAuditText}`);
    assert(/QC semantic pass\s+(\d+|n\/a)\s+\/\s+fail\s+(\d+|n\/a)/.test(productionAuditText), `Production audit QC semantic counts were not rendered: ${productionAuditText}`);
    assert(/Import semantic pass\s+(\d+|n\/a)\s+\/\s+fail\s+(\d+|n\/a)/.test(productionAuditText), `Production audit import semantic counts were not rendered: ${productionAuditText}`);
    assert(/Audit\s+(\d+(\.\d+)?s|n\/a)/.test(productionAuditText), `Production audit timing was not rendered: ${productionAuditText}`);
    assert(/Cache TTL\s+(\d+|n\/a)s/.test(productionAuditText), `Production audit cache policy was not rendered: ${productionAuditText}`);
    const productionAuditResponse = apiResponses.find((item) => item.url === `${API_BASE}/deployment/audit`);
    assert(productionAuditResponse?.status === 200, "Frontend did not load /deployment/audit successfully.");
    const archiveText = await sectionText(page, "Immutable artifact archive");
    assert(/Total\s+\d+/.test(archiveText), `Artifact archive total was not rendered: ${archiveText}`);
    assert(/Ledger\s+(pass|warning|fail|n\/a)/.test(archiveText), `Artifact archive ledger status was not rendered: ${archiveText}`);
    assert(/QC semantic\s+(pass|warning|fail|n\/a)\s+\/\s+checked\s+(\d+|n\/a)/.test(archiveText), `QC bundle archive semantic summary was not rendered: ${archiveText}`);
    assert(/Request\s+(pass|fail|n\/a)\s+\/\s+target\s+(\d+\s+pass\s+\/\s+\d+\s+fail|n\/a)/.test(archiveText), `QC request provenance summary was not rendered: ${archiveText}`);
    assert(/Import audit\s+(pass|warning|fail|n\/a)\s+\/\s+checked\s+(\d+|n\/a)/.test(archiveText), `Structured import audit summary was not rendered: ${archiveText}`);
    assert(/RAG eval archive\s+(pass|warning|fail|n\/a)\s+\/\s+checked\s+(\d+|n\/a)/.test(archiveText), `RAG evaluation archive summary was not rendered: ${archiveText}`);
    assert(/OPT bench archive\s+(pass|warning|fail|n\/a)\s+\/\s+checked\s+(\d+|n\/a)/.test(archiveText), `Optimizer benchmark archive summary was not rendered: ${archiveText}`);
    const archiveSection = page.locator('section[aria-label="Immutable artifact archive"]');
    const verifyButton = archiveSection.getByTitle("Verify archived artifact").first();
    if ((await verifyButton.count()) > 0) {
      const verificationReady = page.waitForResponse(
        (response) => /^\/artifacts\/[^/]+\/verify$/.test(new URL(response.url()).pathname.replace("/api/v1", "")) && response.status() === 200,
        { timeout: 90_000 }
      );
      await verifyButton.click();
      await verificationReady;
      const verifiedArchiveText = await sectionText(page, "Immutable artifact archive");
      assert(/Verify\s+(pass|warning|fail)/.test(verifiedArchiveText), `Archived artifact verification was not rendered: ${verifiedArchiveText}`);
      assert(/Semantic\s+(pass|warning|fail|n\/a)/.test(verifiedArchiveText), `Archived artifact semantic status was not rendered: ${verifiedArchiveText}`);
    }
    assert(consoleErrors.length === 0, `Console errors: ${consoleErrors.join(" | ")}`);

    console.log(JSON.stringify({
      ok: true,
      frontend_url: FRONTEND_URL,
      metrics_response: metricsResponse,
      deployment_response: deploymentResponse,
      production_audit_response: productionAuditResponse,
      checked_sections: [
        "Structured data status",
        "Quality gates",
        "RAG inspector",
        "Security controls",
        "Operational metrics",
        "Deployment readiness",
        "Production audit",
        "Governance attestation",
        "Storage readiness",
        "Immutable artifact archive"
      ]
    }, null, 2));
  } finally {
    await browser.close();
  }
}

async function expectSection(page, label, pattern) {
  const text = await sectionText(page, label);
  assert(pattern.test(text), `Section ${label} did not match ${pattern}: ${text}`);
}

async function sectionText(page, label) {
  return page.evaluate((sectionLabel) => {
    const section = Array.from(document.querySelectorAll("section"))
      .find((item) => item.getAttribute("aria-label") === sectionLabel);
    return section?.textContent ?? "";
  }, label);
}

async function firstExistingPath(paths) {
  const { access } = await import("node:fs/promises");
  for (const candidate of paths) {
    try {
      await access(candidate);
      return candidate;
    } catch {
      // Try the next known browser path.
    }
  }
  return undefined;
}

function waitForApi(page, path, timeout = 90_000) {
  return page.waitForResponse(
    (response) => response.url() === `${API_BASE}${path}` && response.status() === 200,
    { timeout }
  );
}

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
