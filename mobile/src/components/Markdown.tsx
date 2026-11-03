import React, { memo, useMemo, useState } from 'react';
import { Linking, Pressable, ScrollView, Text, View } from 'react-native';
import * as Clipboard from 'expo-clipboard';
import { Block, Inline, parseMarkdown } from '@/lib/markdown';
import { useColors } from '@/lib/session';
import { fonts, space, type } from '@/lib/theme';
import { tap } from '@/lib/haptics';
import { Label } from './ui';

function Inlines({ inl, base }: { inl: Inline[]; base: object }) {
  const c = useColors();
  return (
    <Text style={base}>
      {inl.map((n, i) => {
        switch (n.t) {
          case 'strong':
            return <Text key={i} style={{ fontFamily: fonts.uiSemi }}>{n.v}</Text>;
          case 'em':
            return <Text key={i} style={{ fontFamily: fonts.displayItalic, fontSize: 16.5 }}>{n.v}</Text>;
          case 'code':
            return <Text key={i} style={{ fontFamily: fonts.mono, fontSize: 13, backgroundColor: c.codeBg }}>{` ${n.v} `}</Text>;
          case 'link':
            return (
              <Text key={i} style={{ color: c.accentText, textDecorationLine: 'underline' }} onPress={() => Linking.openURL(n.href)}>
                {n.v}
              </Text>
            );
          default:
            return <Text key={i}>{n.v}</Text>;
        }
      })}
    </Text>
  );
}

export function CodeBlock({ code, lang, open }: { code: string; lang: string; open?: boolean }) {
  const c = useColors();
  const [copied, setCopied] = useState(false);
  return (
    <View style={{ borderWidth: 0.75, borderColor: c.border, backgroundColor: c.codeBg, marginVertical: space[2] }}>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingHorizontal: 10, paddingVertical: 6, borderBottomWidth: 0.75, borderBottomColor: c.border }}>
        <Label style={{ fontSize: 9.5 }}>{lang || 'code'}{open ? ' · writing' : ''}</Label>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Copy code"
          hitSlop={10}
          onPress={async () => {
            await Clipboard.setStringAsync(code);
            tap();
            setCopied(true);
            setTimeout(() => setCopied(false), 1400);
          }}
        >
          <Label style={{ fontSize: 9.5 }} color={copied ? c.ok : c.accentText}>{copied ? 'Copied' : 'Copy'}</Label>
        </Pressable>
      </View>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ padding: 10 }}>
        <Text selectable style={{ fontFamily: fonts.mono, fontSize: 12.5, lineHeight: 18, color: c.text }}>{code}</Text>
      </ScrollView>
    </View>
  );
}

function BlockView({ b }: { b: Block }) {
  const c = useColors();
  const body = [type.body, { color: c.text }];
  switch (b.t) {
    case 'h':
      return (
        <Inlines
          inl={b.inl}
          base={{ fontFamily: fonts.display, color: c.text, fontSize: b.level === 1 ? 22 : b.level === 2 ? 19 : 17, lineHeight: b.level === 1 ? 28 : 24, marginTop: space[3], marginBottom: space[1] }}
        />
      );
    case 'li':
      return (
        <View style={{ flexDirection: 'row', paddingLeft: b.depth * 14, marginVertical: 2 }}>
          <Text style={[type.body, { color: b.ordered ? c.muted : c.accent, width: 22, fontFamily: b.ordered ? fonts.uiMedium : fonts.ui }]}>
            {b.ordered ? `${b.n}.` : '—'}
          </Text>
          <View style={{ flex: 1 }}>
            <Inlines inl={b.inl} base={body} />
          </View>
        </View>
      );
    case 'quote':
      return (
        <View style={{ borderLeftWidth: 1.5, borderLeftColor: c.accent, paddingLeft: 12, marginVertical: space[1] }}>
          <Inlines inl={b.inl} base={{ fontFamily: fonts.displayItalic, fontSize: 17, lineHeight: 24, color: c.text }} />
        </View>
      );
    case 'code':
      return <CodeBlock code={b.code} lang={b.lang} open={b.open} />;
    case 'hr':
      return <View style={{ height: 0.75, backgroundColor: c.border, marginVertical: space[3] }} />;
    default:
      return <Inlines inl={b.inl} base={[...body, { marginVertical: 4 }]} />;
  }
}

export const Markdown = memo(function Markdown({ text }: { text: string }) {
  const blocks = useMemo(() => parseMarkdown(text), [text]);
  return (
    <View>
      {blocks.map((b, i) => (
        <BlockView key={i} b={b} />
      ))}
    </View>
  );
});
