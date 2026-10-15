import { useEffect, useState } from 'react';
import { fetchJson } from './api';
import SponsorMap from './Map';
import type { History, Meta, Sponsor, SponsorPage } from './types';

const emptyFilters = { q: '', city: '', industry: '', job_type: '', has_jobs: false };
const pageSize = 50;
const formatDate = (value: string) => new Date(value).toLocaleDateString(undefined, {
  day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC',
});

export default function App() {
  const [filters, setFilters] = useState(emptyFilters);
  const [offset, setOffset] = useState(0);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [history, setHistory] = useState<History | null>(null);
  const [page, setPage] = useState<SponsorPage | null>(null);
  const [selected, setSelected] = useState<Sponsor | null>(null);
  const [view, setView] = useState<'explore' | 'history'>('explore');
  const [metaError, setMetaError] = useState('');
  const [searchError, setSearchError] = useState('');
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setMeta(null);
    setHistory(null);
    setMetaError('');
    Promise.all([
      fetchJson<Meta>('/api/meta', controller.signal),
      fetchJson<History>('/api/history', controller.signal),
    ]).then(([nextMeta, nextHistory]) => {
      setMeta(nextMeta);
      setHistory(nextHistory);
    }).catch((cause: Error) => {
      if (!controller.signal.aborted) setMetaError(cause.message);
    });
    return () => controller.abort();
  }, [retry]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setSearchError('');
    setSelected(null);
    setPage(null);
    const timer = window.setTimeout(() => {
      const params = new URLSearchParams({ limit: String(pageSize), offset: String(offset) });
      for (const [key, value] of Object.entries(filters)) {
        if (value) params.set(key, String(value));
      }
      fetchJson<SponsorPage>(`/api/sponsors?${params}`, controller.signal)
        .then(setPage)
        .catch((cause: Error) => {
          if (!controller.signal.aborted) setSearchError(cause.message);
        })
        .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }, 250);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [filters, offset, retry]);

  function updateFilter(key: keyof typeof emptyFilters, value: string | boolean) {
    setFilters(current => ({ ...current, [key]: value }));
    setOffset(0);
  }

  const mapped = page?.items.filter(s => s.latitude !== null && s.longitude !== null).length ?? 0;
  const error = metaError || searchError;

  return (
    <div className="app">
      <header className="header">
        <a className="brand" href="./" aria-label="Oranje Sponsor Radar home">
          <span className="brand-icon" aria-hidden="true">o<span /></span>
          <span>oranje<span className="brand-sub">SPONSOR RADAR</span></span>
        </a>
        <nav aria-label="Main navigation">
          <button className={view === 'explore' ? 'nav-active' : ''} onClick={() => setView('explore')}>
            Explore sponsors
          </button>
          <button className={view === 'history' ? 'nav-active' : ''} onClick={() => setView('history')}>
            Registry insights
          </button>
        </nav>
        <a className="source-link" href={meta?.source_url ?? 'https://ind.nl/en/public-register-recognised-sponsors/public-register-work'}
          target="_blank" rel="noreferrer">Official IND register &#8599;</a>
      </header>

      {meta?.demo && <div className="demo-banner" role="status">
        DEMO MODE &mdash; All companies and jobs are fictional. No live IND verification or real vacancies.
      </div>}
      <main>
        <section className="intro">
          <div>
            <p className="eyebrow">YOUR NEXT CHAPTER, IN THE NETHERLANDS</p>
            <h1>A clearer path to<br /><span>your next opportunity.</span></h1>
            <p className="intro-copy">Discover recognised sponsors. Explore open roles.<br />
              Spend less time guessing, more time finding your place.</p>
          </div>
          <div className="stats" aria-label="Platform coverage">
            <div><strong>{meta?.sponsors.toLocaleString() ?? '--'}</strong>
              <span>{meta?.demo ? 'Demo companies' : 'Recognised sponsors'}</span></div>
            <div><strong>{meta?.fresh_jobs.toLocaleString() ?? '--'}</strong>
              <span>Recently checked roles</span></div>
            <div><strong>{meta?.mapped_sponsors.toLocaleString() ?? '--'}</strong>
              <span>Companies on the map</span></div>
          </div>
        </section>

        <aside className="notice">
          <span className="notice-icon" aria-hidden="true">i</span>
          <p><strong>Recognised sponsor does not mean every role offers sponsorship.</strong>{' '}
            Recognition is employer-level authorisation. Confirm visa support directly with the hiring team.</p>
        </aside>
        {meta?.registry_stale && <p className="warning" role="status">
          Registry observations are missing or over two days old. Check the official register before applying.
        </p>}
        {meta?.latest_run && meta.latest_run.status !== 'success' && (
          <details className="warning">
            <summary>Latest data refresh: {meta.latest_run.status}. Some records may be out of date.</summary>
            {meta.latest_run.errors.map((message, i) => <p key={i}>{message}</p>)}
          </details>
        )}
        {error && <div className="error" role="alert">{error}{' '}
          <button onClick={() => setRetry(value => value + 1)}>Retry loading data</button>
        </div>}

        {view === 'explore' ? <>
          <form className="filters" onSubmit={event => event.preventDefault()} aria-label="Search sponsors">
            <label className="search-field">Search companies or roles
              <input type="search" placeholder="Company name, data engineer..."
                value={filters.q} onChange={event => updateFilter('q', event.target.value)} />
            </label>
            <label>Company city
              <select value={filters.city} onChange={event => updateFilter('city', event.target.value)}>
                <option value="">All cities</option>
                {meta?.cities.map(city => <option key={city}>{city}</option>)}
              </select>
            </label>
            <label>Industry
              <select value={filters.industry} onChange={event => updateFilter('industry', event.target.value)}>
                <option value="">All industries</option>
                {meta?.industries.map(industry => <option key={industry}>{industry}</option>)}
              </select>
            </label>
            <label>Job type
              <select value={filters.job_type} onChange={event => updateFilter('job_type', event.target.value)}>
                <option value="">All job types</option>
                <option value="full-time">Full-time</option>
                <option value="part-time">Part-time</option>
                <option value="internship">Internship</option>
                <option value="contract">Contract</option>
                <option value="unknown">Not specified</option>
              </select>
            </label>
            <label className="checkbox-field">
              <input type="checkbox" checked={filters.has_jobs}
                onChange={event => updateFilter('has_jobs', event.target.checked)} />
              With open roles
            </label>
            <button className="text-button" type="button"
              onClick={() => { setFilters(emptyFilters); setOffset(0); }}>Reset</button>
          </form>

          <div className="explorer">
            <section className="results" aria-label="Matching sponsors" aria-busy={loading}>
              <div className="results-heading">
                <h2>{loading || !meta ? 'Finding sponsors...' : `${page?.total.toLocaleString() ?? 0} sponsors`}</h2>
                <span>IN THE NETHERLANDS</span>
              </div>
              <div className="company-list">
                {!loading && meta && page?.total === 0 && <div className="empty">
                  <h3>No sponsors found</h3>
                  <p>Try a broader search or reset your filters. Live map and job coverage require verified company mappings.</p>
                </div>}
                {meta && page?.items.map(sponsor => (
                  <button className={`company-card ${selected?.id === sponsor.id ? 'selected' : ''}`}
                    key={sponsor.id} onClick={() => setSelected(sponsor)}>
                    <span className="company-top"><span className="company-avatar">{sponsor.name.charAt(0)}</span>
                      <span className="recognition-badge">{meta?.demo ? 'Demo sponsor' : 'IND recognised'}</span>
                    </span>
                    <h3>{sponsor.name}</h3>
                    {sponsor.newly_observed && <span className="recognition-badge">
                      Newly observed in the last 30 days
                    </span>}
                    <p>{sponsor.city ?? 'Location not enriched'} &middot; {sponsor.industry ?? 'Industry not enriched'}</p>
                    <span className="company-bottom">
                      <span className={sponsor.job_count ? 'jobs-count' : 'muted'}>
                        {sponsor.job_count ? `${sponsor.job_count} matching open roles` : 'No tracked open roles'}
                      </span>
                      <span aria-hidden="true">&rarr;</span>
                    </span>
                  </button>
                ))}
              </div>
              {page && page.total > pageSize && <div className="pagination">
                <button disabled={!offset || loading} onClick={() => setOffset(value => value - pageSize)}>Previous</button>
                <span>{offset + 1}&ndash;{Math.min(offset + pageSize, page.total)} of {page.total}</span>
                <button disabled={offset + pageSize >= page.total || loading}
                  onClick={() => setOffset(value => value + pageSize)}>Next</button>
              </div>}
            </section>
            <section className="map-panel" aria-label="Sponsor locations">
              <SponsorMap sponsors={meta ? page?.items ?? [] : []} selected={selected} onSelect={setSelected} />
              <div className="map-caption">
                <span><i className="dot orange" /> With matching roles</span>
                <span><i className="dot green" /> Sponsor only</span>
                <span>{meta ? mapped : 0} mapped on this page</span>
              </div>
            </section>
          </div>

          {selected && <section className="details" aria-label="Company details">
            <div className="detail-heading"><div>
              <p className="eyebrow">COMPANY SPOTLIGHT &middot; {selected.kvk ? `KVK ${selected.kvk}` : 'No KVK published'}</p>
              <h2>{selected.name}</h2>
            </div><button onClick={() => setSelected(null)} aria-label="Close company details">Close &times;</button></div>
            <p>Last observed in {meta?.demo ? 'demo data' : 'the IND work register'}: {formatDate(selected.last_seen)}.
              {' '}Map coordinates represent the configured company location, not necessarily the job location.</p>
            {selected.careers_url && <a href={selected.careers_url} target="_blank" rel="noreferrer">Company careers page &#8599;</a>}
            {!selected.jobs.length && <p>No matching jobs tracked. This does not mean the company is not hiring.</p>}
            {selected.jobs.map(job => <article className="job" key={job.id}>
              <div><h3>{job.title}</h3><p>{job.location} &middot; {job.job_type === 'unknown' ? 'Type not specified' : job.job_type}
                {' '}&middot; Checked {formatDate(job.last_seen)}</p>
                <span className="sponsorship-note">Visa sponsorship: not stated / not verified</span>
              </div>
              {meta?.demo ? <span className="muted">Fictional vacancy</span>
                : <a href={job.url} target="_blank" rel="noreferrer">View role &#8599;</a>}
            </article>)}
            {selected.job_count > selected.jobs.length && <p>Showing the first {selected.jobs.length} matching roles.
              Visit the company careers page for the complete list.</p>}
          </section>}
        </> : <section className="history-panel">
          <p className="eyebrow">REGISTRY OBSERVATIONS</p><h2>How the sponsor landscape changes</h2>
          <p>Last 30 days. Dates are when we observed a change, not the legal effective date.
            The IND publishes updates monthly; our daily checks do not make the register real-time.
            Initial imports are a baseline, not newly recognised sponsors.</p>
          {!history ? <p>History is loading or unavailable. Use Retry if a request failed.</p> : <>
            <div className="table-scroll"><table>
              <thead><tr><th>Date (UTC)</th><th>Sponsors</th><th>Added</th><th>Removed</th><th>Restored</th><th>Tracked jobs</th></tr></thead>
              <tbody>{history.snapshots.map(row => <tr key={row.day}>
                <td>{formatDate(row.day)}</td><td>{row.sponsors.toLocaleString()}</td>
                <td>{row.added}</td><td>{row.removed}</td><td>{row.restored}</td><td>{row.active_jobs}</td>
              </tr>)}</tbody>
            </table></div>
            {!history.snapshots.length && <p>No daily observations yet. Run the first data refresh.</p>}
            <h3>Recent changes</h3>
            <p>Latest {history.events_limit} changes within this period. Removal means no longer listed;
              it is not a verified revocation decision.</p>
            {history.events.length ? <ul className="events">{history.events.map(event =>
              <li key={`${event.sponsor_id}-${event.day}-${event.kind}`}>
                <span className={`event-kind ${event.kind}`}>{event.kind}</span>
                <strong>{event.name}</strong><span>{formatDate(event.day)}</span>
              </li>)}</ul> : <p>No changes observed in this period.</p>}
          </>}
        </section>}
      </main>
      <footer>
        <span>Built for your next chapter.</span>
        <span>Registry checked: {meta?.last_registry_check ? formatDate(meta.last_registry_check) : 'not yet'}
          {' '}&middot; Jobs shown if checked within {meta?.job_freshness_hours ?? 72} hours.</span>
        <span>Independent project. Not affiliated with the IND.</span>
      </footer>
    </div>
  );
}
