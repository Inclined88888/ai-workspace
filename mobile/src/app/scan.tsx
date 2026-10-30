/** Full-screen QR scanner for the pairing code. Paper frame, one red corner mark, no overlays. */
import React, { useRef, useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import { CameraView, useCameraPermissions } from 'expo-camera';
import { router } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useSession } from '@/lib/session';
import { parsePairingUrl } from '@/lib/pairing';
import { fonts, space, type } from '@/lib/theme';
import { error as hError, tap } from '@/lib/haptics';
import { Btn, Label, Screen, T } from '@/components/ui';

export default function Scan() {
  const { colors: c } = useSession();
  const insets = useSafeAreaInsets();
  const [perm, ask] = useCameraPermissions();
  const [hint, setHint] = useState('');
  const done = useRef(false);

  if (!perm) return <Screen>{null}</Screen>;
  if (!perm.granted) {
    return (
      <Screen style={{ padding: space[5], justifyContent: 'center' }}>
        <Text style={{ fontFamily: fonts.display, fontSize: 30, color: c.text }}>Camera, briefly.</Text>
        <T style={{ color: c.muted, marginTop: space[3] }}>The camera is only used to read the pairing code on your computer’s screen.</T>
        <Btn label="Allow camera" kind="ink" style={{ marginTop: space[6] }} onPress={ask} />
        <Btn label="Type the code instead" kind="ghost" style={{ marginTop: space[2] }} onPress={() => router.back()} />
      </Screen>
    );
  }

  return (
    <View style={{ flex: 1, backgroundColor: '#141311' }}>
      <CameraView
        style={{ flex: 1 }}
        facing="back"
        barcodeScannerSettings={{ barcodeTypes: ['qr'] }}
        onBarcodeScanned={({ data }) => {
          if (done.current) return;
          const offer = parsePairingUrl(data);
          if (!offer) {
            setHint('That is not an AI Workspace pairing code.');
            hError();
            return;
          }
          done.current = true;
          tap();
          router.replace({ pathname: '/pair', params: { c: offer.code, u: offer.urls, n: offer.name } });
        }}
      />
      <View pointerEvents="box-none" style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, alignItems: 'center', justifyContent: 'center' }}>
        <View style={{ width: 240, height: 240, borderWidth: 1, borderColor: '#F5F1E8' }}>
          <View style={{ position: 'absolute', top: -1, left: -1, width: 28, height: 2, backgroundColor: '#D21F3C' }} />
          <View style={{ position: 'absolute', top: -1, left: -1, width: 2, height: 28, backgroundColor: '#D21F3C' }} />
        </View>
      </View>
      <View style={{ position: 'absolute', left: 0, right: 0, bottom: 0, backgroundColor: '#F5F1E8', paddingHorizontal: space[5], paddingTop: space[4], paddingBottom: insets.bottom + space[4] }}>
        <Label color="#141311">Pair a phone</Label>
        <Text style={[type.title, { color: '#141311', marginTop: 4 }]}>Point at the code on your computer.</Text>
        {hint ? <T style={{ color: '#A61C1C', marginTop: space[2] }}>{hint}</T> : null}
        <Pressable onPress={() => router.back()} accessibilityRole="button" style={{ marginTop: space[3] }}>
          <Label color="#B5122B">Cancel</Label>
        </Pressable>
      </View>
    </View>
  );
}
