export type Job = {
  id: string;
  title: string;
  location: string;
  job_type: string;
  url: string;
  last_seen: string;
  sponsorship: string;
};

export type Sponsor = {
  id: string;
  kvk: string | null;
  name: string;
  city: string | null;
  industry: string | null;
  latitude: number | null;
  longitude: number | null;
  careers_url: string | null;
  first_seen: string;
  last_seen: string;
  jobs: Job[];
  job_count: number;
  newly_observed: boolean;
};

export type SponsorPage = { items: Sponsor[]; total: number; limit: number; offset: number };
export type Meta = {
  demo: boolean;
  source_url: string;
  last_registry_check: string | null;
  registry_stale: boolean;
  sponsors: number;
  mapped_sponsors: number;
  fresh_jobs: number;
  job_freshness_hours: number;
  latest_run: { status: string; errors: string[]; boards_succeeded: number } | null;
  cities: string[];
  industries: string[];
};
export type History = {
  snapshots: {
    day: string; sponsors: number; added: number; removed: number;
    restored: number; active_jobs: number;
  }[];
  events: { sponsor_id: string; name: string; day: string; kind: string }[];
  events_limit: number;
};
