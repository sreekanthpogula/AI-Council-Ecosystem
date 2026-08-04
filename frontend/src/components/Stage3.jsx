import ReactMarkdown from 'react-markdown';
import { Crown } from 'lucide-react';
import './Stage3.css';

export default function Stage3({ finalResponse }) {
  if (!finalResponse) {
    return null;
  }

  return (
    <div className="stage stage3">
      <h3 className="stage-title"><Crown size={16} /> Stage 3: Final Council Answer</h3>
      <div className="final-response">
        <div className="chairman-label">
          <Crown size={13} className="chairman-crown" /> Chairman: {finalResponse.model.split('/')[1] || finalResponse.model}
        </div>
        <div className="final-text markdown-content">
          <ReactMarkdown>{finalResponse.response}</ReactMarkdown>
        </div>
      </div>
    </div>
  );
}
