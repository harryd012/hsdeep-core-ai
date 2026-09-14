'use client';

import { useAiCtoStatus } from '@/hooks/useAiCtoStatus';

export default function AICTOStatusCard() {
  const { data, loading, error } = useAiCtoStatus();

  if (error || !data) {
    return (
      <div className="ai-cto-card ai-cto-card--error">
        <div className="ai-cto-card__status-row">
          <span className="ai-cto-card__title">AI CTO</span>
          <span className="ai-cto-card__badge ai-cto-card__badge--error">ERROR</span>
        </div>
        <div className="ai-cto-card__services">
          <span className="ai-cto-card__service">system unavailable</span>
        </div>
      </div>
    );
  }

  const ctoDegraded = data.ai_cto.status === 'degraded';

  return (
    <div className="ai-cto-card">
      {/* AI CTO header */}
      <div className="ai-cto-card__status-row">
        <span className="ai-cto-card__title">AI CTO</span>
        <span className={`ai-cto-card__badge ai-cto-card__badge--${ctoDegraded ? 'degraded' : 'healthy'}`}>
          {ctoDegraded ? 'DEGRADED' : 'HEALTHY'}
        </span>
      </div>

      {/* Degradation reason (only shown when degraded) */}
      {ctoDegraded && data.ai_cto.reason && (
        <div className="ai-cto-card__reason">
          <span className="ai-cto-card__reason-label">Reason</span>
          <span className="ai-cto-card__reason-text">{data.ai_cto.reason}</span>
        </div>
      )}

      {/* Service health dots */}
      <div className="ai-cto-card__services">
        {(['database', 'redis', 'ollama'] as const).map((svc) => {
          const state = data.services[svc] ?? 'down';
          return (
            <div key={svc} className="ai-cto-card__service">
              <span
                className={`ai-cto-card__dot ${state === 'up' ? 'ai-cto-card__dot--up' : state === 'unavailable' ? 'ai-cto-card__dot--unavailable' : 'ai-cto-card__dot--down'}`}
                aria-hidden="true"
              />
              <span className="ai-cto-card__service-name">{svc}</span>
            </div>
          );
        })}
      </div>

      {/* AI inference status */}
      <div className="ai-cto-card__ai-status">
        <span className="ai-cto-card__ai-label">AI:</span>
        <span className={`ai-cto-card__ai-value ai-cto-card__ai-value--${data.ai.status}`}>
          {data.ai.status.toUpperCase()}
        </span>
        {data.ai.message && <span className="ai-cto-card__ai-message">{data.ai.message}</span>}
      </div>
    </div>
  );
}
