/**
 * Missouri Rut Tracker: field reports and roadkill logs in a Google Sheet.
 *
 * Setup (once):
 *   1. In the Sheet: Extensions > Apps Script, paste this file over Code.gs, save.
 *   2. Pick "setup" in the function menu and press Run. Approve the permissions.
 *      It creates the "reports" and "roadkill" tabs and prints the access KEY in the log.
 *   3. Deploy > New deployment > type "Web app". Execute as: Me. Who has access: Anyone.
 *      Copy the Web app URL (ends in /exec).
 *   4. Put the URL and KEY into tools/make-config.html to make config.enc.json for the site.
 *
 * The page sends:
 *   GET  ?action=list&key=KEY                       -> { ok, reports: [...], roadkill: [...] }
 *   POST {action:'sync', key, kind, token, items}  -> replaces that browser's rows of that kind
 * Each browser has a random token; rows store only a hash of it (owner), so a browser can change
 * only its own rows. You can edit or delete any row here in the Sheet; the page picks it up within a minute.
 */
const COLS = {
  reports: ['id', 'date', 'county', 'fips', 'division', 'stage', 'count', 'sex', 'time', 'source', 'notes', 'entered', 'owner', 'received'],
  roadkill: ['id', 'date', 'county', 'fips', 'n', 'kind', 'sex', 'notes', 'entered', 'owner', 'received'],
};
const MAX_PER_BROWSER = 500;

function setup() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  Object.keys(COLS).forEach(function (name) {
    let sh = ss.getSheetByName(name);
    if (!sh) sh = ss.insertSheet(name);
    sh.getRange(1, 1, 1, COLS[name].length).setValues([COLS[name]]).setFontWeight('bold');
    sh.getRange(1, 1, sh.getMaxRows(), COLS[name].length).setNumberFormat('@'); // keep dates and FIPS as text
    sh.setFrozenRows(1);
  });
  const props = PropertiesService.getScriptProperties();
  let key = props.getProperty('KEY');
  if (!key) { key = Utilities.getUuid().replace(/-/g, ''); props.setProperty('KEY', key); }
  Logger.log('KEY: ' + key);
}

function doGet(e) { return handle_((e && e.parameter) || {}); }
function doPost(e) {
  let body = {};
  try { body = JSON.parse((e && e.postData && e.postData.contents) || '{}'); } catch (err) { return out_({ ok: false, error: 'bad_json' }); }
  return handle_(body);
}

function handle_(p) {
  const key = PropertiesService.getScriptProperties().getProperty('KEY');
  if (!key || p.key !== key) return out_({ ok: false, error: 'bad_key' });
  if (p.action === 'list') return out_({ ok: true, reports: read_('reports'), roadkill: read_('roadkill') });
  if (p.action === 'sync') return sync_(p);
  return out_({ ok: false, error: 'bad_action' });
}

function read_(name) {
  const sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(name);
  if (!sh || sh.getLastRow() < 2) return [];
  const vals = sh.getRange(1, 1, sh.getLastRow(), sh.getLastColumn()).getDisplayValues();
  const head = vals[0];
  return vals.slice(1).filter(function (r) { return r[0]; }).map(function (r) {
    const o = {}; head.forEach(function (h, i) { if (h && h !== 'received') o[h] = r[i]; }); return o;
  });
}

function sync_(p) {
  const kind = p.kind;
  if (!COLS[kind]) return out_({ ok: false, error: 'bad_kind' });
  if (typeof p.token !== 'string' || p.token.length < 16 || p.token.length > 64) return out_({ ok: false, error: 'bad_token' });
  const items = Array.isArray(p.items) ? p.items : [];
  if (items.length > MAX_PER_BROWSER) return out_({ ok: false, error: 'too_many' });
  const owner = hash_(p.token);
  const cols = COLS[kind];
  const now = new Date().toISOString();
  const rows = items.filter(function (x) { return x && /^\d{4}-\d{2}-\d{2}$/.test(String(x.date)) && /^29\d{3}$/.test(String(x.fips)); })
    .map(function (x) {
      return cols.map(function (c) {
        if (c === 'owner') return owner;
        if (c === 'received') return now;
        if (c === 'county') return clean_(x.county || '');
        if (c === 'division') return clean_(x.div || x.division || '');
        return clean_(x[c] == null ? '' : x[c]);
      });
    });
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    const sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(kind);
    if (!sh) return out_({ ok: false, error: 'run_setup' });
    const last = sh.getLastRow();
    const ownerCol = cols.indexOf('owner');
    const keep = last >= 2 ? sh.getRange(2, 1, last - 1, cols.length).getDisplayValues().filter(function (r) { return r[0] && r[ownerCol] !== owner; }) : [];
    // keep the "received" time for rows this browser already had
    const had = {};
    if (last >= 2) sh.getRange(2, 1, last - 1, cols.length).getDisplayValues().forEach(function (r) { if (r[ownerCol] === owner) had[r[0]] = r[cols.indexOf('received')]; });
    rows.forEach(function (r) { if (had[r[0]]) r[cols.indexOf('received')] = had[r[0]]; });
    const all = keep.concat(rows).sort(function (a, b) { return a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0; });
    if (last >= 2) sh.getRange(2, 1, last - 1, cols.length).clearContent();
    if (all.length) { const rg = sh.getRange(2, 1, all.length, cols.length); rg.setNumberFormat('@'); rg.setValues(all); }
  } finally { lock.releaseLock(); }
  return out_({ ok: true, n: rows.length });
}

function clean_(v) {
  let s = String(v).slice(0, 300);
  if (/^[=+\-@]/.test(s)) s = "'" + s; // never let a note run as a formula
  return s;
}
function hash_(token) {
  return Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, token, Utilities.Charset.UTF_8)
    .map(function (b) { return ('0' + ((b + 256) % 256).toString(16)).slice(-2); }).join('').slice(0, 24);
}
function out_(o) { return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON); }
