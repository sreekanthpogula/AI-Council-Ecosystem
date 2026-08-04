import { useState, useRef, useEffect } from 'react';
import { Zap, Plus, Pencil, Trash2, Check, X, GitFork } from 'lucide-react';
import { version as APP_VERSION } from '../../package.json';
import './Sidebar.css';

function ConversationItem({ conv, isActive, onSelect, onRename, onDelete }) {
  const [isEditing, setIsEditing] = useState(false);
  const [draftTitle, setDraftTitle] = useState(conv.title || 'New Conversation');
  const inputRef = useRef(null);

  useEffect(() => {
    if (isEditing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [isEditing]);

  const startEditing = (e) => {
    e.stopPropagation();
    setDraftTitle(conv.title || 'New Conversation');
    setIsEditing(true);
  };

  const commitRename = () => {
    const trimmed = draftTitle.trim();
    setIsEditing(false);
    if (trimmed && trimmed !== conv.title) {
      onRename(conv.id, trimmed);
    }
  };

  const cancelRename = () => {
    setIsEditing(false);
    setDraftTitle(conv.title || 'New Conversation');
  };

  const handleDelete = (e) => {
    e.stopPropagation();
    if (window.confirm('Delete this conversation? This cannot be undone.')) {
      onDelete(conv.id);
    }
  };

  return (
    <div
      className={`conversation-item ${isActive ? 'active' : ''}`}
      onClick={() => !isEditing && onSelect(conv.id)}
    >
      {isEditing ? (
        <div className="conversation-edit-row" onClick={(e) => e.stopPropagation()}>
          <input
            ref={inputRef}
            className="conversation-edit-input"
            value={draftTitle}
            onChange={(e) => setDraftTitle(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') commitRename();
              if (e.key === 'Escape') cancelRename();
            }}
            onBlur={commitRename}
          />
          <button className="conversation-action-btn" onClick={commitRename} aria-label="Save title">
            <Check size={14} />
          </button>
          <button className="conversation-action-btn" onClick={cancelRename} aria-label="Cancel">
            <X size={14} />
          </button>
        </div>
      ) : (
        <>
          <div className="conversation-title">{conv.title || 'New Conversation'}</div>
          <div className="conversation-meta">{conv.message_count} messages</div>
          <div className="conversation-actions">
            <button className="conversation-action-btn" onClick={startEditing} aria-label="Rename conversation">
              <Pencil size={13} />
            </button>
            <button className="conversation-action-btn conversation-action-danger" onClick={handleDelete} aria-label="Delete conversation">
              <Trash2 size={13} />
            </button>
          </div>
        </>
      )}
    </div>
  );
}

export default function Sidebar({
  conversations,
  currentConversationId,
  onSelectConversation,
  onNewConversation,
  onRenameConversation,
  onDeleteConversation,
  onGoHome,
}) {
  return (
    <div className="sidebar">
      <div className="sidebar-header">
        <button className="brand" onClick={onGoHome} title="Go to home">
          <span className="brand-mark">
            <Zap size={15} strokeWidth={2.5} />
          </span>
          <h1>
            AI-<span className="brand-gradient">Council</span>
            <sup className="brand-trademark">™</sup>
          </h1>
        </button>
        <button className="new-conversation-btn" onClick={onNewConversation}>
          <Plus size={16} strokeWidth={2.5} /> New Conversation
        </button>
      </div>

      <div className="conversation-list">
        {conversations.length === 0 ? (
          <div className="no-conversations">No conversations yet</div>
        ) : (
          conversations.map((conv) => (
            <ConversationItem
              key={conv.id}
              conv={conv}
              isActive={conv.id === currentConversationId}
              onSelect={onSelectConversation}
              onRename={onRenameConversation}
              onDelete={onDeleteConversation}
            />
          ))
        )}
      </div>

      <div className="sidebar-bottom">
        <a
          className="sidebar-footer"
          href="https://github.com/karpathy/llm-council"
          target="_blank"
          rel="noopener noreferrer"
        >
          <GitFork size={13} />
          <span>Inspired by Andrej Karpathy&rsquo;s <strong>llm-council</strong></span>
        </a>
        <div className="sidebar-version">AI-Council&trade; v{APP_VERSION}</div>
      </div>
    </div>
  );
}
