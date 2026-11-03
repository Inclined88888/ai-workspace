/** Companion: one live view of every AI agent on your computer, with approvals and questions you can answer from here. */
import React, { useState, useMemo } from 'react';
import { Pressable, RefreshControl, ScrollView, View, Text as RNText } from 'react-native';
import { CompanionState, CompanionRequest, CompanionAgent } from '@/lib/api';
import { useRemote, setApprovalsCount } from '@/lib/hooks';
import { useSession } from '@/lib/session';
import { confirmWithBiometrics } from '@/lib/guard';
import { relativeTime } from '@/lib/format';
import { fonts, hairline, space, type } from '@/lib/theme';
import { success, warning, tap } from '@/lib/haptics';
import { ConnectionMark, OfflineNotice } from '@/components/Connection';
import { Btn, Empty, FadeIn, Label, Masthead, Notice, Screen, Section, T, Tag } from '@/components/ui';

function toISO(ts: number): string {
  return new Date(ts * 1000).toISOString();
}

function moodEmoji(mood: CompanionState['mood']): string {
  switch (mood) {
    case 'idle': return '😴';
    case 'working': return '🤖';
    case 'waiting': return '🤔';
    case 'done': return '✅';
  }
}

function statusDot(status: CompanionAgent['status'], colors: ReturnType<typeof useSession>['colors']) {
  const map: Record<CompanionAgent['status'], string> = {
    idle: colors.muted,
    working: colors.accent,
    thinking: colors.blueSoft,
    waiting: colors.warn,
    done: colors.ok,
    error: colors.danger,
  };
  return <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: map[status] }} />;
}

export function CompanionRequestCard({
  request,
  onDecided,
}: {
  request: CompanionRequest;
  onDecided?: (r: CompanionRequest) => void;
}) {
  const { colors: c, client } = useSession();
  const [busy, setBusy] = useState<'allow' | 'deny' | 'always' | 'answer' | ''>('');
  const [err, setErr] = useState('');
  const [done, setDone] = useState<CompanionRequest | null>(null);

  const isPermission = request.kind === 'permission';
  const canAlways = isPermission && request.always;

  const decide = async (decision: 'allow' | 'deny' | 'always' | 'answer', answer?: unknown) => {
    if (!client || busy) return;
    if (decision === 'allow' || decision === 'always') {
      const g = await confirmWithBiometrics(`Approve: ${request.title}`);
      if (!g.ok) {
        setErr(g.reason);
        return;
      }
    }
    setBusy(decision);
    setErr('');
    try {
      const r = await client.companionDecide(request.id, decision, answer);
      if (decision === 'allow' || decision === 'always') success();
      else warning();
      setDone(r);
      setTimeout(() => onDecided?.(r), 700);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy('');
    }
  };

  const isPending = request.status === 'pending';

  return (
    <View style={{ paddingVertical: space[4], borderBottomWidth: hairline, borderBottomColor: c.border }}>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
        <Tag label={isPermission ? 'permission' : 'question'} tone={isPermission ? 'warn' : 'live'} />
        <Label style={{ fontSize: 9.5 }}>{relativeTime(toISO(request.created_at))}</Label>
      </View>
      <T style={[type.title, { color: c.text, marginTop: space[2], fontSize: 21 }]}>{request.title}</T>
      {request.detail ? (
        <T style={{ fontFamily: fonts.displayItalic, fontSize: 15, lineHeight: 21, color: c.muted, marginTop: 2 }}>
          {request.detail}
        </T>
      ) : null}
      {request.action && request.target ? (
        <View style={{ marginTop: space[3], backgroundColor: c.panel, borderLeftWidth: 1.5, borderLeftColor: c.blueSoft, paddingVertical: 8, paddingHorizontal: 10, gap: 4 }}>
          <View style={{ flexDirection: 'row', gap: 10 }}>
            <Label style={{ width: 72, fontSize: 9.5, marginTop: 3 }}>{request.action}</Label>
            <T style={{ flex: 1, fontSize: 13.5, lineHeight: 19 }} numberOfLines={4}>{request.target}</T>
          </View>
        </View>
      ) : null}
      {err ? <T style={{ color: c.danger, marginTop: space[2], fontSize: 13 }}>{err}</T> : null}
      {done ? (
        <View style={{ marginTop: space[3], flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          {done.status === 'deny' ? (
            <View style={{ width: 8, height: 8, backgroundColor: c.danger }} />
          ) : (
            <View style={{ width: 8, height: 8, backgroundColor: c.ok, borderRadius: 4 }} />
          )}
          <Label color={c.text}>
            {done.status === 'deny'
              ? 'Denied · logged'
              : done.status === 'always'
              ? 'Always allowed · rule saved'
              : done.status === 'answered'
              ? 'Answered'
              : 'Approved · running on your computer'}
          </Label>
        </View>
      ) : isPending ? (
        <View style={{ flexDirection: 'row', gap: space[2], marginTop: space[3] }}>
          {isPermission ? (
            <>
              <Btn
                label="Approve"
                kind="ink"
                style={{ flex: 1 }}
                busy={busy === 'allow'}
                disabled={!!busy}
                onPress={() => decide('allow')}
              />
              {canAlways ? (
                <Btn
                  label="Always"
                  kind="accent"
                  style={{ flex: 1 }}
                  busy={busy === 'always'}
                  disabled={!!busy}
                  onPress={() => decide('always')}
                />
              ) : (
                <Btn
                  label="Deny"
                  kind="outline"
                  style={{ flex: 1 }}
                  busy={busy === 'deny'}
                  disabled={!!busy}
                  onPress={() => decide('deny')}
                />
              )}
            </>
          ) : (
            <>
              {request.options.length ? (
                request.options.map((opt, i) => (
                  <Btn
                    key={i}
                    label={opt}
                    kind="outline"
                    style={{ flex: 1 }}
                    busy={busy === 'answer'}
                    disabled={!!busy}
                    onPress={() => decide('answer', opt)}
                  />
                ))
              ) : (
                <View style={{ flexDirection: 'row', gap: space[2] }}>
                  <Btn
                    label="Answer"
                    kind="ink"
                    style={{ flex: 1 }}
                    busy={busy === 'answer'}
                    disabled={!!busy}
                    onPress={() => {
                      // For free-text answers, we'd need a modal. For now just use a default.
                      decide('answer', 'yes');
                    }}
                  />
                </View>
              )}
              <Btn
                label="Decline"
                kind="outline"
                style={{ flex: 1 }}
                busy={busy === 'deny'}
                disabled={!!busy}
                onPress={() => decide('deny')}
              />
            </>
          )}
        </View>
      ) : null}
    </View>
  );
}

export default function Companion() {
  const { colors: c } = useSession();
  const [expandedAgents, setExpandedAgents] = useState<Set<string>>(new Set());

  const state = useRemote<CompanionState>(
    async (cl) => {
      const s = await cl.companionState();
      // Count pending requests for the tab badge
      const pending = s.requests.filter((r) => r.status === 'pending').length;
      setApprovalsCount(pending);
      return s;
    },
    2500,
  );

  const s = state.data;
  const pendingRequests = useMemo(
    () => s?.requests.filter((r) => r.status === 'pending') ?? [],
    [s?.requests],
  );
  const agents = useMemo(() => s?.agents ?? [], [s?.agents]);

  const toggleAgent = (key: string) => {
    tap();
    setExpandedAgents((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  return (
    <Screen>
      <ScrollView
        contentContainerStyle={{ paddingBottom: space[12] }}
        refreshControl={
          <RefreshControl refreshing={state.refreshing} onRefresh={state.refresh} tintColor={c.accent} />
        }
      >
        <Masthead
          kicker={s ? `${moodEmoji(s.mood)} ${s.mood.charAt(0).toUpperCase() + s.mood.slice(1)}` : 'Loading…'}
          title="Companion"
          deck="Every agent on your computer, live. Approve or answer what needs you."
          right={<ConnectionMark />}
        />
        <OfflineNotice />
        {state.error && s ? (
          <View style={{ paddingHorizontal: space[5], paddingTop: space[3] }}>
            <Notice text={state.error} />
          </View>
        ) : null}

        {/* Usage line */}
        {s && (
          <FadeIn delay={40} style={{ paddingHorizontal: space[5] }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: space[4], paddingVertical: space[3], borderBottomWidth: hairline, borderBottomColor: c.border }}>
              {s.usage.rooks_month ? (
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                  <Label style={{ fontSize: 9.5 }}>This month</Label>
                  <T style={{ color: c.text, fontSize: 14 }}>{s.usage.rooks_month.cents > 0 ? `$${(s.usage.rooks_month.cents / 100).toFixed(2)}` : 'Free'}</T>
                  {s.usage.rooks_month.tokens && (
                    <Label style={{ fontSize: 9.5 }}>{s.usage.rooks_month.tokens.toLocaleString()} tokens</Label>
                  )}
                </View>
              ) : (
                <Label style={{ fontSize: 9.5, color: c.muted }}>No usage data yet</Label>
              )}
              {s.usage.agents.length > 0 ? (
                <View style={{ flex: 1, flexDirection: 'row', justifyContent: 'flex-end', gap: space[3] }}>
                  {s.usage.agents.slice(0, 3).map((u, i) => (
                    <View key={i} style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
                      <Label style={{ fontSize: 9.5 }}>{u.name}</Label>
                      {typeof u.used === 'number' ? (
                        <T style={{ color: c.text, fontSize: 13 }}>{Math.round(u.used * 100)}%</T>
                      ) : (
                        <Label style={{ fontSize: 9.5 }}>{u.label}</Label>
                      )}
                    </View>
                  ))}
                </View>
              ) : null}
            </View>
          </FadeIn>
        )}

        {/* Agents */}
        {agents.length > 0 ? (
          <FadeIn delay={80} style={{ paddingHorizontal: space[5] }}>
            <Section title={`Agents · ${agents.length}`} />
            {agents.map((a, i) => (
              <FadeIn key={a.key} delay={Math.min(i, 6) * 25}>
                <View style={{ borderBottomWidth: hairline, borderBottomColor: c.border }}>
                  <Pressable
                    onPress={() => toggleAgent(a.key)}
                    style={{ paddingVertical: space[3], flexDirection: 'row', alignItems: 'center', gap: space[3] }}
                  >
                    {statusDot(a.status, c)}
                    <T style={{ color: c.text, fontSize: 15 }}>{a.name}</T>
                    <View style={{ flex: 1 }} />
                    <Label style={{ fontSize: 9.5 }}>{relativeTime(toISO(a.updated_at))}</Label>
                    <View style={{ width: 18, height: 18, alignItems: 'center', justifyContent: 'center' }}>
                      <RNText style={{ color: c.muted, fontSize: 12, transform: [{ rotate: expandedAgents.has(a.key) ? '180deg' : '0deg' }] }}>▼</RNText>
                    </View>
                  </Pressable>
                  {expandedAgents.has(a.key) && (
                    <View style={{ marginLeft: 22, paddingBottom: space[3] }}>
                      {a.steps.slice(-8).map((step, si) => (
                        <View key={si} style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 8, paddingVertical: 2 }}>
                          <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: c.muted, marginTop: 7 }} />
                          <T style={{ color: c.muted, fontSize: 12.5, flex: 1 }} numberOfLines={3}>
                            <T style={{ color: c.faint, fontSize: 11 }}>{step.kind}: </T>
                            {step.text}
                          </T>
                          <Label style={{ fontSize: 9.5, marginTop: 2 }}>{relativeTime(toISO(step.t))}</Label>
                        </View>
                      ))}
                    </View>
                  )}
                </View>
              </FadeIn>
            ))}
          </FadeIn>
        ) : (
          <FadeIn delay={80} style={{ paddingHorizontal: space[5] }}>
            <Section title="Agents" />
            <Empty title="No agents reporting." body="Start a coding agent, a chat, or a scheduled job on your computer — it will appear here live." />
          </FadeIn>
        )}

        {/* Requests */}
        {pendingRequests.length > 0 ? (
          <FadeIn delay={120} style={{ paddingHorizontal: space[5] }}>
            <Section title={`Waiting for you · ${pendingRequests.length}`} />
            {pendingRequests.map((r, i) => (
              <FadeIn key={r.id} delay={Math.min(i, 6) * 30}>
                <CompanionRequestCard request={r} onDecided={() => state.reload()} />
              </FadeIn>
            ))}
          </FadeIn>
        ) : s ? (
          <FadeIn delay={120} style={{ paddingHorizontal: space[5] }}>
            <Section title="Waiting for you" />
            <Empty title="Nothing needs you right now." body="When an agent wants permission or asks a question, it appears here and on your lock screen." />
          </FadeIn>
        ) : null}

        {/* Always rules */}
        {s && s.rules.length > 0 && (
          <FadeIn delay={160} style={{ paddingHorizontal: space[5] }}>
            <Section title={`Always rules · ${s.rules.length}`} />
            {s.rules.map((rule, i) => (
              <View key={i} style={{ paddingVertical: space[3], borderBottomWidth: hairline, borderBottomColor: c.border }}>
                <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
                  <T style={{ color: c.text, fontSize: 14 }}>{rule.tool} · {rule.action}</T>
                  <Label style={{ fontSize: 9.5 }}>{rule.pattern}</Label>
                </View>
              </View>
            ))}
          </FadeIn>
        )}
      </ScrollView>
    </Screen>
  );
}