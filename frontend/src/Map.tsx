import { useEffect } from 'react';
import { CircleMarker, MapContainer, Popup, TileLayer, useMap } from 'react-leaflet';
import { latLngBounds } from 'leaflet';
import type { Sponsor } from './types';

function Viewport({ sponsors, selected }: { sponsors: Sponsor[]; selected: Sponsor | null }) {
  const map = useMap();
  useEffect(() => {
    const mapped = sponsors.filter(s => s.latitude !== null && s.longitude !== null);
    if (mapped.length) {
      map.fitBounds(latLngBounds(mapped.map(s => [s.latitude!, s.longitude!])), {
        padding: [45, 45], maxZoom: 10, animate: false,
      });
    }
  }, [map, sponsors]);
  useEffect(() => {
    if (selected?.latitude != null && selected.longitude != null) {
      map.setView([selected.latitude, selected.longitude], 11, { animate: false });
    }
  }, [map, selected]);
  return null;
}

export default function SponsorMap({
  sponsors, selected, onSelect,
}: {
  sponsors: Sponsor[];
  selected: Sponsor | null;
  onSelect: (sponsor: Sponsor) => void;
}) {
  return (
    <MapContainer center={[52.15, 5.3]} zoom={7} className="map" scrollWheelZoom zoomAnimation={false}>
      <TileLayer
        url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
      />
      <Viewport sponsors={sponsors} selected={selected} />
      {sponsors.map(sponsor => sponsor.latitude !== null && sponsor.longitude !== null && (
        <CircleMarker
          key={sponsor.id}
          center={[sponsor.latitude, sponsor.longitude]}
          radius={selected?.id === sponsor.id ? 12 : 8}
          pathOptions={{
            color: '#fff', weight: 3, fillColor: sponsor.job_count ? '#ec6429' : '#63776f',
            fillOpacity: 1,
          }}
          eventHandlers={{ click: () => onSelect(sponsor) }}
        >
          <Popup>
            <strong>{sponsor.name}</strong><br />
            {sponsor.city} &middot; {sponsor.job_count} matching roles<br />
            <button onClick={() => onSelect(sponsor)}>View company details</button>
          </Popup>
        </CircleMarker>
      ))}
    </MapContainer>
  );
}
