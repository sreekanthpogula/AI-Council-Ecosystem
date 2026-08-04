import { Brain, Wrench, Eye, Headphones, Settings } from 'lucide-react';
import './RoutingBanner.css';

const CAPABILITY_META = {
  reasoning: { label: 'Reasoning', Icon: Brain },
  acting: { label: 'Acting / Tools', Icon: Wrench },
  vision: { label: 'Vision', Icon: Eye },
  audio: { label: 'Audio', Icon: Headphones },
};

export default function RoutingBanner({ routing }) {
  if (!routing) return null;

  const meta = CAPABILITY_META[routing.capability] || { label: routing.capability, Icon: Settings };
  const { Icon } = meta;

  return (
    <div className={`routing-banner routing-${routing.capability}`}>
      <span className="routing-badge">
        <Icon size={13} className="routing-icon" /> {meta.label}
      </span>
      {routing.description && <span className="routing-description">{routing.description}</span>}
      {routing.models?.length > 0 && (
        <span className="routing-models">
          Routed to: {routing.models.map((m) => m.split('/')[1] || m).join(', ')}
        </span>
      )}
    </div>
  );
}
