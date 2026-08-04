import { useState, useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import { Paperclip, SendHorizontal, X } from 'lucide-react';
import Stage1 from './Stage1';
import Stage2 from './Stage2';
import Stage3 from './Stage3';
import RoutingBanner from './RoutingBanner';
import { fileToBase64 } from '../api';
import './ChatInterface.css';

const ACCEPTED_ATTACHMENT_TYPES = 'image/*,video/*,audio/*';

function CreditFooter() {
  return (
    <div className="credit-footer">
      Built by <strong>Sreekanth Pogula</strong>
    </div>
  );
}

function AttachmentPreview({ attachment }) {
  if (!attachment) return null;
  const src = `data:${attachment.mime_type};base64,${attachment.base64}`;
  const category = attachment.mime_type.split('/')[0];

  return (
    <div className="attachment-preview-inline">
      {category === 'image' && <img src={src} alt={attachment.name || 'attachment'} />}
      {category === 'video' && <video src={src} controls />}
      {category === 'audio' && <audio src={src} controls />}
    </div>
  );
}

export default function ChatInterface({
  conversation,
  onSendMessage,
  isLoading,
}) {
  const [input, setInput] = useState('');
  const [pendingAttachment, setPendingAttachment] = useState(null); // { base64, mimeType, name }
  const messagesEndRef = useRef(null);
  const fileInputRef = useRef(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [conversation]);

  const handleFileChange = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const base64 = await fileToBase64(file);
    setPendingAttachment({ base64, mimeType: file.type, name: file.name });
    e.target.value = '';
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    if (input.trim() && !isLoading) {
      onSendMessage(input, pendingAttachment);
      setInput('');
      setPendingAttachment(null);
    }
  };

  const handleKeyDown = (e) => {
    // Submit on Enter (without Shift)
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  if (!conversation) {
    return (
      <div className="chat-interface">
        <div className="hero-empty-state">
          <img src="/header.jpg" alt="AI-Council" className="hero-image" />
          <div className="hero-overlay">
            <span className="hero-eyebrow">Boss-routed &middot; Multimodal &middot; Free-tier</span>
            <h2 className="hero-title">Welcome to AI-Council</h2>
            <p className="hero-subtitle">Create a new conversation to route your question to the right AI council</p>
          </div>
        </div>
        <CreditFooter />
      </div>
    );
  }

  return (
    <div className="chat-interface">
      <div className="messages-container">
        {conversation.messages.length === 0 ? (
          <div className="hero-empty-state hero-empty-state-compact">
            <img src="/header.jpg" alt="AI-Council" className="hero-image" />
            <div className="hero-overlay">
              <h2 className="hero-title">Start a conversation</h2>
              <p className="hero-subtitle">Ask a question, or attach an image/audio/video - the boss model will route it to the right capability</p>
            </div>
          </div>
        ) : (
          conversation.messages.map((msg, index) => (
            <div key={index} className="message-group fade-in-up">
              {msg.role === 'user' ? (
                <div className="user-message">
                  <div className="message-label">You</div>
                  <div className="message-content">
                    {msg.attachment && <AttachmentPreview attachment={msg.attachment} />}
                    <div className="markdown-content">
                      <ReactMarkdown>{msg.content}</ReactMarkdown>
                    </div>
                  </div>
                </div>
              ) : (
                <div className="assistant-message">
                  <div className="message-label">AI-Council</div>

                  {/* Routing */}
                  {msg.loading?.routing && (
                    <div className="stage-loading">
                      <div className="spinner"></div>
                      <span>Boss model is routing your request...</span>
                    </div>
                  )}
                  {(msg.routing || msg.capability) && (
                    <RoutingBanner
                      routing={
                        msg.routing || {
                          capability: msg.capability,
                          description: '',
                          models: (msg.stage1 || []).map((r) => r.model),
                        }
                      }
                    />
                  )}

                  {/* Stage 1 */}
                  {msg.loading?.stage1 && (
                    <div className="stage-loading">
                      <div className="spinner"></div>
                      <span>Running Stage 1: Collecting individual responses...</span>
                    </div>
                  )}
                  {msg.stage1 && <Stage1 responses={msg.stage1} />}

                  {/* Stage 2 */}
                  {msg.loading?.stage2 && (
                    <div className="stage-loading">
                      <div className="spinner"></div>
                      <span>Running Stage 2: Peer rankings...</span>
                    </div>
                  )}
                  {msg.stage2 && (
                    <Stage2
                      rankings={msg.stage2}
                      labelToModel={msg.metadata?.label_to_model}
                      aggregateRankings={msg.metadata?.aggregate_rankings}
                    />
                  )}

                  {/* Stage 3 */}
                  {msg.loading?.stage3 && (
                    <div className="stage-loading">
                      <div className="spinner"></div>
                      <span>Running Stage 3: Final synthesis...</span>
                    </div>
                  )}
                  {msg.stage3 && <Stage3 finalResponse={msg.stage3} />}
                </div>
              )}
            </div>
          ))
        )}

        {isLoading && (
          <div className="loading-indicator">
            <div className="spinner"></div>
            <span>Consulting the council...</span>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {conversation.messages.length === 0 && (
        <form className="input-form" onSubmit={handleSubmit}>
          <div className="input-column">
            {pendingAttachment && (
              <div className="attachment-chip">
                <span>{pendingAttachment.name}</span>
                <button
                  type="button"
                  className="attachment-chip-remove"
                  onClick={() => setPendingAttachment(null)}
                  aria-label="Remove attachment"
                >
                  <X size={14} />
                </button>
              </div>
            )}
            <textarea
              className="message-input"
              placeholder="Ask your question... (Shift+Enter for new line, Enter to send)"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={isLoading}
              rows={3}
            />
          </div>
          <input
            type="file"
            ref={fileInputRef}
            accept={ACCEPTED_ATTACHMENT_TYPES}
            onChange={handleFileChange}
            style={{ display: 'none' }}
          />
          <button
            type="button"
            className="attach-button"
            onClick={() => fileInputRef.current?.click()}
            disabled={isLoading}
            title="Attach an image, audio, or video file"
          >
            <Paperclip size={18} />
          </button>
          <button
            type="submit"
            className="send-button"
            disabled={!input.trim() || isLoading}
          >
            Send <SendHorizontal size={16} />
          </button>
        </form>
      )}

      <CreditFooter />
    </div>
  );
}
