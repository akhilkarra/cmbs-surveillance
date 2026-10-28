// Browser-side extraction used on 2026-10-24 (run in a normal browser tab on https://www.sec.gov,
// because www.sec.gov returned 403 to curl with the project UA). Paste into the devtools console.
// Low volume: ~40 EX-102 fetches total, >=200 ms apart.
//
// loanRows(cik, accession) -> {file, bytes, rp, n, rows}
//   rows: TSV, one per top-level loan, columns:
//   id, endBal, paidThrough, paymentStatus, ssTransferDate, msReturnDate, workoutCode, modCode,
//   liqPrepayCode, propType, finStart, finEnd, mostRecentNOI, mostRecentDSCR_NOI, mostRecentOcc,
//   financialsSecuritizationDate, propertyName(28 chars)
window.loanRows = async function (cik, acc) {
  const base = `https://www.sec.gov/Archives/edgar/data/${cik}/${acc.replace(/-/g, '')}/`;
  const idx = await (await fetch(base + 'index.json')).json();
  const names = idx.directory.item.map(i => i.name);
  const f = names.find(n => /102.*\.xml$/i.test(n)) || names.find(n => /\.xml$/i.test(n));
  const t = await (await fetch(base + f)).text();
  const doc = new DOMParser().parseFromString(t, 'application/xml');
  const g = (el, n) => { for (const c of el.children) if (c.localName === n) return c.textContent.trim(); return ''; };
  const out = []; let rp = '';
  for (const a of doc.documentElement.children) {
    const id = g(a, 'assetNumber'); rp = g(a, 'reportingPeriodEndDate') || rp;
    if (id.includes('-')) continue;                     // property-level sub-assets of portfolios
    const p = [...a.children].find(c => c.localName === 'property');
    const pn = p ? g(p, 'propertyName') : '';
    if (pn === 'NA') continue;                          // pari-passu companion notes (no property data)
    const P = n => (p ? g(p, n) : '');
    out.push([id, g(a, 'reportPeriodEndActualBalanceAmount'), g(a, 'paidThroughDate'), g(a, 'paymentStatusLoanCode'),
      g(a, 'mostRecentSpecialServicerTransferDate'), g(a, 'mostRecentMasterServicerReturnDate'), g(a, 'workoutStrategyCode'),
      g(a, 'modificationCode'), g(a, 'liquidationPrepaymentCode'), P('propertyTypeCode'), P('mostRecentFinancialsStartDate'),
      P('mostRecentFinancialsEndDate'), P('mostRecentNetOperatingIncomeAmount'),
      P('mostRecentDebtServiceCoverageNetOperatingIncomePercentage'), P('mostRecentPhysicalOccupancyPercentage'),
      P('financialsSecuritizationDate'), pn.slice(0, 28)].join('\t'));
  }
  return { file: f, bytes: t.length, rp, n: out.length, rows: out.join('\n') };
};

// Staleness (cross-section): active = endBal>0, not 'Defeased', propType != 'CH';
// staleness = reportingPeriodEndDate - finEnd. Time-series "first-appearance lag" =
// first reportingPeriodEndDate at which a (loan, finEnd) pair appears - finEnd, excluding the
// first month of the window (left-censored). Full code of both loops is in
// src/cmbs/analysis/staleness.py (same definitions) and in research/11-data-sources.md §1.6.
