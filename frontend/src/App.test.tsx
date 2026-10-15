import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from './App';

vi.mock('./Map', () => ({
  default: () => <div data-testid="map">Map</div>,
}));

const sponsor = {
  id: '00000001', kvk: '00000001', name: 'Demo Canal Software', city: 'Amsterdam',
  industry: 'Technology', latitude: 52.36, longitude: 4.9, careers_url: null,
  first_seen: '2026-10-15', last_seen: '2026-10-15',
  job_count: 1, jobs: [{
    id: 'demo:1', title: 'Data Engineer', location: 'Amsterdam', job_type: 'full-time',
    url: 'https://example.com', last_seen: '2026-10-15T04:00:00Z', sponsorship: 'not_stated',
  }],
};
const meta = {
  demo: true, sponsors: 1, mapped_sponsors: 1, fresh_jobs: 1,
  cities: ['Amsterdam'], industries: ['Technology'], registry_stale: false,
  latest_run: null, last_registry_check: '2026-10-15T04:00:00Z', job_freshness_hours: 72,
  source_url: 'https://ind.nl',
};

function mockApi(failure = false) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async input => {
    const url = String(input);
    if (failure) return new Response('unavailable', { status: 503 });
    const body = url.includes('/meta') ? meta : url.includes('/history')
      ? { snapshots: [], events: [], events_limit: 100 }
      : { items: [sponsor], total: 1, offset: 0, limit: 50 };
    return new Response(JSON.stringify(body), { status: 200 });
  });
}

afterEach(() => vi.restoreAllMocks());

describe('sponsor portal', () => {
  it('labels demo data and never offers a fictional job as a real application', async () => {
    mockApi();
    render(<App />);
    expect(await screen.findByText(/All companies and jobs are fictional/)).toBeInTheDocument();
    fireEvent.click(await screen.findByRole('button', { name: /Demo Canal Software/ }));
    expect(screen.getByText('Data Engineer')).toBeInTheDocument();
    expect(screen.getByText(/Visa sponsorship: not stated/)).toBeInTheDocument();
    expect(screen.getByText('Fictional vacancy')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /View role/ })).not.toBeInTheDocument();
  });

  it('sends combined filters to the API and supports reset', async () => {
    const fetch = mockApi();
    render(<App />);
    await screen.findByText(/All companies and jobs are fictional/);
    fireEvent.change(screen.getByLabelText('Search companies or roles'), { target: { value: 'Data' } });
    fireEvent.change(screen.getByLabelText('Company city'), { target: { value: 'Amsterdam' } });
    fireEvent.change(screen.getByLabelText('Industry'), { target: { value: 'Technology' } });
    fireEvent.change(screen.getByLabelText('Job type'), { target: { value: 'internship' } });
    fireEvent.click(screen.getByLabelText('With open roles'));
    await waitFor(() => {
      const urls = fetch.mock.calls.map(([url]) => String(url));
      expect(urls.some(url => url.includes('q=Data') && url.includes('city=Amsterdam')
        && url.includes('industry=Technology') && url.includes('job_type=internship')
        && url.includes('has_jobs=true'))).toBe(true);
    });
    fireEvent.click(screen.getByRole('button', { name: 'Reset' }));
    expect(screen.getByLabelText('Search companies or roles')).toHaveValue('');
    expect(screen.getByLabelText('With open roles')).not.toBeChecked();
  });

  it('shows failures explicitly and permits retry', async () => {
    mockApi(true);
    render(<App />);
    expect(await screen.findByRole('alert')).toHaveTextContent('503');
    mockApi();
    fireEvent.click(screen.getByRole('button', { name: /Retry loading data/ }));
    expect(await screen.findByRole('button', { name: /Demo Canal Software/ })).toBeInTheDocument();
  });

  it('provides lifecycle context and an honest empty history', async () => {
    mockApi();
    render(<App />);
    await screen.findByText(/All companies and jobs are fictional/);
    fireEvent.click(screen.getByRole('button', { name: 'Registry insights' }));
    expect(screen.getByText(/IND publishes updates monthly/)).toBeInTheDocument();
    expect(screen.getByText(/No daily observations yet/)).toBeInTheDocument();
  });
});
