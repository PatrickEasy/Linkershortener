/**
 * Google Apps Script backend for Linkershortener click statistics.
 *
 * Setup:
 *   1. Create a Google Sheet.  Extensions -> Apps Script.  Paste this file.
 *   2. Deploy -> New deployment -> Web app.  Execute as: Me.  Who has access: Anyone.
 *   3. Copy the web-app URL into .env as STATS_ENDPOINT, run `build`, push.
 *
 * POST  (from redirect pages): appends one row per click.
 * GET   (from `shortener.py stats`, optional ?slug=...): returns all rows as JSON.
 *
 * Re-deploy (Deploy -> Manage deployments -> edit -> new version) after edits.
 */

var SHEET_NAME = "clicks";
var HEADERS = ["timestamp", "slug", "target", "country", "query", "referrer", "userAgent", "language"];

function getSheet_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) {
    sheet = ss.insertSheet(SHEET_NAME);
    sheet.appendRow(HEADERS);
    sheet.setFrozenRows(1);
  }
  return sheet;
}

function doPost(e) {
  var lock = LockService.getScriptLock();
  lock.waitLock(5000);
  try {
    var body = JSON.parse(e.postData.contents || "{}");
    getSheet_().appendRow([
      body.timestamp || new Date().toISOString(),
      body.slug || "",
      body.target || "",
      body.country || "",
      JSON.stringify(body.query || {}),
      body.referrer || "",
      body.userAgent || "",
      body.language || ""
    ]);
    return ContentService.createTextOutput("ok");
  } finally {
    lock.releaseLock();
  }
}

function doGet(e) {
  var slugFilter = e && e.parameter && e.parameter.slug;
  var rows = getSheet_().getDataRange().getValues();
  var out = [];
  for (var i = 1; i < rows.length; i++) {
    var r = rows[i];
    if (slugFilter && r[1] !== slugFilter) continue;
    var query = {};
    try { query = JSON.parse(r[4] || "{}"); } catch (err) {}
    out.push({
      timestamp: r[0] instanceof Date ? r[0].toISOString() : String(r[0]),
      slug: r[1], target: r[2], country: r[3] || null, query: query,
      referrer: r[5] || null, userAgent: r[6], language: r[7] || null
    });
  }
  return ContentService.createTextOutput(JSON.stringify(out))
    .setMimeType(ContentService.MimeType.JSON);
}
