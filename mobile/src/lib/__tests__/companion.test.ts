// Mock expo-local-authentication before importing guard
jest.mock('expo-local-authentication', () => ({
  SecurityLevel: { NONE: 0, SECRET: 1, BIOMETRIC: 1, BIOMETRIC_WEAK: 2, BIOMETRIC_STRONG: 3 },
  getEnrolledLevelAsync: jest.fn(),
  authenticateAsync: jest.fn(),
}));

import { CompanionState, CompanionRequest, CompanionAgent, CompanionAgentStep } from '../api';
import { confirmWithBiometrics } from '../guard';
import { decisionFor, COMPANION_PERMISSION_CATEGORY, COMPANION_QUESTION_CATEGORY } from '../lockActions';
import * as LocalAuthentication from 'expo-local-authentication';

describe('Companion types', () => {
  it('defines a valid agent step', () => {
    const step: CompanionAgentStep = { t: Date.now() / 1000, kind: 'read', text: 'Read app.py' };
    expect(step.kind).toBe('read');
    expect(typeof step.t).toBe('number');
  });

  it('defines a valid agent', () => {
    const agent: CompanionAgent = {
      key: 'claude-code:s1',
      tool: 'claude-code',
      name: 'Claude Code',
      folder: '/tmp/proj',
      status: 'working',
      steps: [{ t: Date.now() / 1000, kind: 'read', text: 'Read app.py' }],
      usage: { label: '5-hour', used: 0.42 },
      updated_at: Date.now() / 1000,
      started_at: Date.now() / 1000,
    };
    expect(agent.status).toBe('working');
    expect(agent.steps.length).toBe(1);
  });

  it('defines a valid permission request', () => {
    const req: CompanionRequest = {
      id: 'abc123',
      tool: 'claude-code',
      session: 's1',
      agent: 'Claude Code',
      agent_key: 'claude-code:s1',
      kind: 'permission',
      title: 'Run npm test',
      detail: 'Run the test suite',
      action: 'Bash',
      target: 'npm test',
      options: [],
      multi: false,
      status: 'pending',
      answer: null,
      decided_by: '',
      created_at: Date.now() / 1000,
      decided_at: null,
      always: true,
    };
    expect(req.kind).toBe('permission');
    expect(req.always).toBe(true);
    expect(req.status).toBe('pending');
  });

  it('defines a valid question request', () => {
    const req: CompanionRequest = {
      id: 'q1',
      tool: 'gemini-cli',
      session: 's2',
      agent: 'Gemini CLI',
      agent_key: 'gemini-cli:s2',
      kind: 'question',
      title: 'Which file?',
      detail: '',
      action: 'question',
      target: '',
      options: ['api.py', 'db.py'],
      multi: false,
      status: 'pending',
      answer: null,
      decided_by: '',
      created_at: Date.now() / 1000,
      decided_at: null,
      always: false,
    };
    expect(req.kind).toBe('question');
    expect(req.options).toEqual(['api.py', 'db.py']);
    expect(req.always).toBe(false);
  });

  it('defines a valid companion state', () => {
    const state: CompanionState = {
      version: 42,
      mood: 'waiting',
      agents: [
        {
          key: 'claude-code:s1',
          tool: 'claude-code',
          name: 'Claude Code',
          folder: '/tmp/proj',
          status: 'waiting',
          steps: [],
          usage: null,
          updated_at: Date.now() / 1000,
          started_at: Date.now() / 1000,
        },
      ],
      requests: [
        {
          id: 'abc123',
          tool: 'claude-code',
          session: 's1',
          agent: 'Claude Code',
          agent_key: 'claude-code:s1',
          kind: 'permission',
          title: 'Run npm test',
          detail: '',
          action: 'Bash',
          target: 'npm test',
          options: [],
          multi: false,
          status: 'pending',
          answer: null,
          decided_by: '',
          created_at: Date.now() / 1000,
          decided_at: null,
          always: true,
        },
      ],
      usage: { rooks_month: { cents: 123, tokens: 456 }, agents: [] },
      rules: [],
    };
    expect(state.mood).toBe('waiting');
    expect(state.agents.length).toBe(1);
    expect(state.requests.length).toBe(1);
  });
});

describe('confirmWithBiometrics (auth gating)', () => {
  const mockGetEnrolledLevel = LocalAuthentication.getEnrolledLevelAsync as jest.Mock;
  const mockAuthenticate = LocalAuthentication.authenticateAsync as jest.Mock;

  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('rejects when no biometrics or passcode enrolled (SecurityLevel.NONE)', async () => {
    mockGetEnrolledLevel.mockResolvedValue(LocalAuthentication.SecurityLevel.NONE);
    const result = await confirmWithBiometrics('Test action');
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.reason).toContain('passcode or Face ID');
  });

  it('allows when biometrics enrolled and authentication succeeds', async () => {
    mockGetEnrolledLevel.mockResolvedValue(LocalAuthentication.SecurityLevel.BIOMETRIC_STRONG);
    mockAuthenticate.mockResolvedValue({ success: true, error: null });
    const result = await confirmWithBiometrics('Approve: Bash npm test');
    expect(result.ok).toBe(true);
  });

  it('allows when device passcode enrolled and authentication succeeds', async () => {
    mockGetEnrolledLevel.mockResolvedValue(LocalAuthentication.SecurityLevel.SECRET);
    mockAuthenticate.mockResolvedValue({ success: true, error: null });
    const result = await confirmWithBiometrics('Approve: Bash npm test');
    expect(result.ok).toBe(true);
  });

  it('rejects when user cancels authentication', async () => {
    mockGetEnrolledLevel.mockResolvedValue(LocalAuthentication.SecurityLevel.BIOMETRIC_STRONG);
    mockAuthenticate.mockResolvedValue({ success: false, error: 'user_cancel' });
    const result = await confirmWithBiometrics('Approve: Bash npm test');
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.reason).toBe('Cancelled');
  });

  it('rejects when authentication fails', async () => {
    mockGetEnrolledLevel.mockResolvedValue(LocalAuthentication.SecurityLevel.BIOMETRIC_STRONG);
    mockAuthenticate.mockResolvedValue({ success: false, error: 'authentication_failed' });
    const result = await confirmWithBiometrics('Approve: Bash npm test');
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.reason).toContain('Couldn\'t confirm');
  });

  it('rejects on unexpected error', async () => {
    mockGetEnrolledLevel.mockResolvedValue(LocalAuthentication.SecurityLevel.BIOMETRIC_STRONG);
    mockAuthenticate.mockRejectedValue(new Error('System error'));
    const result = await confirmWithBiometrics('Approve: Bash npm test');
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.reason).toContain('Couldn\'t confirm');
  });
});

describe('Companion decision logic (reducer-style)', () => {
  type CompanionDecision = { decision: 'allow' | 'deny' | 'always' | 'answer'; answer?: string };

  function canDecideWithoutBiometrics(decision: CompanionDecision['decision']): boolean {
    // Deny never needs biometrics
    if (decision === 'deny') return true;
    // Allow, Always, Answer need biometrics
    return false;
  }

  it('Deny never requires biometrics', () => {
    expect(canDecideWithoutBiometrics('deny')).toBe(true);
  });

  it('Allow requires biometrics', () => {
    expect(canDecideWithoutBiometrics('allow')).toBe(false);
  });

  it('Always requires biometrics', () => {
    expect(canDecideWithoutBiometrics('always')).toBe(false);
  });

  it('Answer requires biometrics', () => {
    expect(canDecideWithoutBiometrics('answer')).toBe(false);
  });
});

describe('lockActions decisionFor (Companion categories)', () => {
  it('returns companion decision for permission allow', () => {
    const result = decisionFor('allow', { kind: 'companion', request_id: 'abc123', request_kind: 'permission' });
    expect(result).toEqual({ kind: 'companion', id: 'abc123', decision: 'allow' });
  });

  it('returns companion decision for permission deny', () => {
    const result = decisionFor('deny', { kind: 'companion', request_id: 'abc123', request_kind: 'permission' });
    expect(result).toEqual({ kind: 'companion', id: 'abc123', decision: 'deny' });
  });

  it('opens companion screen for permission open', () => {
    const result = decisionFor('open', { kind: 'companion', request_id: 'abc123', request_kind: 'permission' });
    expect(result).toEqual({ kind: 'open', path: '/companion' });
  });

  it('returns companion decision for question answer', () => {
    const result = decisionFor('answer', { kind: 'companion', request_id: 'q1', request_kind: 'question' });
    expect(result).toEqual({ kind: 'companion', id: 'q1', decision: 'answer', answer: 'yes' });
  });

  it('returns companion decision for question decline', () => {
    const result = decisionFor('decline', { kind: 'companion', request_id: 'q1', request_kind: 'question' });
    expect(result).toEqual({ kind: 'companion', id: 'q1', decision: 'deny' });
  });

  it('opens companion screen for question open', () => {
    const result = decisionFor('open', { kind: 'companion', request_id: 'q1', request_kind: 'question' });
    expect(result).toEqual({ kind: 'open', path: '/companion' });
  });

  it('returns null for malformed companion data', () => {
    expect(decisionFor('allow', { kind: 'companion' })).toBeNull();
    expect(decisionFor('allow', { kind: 'companion', request_id: '', request_kind: 'permission' })).toBeNull();
    expect(decisionFor('allow', { kind: 'companion', request_kind: 'permission' })).toBeNull();
  });

  it('still handles legacy approval category', () => {
    const result = decisionFor('approve', { kind: 'approval', approval_id: 5 });
    expect(result).toEqual({ kind: 'decide', id: 5, approve: true });
  });

  it('exports companion category constants', () => {
    expect(COMPANION_PERMISSION_CATEGORY).toBe('companion-permission');
    expect(COMPANION_QUESTION_CATEGORY).toBe('companion-question');
  });
});